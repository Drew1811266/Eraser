"""Run an upscale: segment the video, upscale each piece, join, finalise.

The pipeline is deliberately linear and file-based:

    source ─► [segment i] ─► 4x model ─► resize ─► part_i.mp4
                                                      │
                                     concat ◄─────────┘
                                        │
                                        ├─► resize to the planned resolution
                                        └─► mux the original audio

Two things are worth knowing about the shape:

* the model runs at its native 4x every time. Every other size (2x, 1080p, "same
  resolution") is produced by resampling that result with lanczos, which is both
  cheaper and more faithful than asking a 4x network for a fake scale;
* each scene segment is finished on its own and joined with a stream copy, so a
  long video never has to be held in memory and a segment can be cancelled
  without losing the work already done.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from engine.pipeline.logging import logger
from engine.pipeline.upscale_plan import (
    NATIVE_SCALE,
    UpscaleConfig,
    UpscalePlan,
    plan_output_name,
    resolve_plan,
)
from engine.platform.ffmpeg import resolve

ProgressCallback = Callable[[dict[str, Any]], None]
CancelEvent = Any

X264_ARGS = [
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
]

AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k"]


class UpscaleError(RuntimeError):
    """The upscale could not be completed."""


class _Cancelled(RuntimeError):
    """Raised inside the worker when the task's cancel event is set."""


def _ffmpeg() -> str:
    found = resolve("ffmpeg")
    if not found.path:
        raise UpscaleError("ffmpeg not found")
    return found.path


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _probe(path: str | Path) -> tuple[int, int, float, bool, int, float]:
    from engine.pipeline.watermark import VideoProcessor

    info = VideoProcessor(remover=None).get_video_info(str(path))
    return (
        int(info.width),
        int(info.height),
        float(info.fps),
        bool(info.has_audio),
        int(info.frame_count),
        float(getattr(info, "duration", 0.0) or 0.0),
    )


def _emit(progress: ProgressCallback | None, payload: dict[str, Any]) -> None:
    if progress is None:
        return
    try:
        progress(payload)
    except Exception as exc:  # noqa: BLE001 - progress must never break a run
        logger.warning("upscale progress callback failed: %s", exc)


def _denoise_filter(strength: float) -> list[str]:
    """Map 0..1 onto hqdn3d's strengths.

    Kept as a filter rather than a numpy pass: it has to happen before the model
    sees the frame, and doing it in the decoder is free.
    """
    if strength <= 0:
        return []
    luma = round(1.0 + strength * 5.0, 2)
    chroma = round(1.0 + strength * 4.0, 2)
    temporal = round(1.0 + strength * 8.0, 2)
    # ffmpeg spells the temporal terms `luma_tmp` / `chroma_tmp`. Unknown names
    # are not ignored: the filter fails to configure and the decoder emits
    # nothing at all, which is how this was found the first time.
    return ["hqdn3d=luma_spatial=%s:chroma_spatial=%s:luma_tmp=%s:chroma_tmp=%s"
            % (luma, chroma, temporal, temporal)]


def upscale_segment(
    engine: Any,
    source: str | Path,
    destination: str | Path,
    *,
    start: float = 0.0,
    length: float | None = None,
    denoise: float = 0.0,
    cancel: CancelEvent = None,
    on_frame: Callable[[int], None] | None = None,
) -> int:
    """Upscale one time window to the model's native size. Returns frames written."""
    from engine.pipeline.upscaler import SCALE

    ffmpeg = _ffmpeg()
    source = Path(source)
    destination = Path(destination)
    width, height, fps, _has_audio, frames, _duration = _probe(source)

    decode_command = [ffmpeg, "-v", "error"]
    if start > 0:
        # Seek before -i: the decoder jumps and only the window is decoded.
        decode_command += ["-ss", f"{start:.6f}"]
    if length is not None and length > 0:
        decode_command += ["-t", f"{length:.6f}"]
    decode_command += ["-i", str(source)]

    filters = _denoise_filter(denoise)
    if filters:
        decode_command += ["-vf", ",".join(filters)]
    decode_command += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    decode = subprocess.Popen(
        decode_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_no_window(),
    )

    encode = subprocess.Popen(
        [
            ffmpeg, "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width * SCALE}x{height * SCALE}", "-r", f"{fps:.6f}",
            "-i", "-",
            *X264_ARGS, str(destination),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_no_window(),
    )

    frame_bytes = width * height * 3
    written = 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            buffer = decode.stdout.read(frame_bytes) if decode.stdout else b""
            if len(buffer) < frame_bytes:
                break
            frame = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3)
            enlarged = engine.upscale(frame, cancel=cancel)
            assert encode.stdin is not None
            encode.stdin.write(enlarged.tobytes())
            written += 1
            if on_frame is not None:
                on_frame(written)

        if written == 0:
            detail = (decode.stderr.read() if decode.stderr else b"").decode("utf-8", "replace")
            raise UpscaleError(f"the decoder produced no frames: {detail[-300:]}")

        if encode.stdin is not None:
            encode.stdin.close()
        code = encode.wait()
        if code != 0:
            detail = (encode.stderr.read() if encode.stderr else b"").decode("utf-8", "replace")
            raise UpscaleError(f"encoder failed ({code}): {detail[-500:]}")
    finally:
        for process in (encode, decode):
            if process.poll() is None:
                process.kill()
        for process in (decode, encode):
            if process.stderr is not None:
                try:
                    process.stderr.close()
                except Exception:  # noqa: BLE001
                    pass
    return written


def _resize_and_audio(
    video: Path,
    source: Path,
    destination: Path,
    *,
    width: int,
    height: int,
    has_audio: bool,
    keep_audio: bool,
) -> None:
    """Produce the final file: resample to the target size, carry the audio over."""
    ffmpeg = _ffmpeg()
    probe_width, probe_height, _fps, _audio, _frames, _duration = _probe(video)

    needs_resize = (probe_width, probe_height) != (width, height)
    with_audio = has_audio and keep_audio

    command = [ffmpeg, "-y", "-v", "error", "-i", str(video)]
    if with_audio:
        command += ["-i", str(source)]
    if needs_resize:
        command += ["-vf", f"scale={width}:{height}:flags=lanczos"]
    else:
        command += ["-c:v", "copy"]
    if needs_resize:
        command += X264_ARGS
    if with_audio:
        command += ["-map", "0:v:0", "-map", "1:a:0", *AUDIO_ARGS, "-shortest"]
    command += [str(destination)]

    completed = subprocess.run(command, capture_output=True, timeout=3600,
                               check=False, creationflags=_no_window())
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace")[-500:]
        raise UpscaleError(f"finalise failed ({completed.returncode}): {detail}")


def _concat(parts: list[Path], destination: Path, scratch: Path) -> None:
    """Join finished segments without re-encoding."""
    if len(parts) == 1:
        parts[0].replace(destination)
        return

    listing = scratch / "parts.txt"
    listing.write_text("".join(f"file '{part.as_posix()}'\n" for part in parts), encoding="utf-8")
    completed = subprocess.run(
        [_ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", str(destination)],
        capture_output=True, timeout=3600, check=False, creationflags=_no_window(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace")[-500:]
        raise UpscaleError(f"concat failed ({completed.returncode}): {detail}")


def upscale_with_config(
    input_path: str | Path,
    output_path: str | Path | None,
    config: UpscaleConfig,
    *,
    segments: Iterable[tuple[float, float]] | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent = None,
    upscaler: Any = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Upscale according to *config*, optionally in scene segments."""
    from engine.pipeline.upscaler import RealEsrganUpscaler

    source = Path(input_path)
    if not source.is_file():
        raise UpscaleError(f"input not found: {source}")

    width, height, fps, has_audio, frames, duration = _probe(source)
    if frames <= 0 or width <= 0 or height <= 0:
        raise UpscaleError("could not read the source video")

    plan = resolve_plan(config, width, height)
    if output_path is None:
        directory = Path(output_dir) if output_dir else source.parent
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / plan_output_name(source, plan)
    else:
        target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Prefer frame_count / fps over the container's duration: for a concatenated
    # or VFR file the header's number is short by a frame or two, and `-t` drops
    # exactly those.
    exact_duration = frames / fps if fps > 0 else duration
    span = exact_duration if exact_duration > 0 else duration
    windows = list(segments) if segments else [(0.0, span)]
    total = len(windows)

    engine = upscaler or RealEsrganUpscaler()
    engine.load_model()

    _emit(progress, {
        "phase": "prepare", "progress": 0.0, "processed": 0, "total": frames,
        "message": plan.describe(), "segment_index": 0, "segment_total": total,
    })

    with tempfile.TemporaryDirectory(prefix="eraser-upscale-") as scratch_name:
        scratch = Path(scratch_name)
        parts: list[Path] = []
        done_frames = 0

        for index, (start, length) in enumerate(windows, start=1):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            part = scratch / f"part{index:03d}.mp4"

            def on_frame(written: int, index: int = index) -> None:
                overall = done_frames + written
                _emit(progress, {
                    "phase": "upscale",
                    # Leave the last 4% for join + finalise, which are real steps.
                    "progress": min(0.96, overall / max(1, frames) * 0.96),
                    "processed": overall,
                    "total": frames,
                    "message": f"超分中（{index}/{total}）",
                    "segment_index": index,
                    "segment_total": total,
                })

            written = upscale_segment(
                engine, source, part,
                start=start, length=length,
                denoise=config.denoise_strength,
                cancel=cancel, on_frame=on_frame,
            )
            done_frames += written
            parts.append(part)

        if not parts:
            raise UpscaleError("nothing was upscaled")

        _emit(progress, {
            "phase": "compose", "progress": 0.97, "processed": frames, "total": frames,
            "message": "合并分段", "segment_index": total, "segment_total": total,
        })

        joined = scratch / "joined.mp4"
        _concat(parts, joined, scratch)

        _emit(progress, {
            "phase": "finalize", "progress": 0.98, "processed": frames, "total": frames,
            "message": "输出最终分辨率" if plan.downscale else "写出文件",
            "segment_index": total, "segment_total": total,
        })
        _resize_and_audio(
            joined, source, target,
            width=plan.target_width, height=plan.target_height,
            has_audio=has_audio, keep_audio=config.keep_audio,
        )

    _emit(progress, {
        "phase": "finalize", "progress": 1.0, "processed": frames, "total": frames,
        "message": "完成", "segment_index": total, "segment_total": total,
    })

    return {
        "output_path": str(target),
        "width": plan.target_width,
        "height": plan.target_height,
        "scale": plan.scale,
        "mode": config.mode,
        "scene_split": config.scene_split,
        "segments": total,
        "frames": frames,
        "same_resolution": plan.same_resolution,
    }


def upscale_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    scale: int = 4,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent = None,
    upscaler: Any = None,
) -> dict[str, Any]:
    """Single-shot upscale by a factor of 2 or 4.

    Kept as the simple entry point for the CLI and the older tests; the richer
    behaviour lives in :func:`upscale_with_config`.
    """
    from engine.pipeline.upscaler import SCALE, RealEsrganUpscaler

    if scale not in (2, 4):
        raise UpscaleError(f"unsupported scale {scale}; expected 2 or 4")

    source = Path(input_path)
    target = Path(output_path)
    if not source.is_file():
        raise UpscaleError(f"input not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)

    width, height, fps, has_audio, frames, _duration = _probe(source)
    if frames <= 0 or width <= 0 or height <= 0:
        raise UpscaleError("could not read the source video")

    out_width, out_height = width * scale, height * scale
    engine = upscaler or RealEsrganUpscaler()
    engine.load_model()

    _emit(progress, {"phase": "prepare", "progress": 0.0, "processed": 0, "total": frames,
                     "message": f"放大到 {out_width}x{out_height}"})

    with tempfile.TemporaryDirectory(prefix="eraser-upscale-") as scratch_name:
        scratch = Path(scratch_name)
        silent = scratch / "video.mp4"

        def on_frame(written: int) -> None:
            if written % 5 == 0 or written == frames:
                _emit(progress, {
                    "phase": "upscale",
                    "progress": min(0.96, written / max(1, frames) * 0.96),
                    "processed": written, "total": frames, "message": "超分中",
                })

        upscale_segment(engine, source, silent, cancel=cancel, on_frame=on_frame)

        _emit(progress, {"phase": "audio", "progress": 0.97, "processed": frames,
                         "total": frames, "message": "合并音轨"})
        _resize_and_audio(silent, source, target, width=out_width, height=out_height,
                          has_audio=has_audio, keep_audio=True)

    _emit(progress, {"phase": "finalize", "progress": 1.0, "processed": frames,
                     "total": frames, "message": "完成"})
    return {
        "output_path": str(target),
        "width": out_width,
        "height": out_height,
        "scale": scale,
        "frames": frames,
    }
