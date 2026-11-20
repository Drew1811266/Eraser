"""Command line entry point.

Two reasons this exists before the UI: it makes the engine usable while the
shell is still being built, and it is how a user (or a bug report) can prove the
pipeline works on a specific file without Electron in the way.

    python -m engine.cli info
    python -m engine.cli probe "clip.mp4"
    python -m engine.cli process "clip.mp4" --rect 480,20,120,60
    python -m engine.cli process "clip.mp4" --rect 480,20,120,60 \
        --frames 0:239 --output "D:/out/clip.mp4"

Exit codes: 0 success, 1 failure, 2 bad usage.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from engine import __version__
from engine.pipeline.logging import logger


def _rect(value: str) -> dict[str, int]:
    parts = [item.strip() for item in value.replace("x", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("rect must be x,y,width,height")
    try:
        x, y, width, height = (int(item) for item in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("rect must be four integers") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("rect width and height must be positive")
    return {"x": x, "y": y, "width": width, "height": height}


def _frames(value: str) -> tuple[int, int]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("frames must be start:end")
    start_text, end_text = value.split(":", 1)
    try:
        start, end = int(start_text), int(end_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("frames must be two integers") from exc
    if end < start:
        raise argparse.ArgumentTypeError("frames end must not precede start")
    return start, end


class ProgressLine:
    """One-line progress bar that rewrites itself instead of scrolling."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.started = time.monotonic()
        self._last_length = 0

    def update(self, payload: Any) -> None:
        if not self.enabled:
            return
        if isinstance(payload, dict):
            fraction = float(payload.get("progress") or 0.0)
            message = payload.get("message") or payload.get("phase") or ""
            processed = payload.get("processed")
            total = payload.get("total")
        elif isinstance(payload, (int, float)):
            fraction, message, processed, total = float(payload), "", None, None
        else:
            return

        elapsed = time.monotonic() - self.started
        eta = ""
        if 0 < fraction < 1:
            remaining = elapsed / fraction - elapsed
            eta = f"  eta {int(remaining) // 60:02d}:{int(remaining) % 60:02d}"
        counter = f"  {processed}/{total}" if processed is not None and total else ""
        bar_width = 24
        filled = int(round(fraction * bar_width))
        bar = "#" * filled + "-" * (bar_width - filled)
        line = f"  [{bar}] {fraction * 100:5.1f}%{counter}{eta}  {str(message)[:40]}"
        padding = " " * max(0, self._last_length - len(line))
        self._last_length = len(line)
        sys.stdout.write("\r" + line + padding)
        sys.stdout.flush()

    def finish(self) -> None:
        if self.enabled and self._last_length:
            sys.stdout.write("\n")
            sys.stdout.flush()


def command_info(args: argparse.Namespace) -> int:
    from engine.platform import ffmpeg as ffmpeg_tools
    from engine.platform import ort_runtime
    from engine.platform.device import detect_devices
    from engine.platform.paths import default_output_dir, models_dir

    ffmpeg = ffmpeg_tools.resolve("ffmpeg")
    ffprobe = ffmpeg_tools.resolve("ffprobe")
    print(f"engine      {__version__}")
    print(f"python      {sys.version.split()[0]}")
    print(f"models dir  {models_dir()}")
    print(f"output dir  {default_output_dir()}")
    print(f"runtime     {ort_runtime.summary()}")
    print(f"ffmpeg      {ffmpeg.path or 'not found'}  ({ffmpeg.source or '-'})")
    print(f"ffprobe     {ffprobe.path or 'not found'}  ({ffprobe.source or '-'})")
    print("devices:")
    for device in detect_devices():
        available = device.get("available")
        suffix = "" if available is None else ("  (usable)" if available else "  (detected, not usable)")
        memory = f"  {device['memory_mb']:.0f} MB" if "memory_mb" in device else ""
        print(f"  {device['kind']:<5} {device['name']}{memory}{suffix}")
    return 0


def command_probe(args: argparse.Namespace) -> int:
    from engine.pipeline.watermark import VideoProcessor

    path = Path(args.input)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    info = VideoProcessor(remover=None).get_video_info(str(path))
    for key in ("width", "height", "fps", "frame_count", "duration", "has_audio", "codec"):
        value = getattr(info, key, None)
        if value is None and isinstance(info, dict):
            value = info.get(key)
        print(f"{key:<12} {value}")
    return 0


def command_process(args: argparse.Namespace) -> int:
    from engine.pipeline.remover import ModelNotFound
    from engine.pipeline.watermark import VideoProcessor
    from engine.runner import output_path_for, run_job

    path = Path(args.input)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    probe = VideoProcessor(remover=None).get_video_info(str(path))
    frame_count = int(getattr(probe, "frame_count", 0) or 0)
    if frame_count <= 0:
        print("could not read the frame count; is this a video?", file=sys.stderr)
        return 2

    start, end = args.frames if args.frames else (0, frame_count - 1)
    end = min(end, frame_count - 1)
    if start >= frame_count:
        print(f"start frame {start} is past the end ({frame_count} frames)", file=sys.stderr)
        return 2

    segment = {
        "start_frame": start,
        "end_frame": end,
        "rect": dict(args.rect),
        "expand_px": args.expand,
        "feather_px": args.feather,
        "enabled": True,
    }

    output = Path(args.output) if args.output else output_path_for(path, args.output_dir)

    progress = ProgressLine(enabled=not args.quiet and sys.stdout.isatty())
    print(f"input      {path}")
    print(f"output     {output}")
    print(f"frames     {start}..{end} of {frame_count}")
    print(f"rect       x={args.rect['x']} y={args.rect['y']} "
          f"w={args.rect['width']} h={args.rect['height']}")
    print(f"model      {args.model}")

    started = time.monotonic()
    try:
        result = run_job(
            path,
            output,
            [segment],
            args.model,
            progress=progress.update,
            status=(None if args.quiet else progress.update),
        )
    except ModelNotFound as exc:
        progress.finish()
        print(f"model missing: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        progress.finish()
        print(f"processing failed: {exc}", file=sys.stderr)
        if args.traceback:
            raise
        return 1
    finally:
        progress.finish()

    elapsed = time.monotonic() - started
    frames = end - start + 1
    print(f"done       {result['output_path']}")
    print(f"elapsed    {elapsed:.1f}s ({elapsed / frames * 1000:.0f} ms/frame)")
    if result.get("model_warning"):
        print(f"warning    {result['model_warning']}")
    return 0


def command_upscale(args: argparse.Namespace) -> int:
    from engine.pipeline.upscale_job import UpscaleError, upscale_video
    from engine.pipeline.upscaler import UpscaleModelNotFound

    path = Path(args.input)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    output = Path(args.output) if args.output else path.with_name(f"{path.stem}_x{args.scale}.mp4")
    progress = ProgressLine(enabled=not args.quiet and sys.stdout.isatty())
    print(f"input      {path}")
    print(f"output     {output}")
    print(f"scale      {args.scale}x")

    started = time.monotonic()
    try:
        result = upscale_video(
            path,
            output,
            scale=args.scale,
            progress=lambda payload: progress.update(payload),
        )
    except UpscaleModelNotFound as exc:
        print(f"model missing: {exc}", file=sys.stderr)
        return 1
    except UpscaleError as exc:
        print(f"upscale failed: {exc}", file=sys.stderr)
        return 1
    finally:
        progress.finish()

    elapsed = time.monotonic() - started
    print(f"done       {result['output_path']}  ({result['width']}x{result['height']})")
    print(f"elapsed    {elapsed:.1f}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eraser-engine",
        description="Eraser engine: probe videos and remove a watermarked region.",
    )
    parser.add_argument("--version", action="version", version=f"eraser-engine {__version__}")
    parser.add_argument("--verbose", action="store_true", help="log engine internals to stderr")
    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser("info", help="report versions, assets and devices")
    info.set_defaults(handler=command_info)

    probe = subparsers.add_parser("probe", help="print a video's properties")
    probe.add_argument("input")
    probe.set_defaults(handler=command_probe)

    process = subparsers.add_parser("process", help="remove a watermarked region")
    process.add_argument("input")
    process.add_argument("--rect", type=_rect, required=True,
                         help="watermark rectangle as x,y,width,height")
    process.add_argument("--frames", type=_frames, default=None,
                         help="frame range start:end (default: the whole video)")
    process.add_argument("--output", default=None, help="output file path")
    process.add_argument("--output-dir", default=None, help="output directory")
    process.add_argument("--model", default="lama_roi", help="model id (default: lama_roi)")
    process.add_argument("--expand", type=int, default=5, help="mask expansion in pixels")
    process.add_argument("--feather", type=int, default=3, help="mask feather in pixels")
    process.add_argument("--quiet", action="store_true", help="no progress output")
    process.add_argument("--traceback", action="store_true", help="show the traceback on failure")
    process.set_defaults(handler=command_process)

    upscale = subparsers.add_parser("upscale", help="enlarge a video 2x or 4x (Real-ESRGAN)")
    upscale.add_argument("input")
    upscale.add_argument("--scale", type=int, choices=(2, 4), default=4)
    upscale.add_argument("--output", default=None, help="output file path")
    upscale.add_argument("--quiet", action="store_true")
    upscale.set_defaults(handler=command_upscale)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.set_level("debug" if args.verbose else "warning")
    return int(args.handler(args))


if __name__ == "__main__":
    sys.exit(main())
