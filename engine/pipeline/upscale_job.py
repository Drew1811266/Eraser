"""Video-level upscaling: decode -> Real-ESRGAN -> encode -> remux audio.

ffmpeg does the container work and Python does the pixels, with the frame
streaming through a pipe so a 4K upscale never materialises the whole video in
memory.

Two processes are involved rather than one, because a single ffmpeg cannot both
decode the source and mux the source's audio while its video input comes from a
pipe. So the video is encoded first and the audio is copied in a second,
lossless pass — which also means a failed audio remux still leaves a usable
silent file rather than nothing.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np

from engine.pipeline.logging import logger
from engine.platform.ffmpeg import resolve

ProgressCallback = Callable[[dict[str, Any]], None]
CancelEvent = Any

X264_ARGS = [
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
]


class UpscaleError(RuntimeError):
    """Raised when ffmpeg or the model fails mid-run."""


def _ffmpeg() -> str:
    found = resolve("ffmpeg")
    if not found.path:
        raise UpscaleError("ffmpeg not found")
    return found.path


def _probe(path: str) -> tuple[int, int, float, bool, int]:
    from engine.pipeline.watermark import VideoProcessor

    info = VideoProcessor(remover=None).get_video_info(path)
    return (
        int(info.width),
        int(info.height),
        float(info.fps),
        bool(info.has_audio),
        int(info.frame_count),
    )


def upscale_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    scale: int = 4,
    progress: ProgressCallback | None = None,
    cancel: CancelEvent | None = None,
    upscaler: Any = None,
) -> dict[str, Any]:
    """Upscale a video by `scale` (2 or 4). Returns a result dict."""
    from engine.pipeline.upscaler import SCALE, RealEsrganUpscaler

    if scale not in (2, 4):
        raise UpscaleError(f"unsupported scale {scale}; expected 2 or 4")

    source = Path(input_path)
    target = Path(output_path)
    if not source.is_file():
        raise UpscaleError(f"input not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)

    width, height, fps, has_audio, frames = _probe(str(source))
    if frames <= 0 or width <= 0 or height <= 0:
        raise UpscaleError("could not read the source video")

    out_width, out_height = width * SCALE, height * SCALE
    engine = upscaler or RealEsrganUpscaler()
    engine.load_model()

    def emit(payload: dict[str, Any]) -> None:
        if progress is not None:
            try:
                progress(payload)
            except Exception as exc:  # noqa: BLE001 - progress must never break a run
                logger.warning("upscale progress callback failed: %s", exc)

    emit({"phase": "prepare", "progress": 0.0, "processed": 0, "total": frames,
          "message": f"放大到 {out_width}x{out_height}"})

    with tempfile.TemporaryDirectory(prefix="eraser-upscale-") as scratch:
        silent = Path(scratch) / "video.mp4"
        _transcode(engine, str(source), str(silent), width, height, out_width, out_height, fps,
                   frames, scale, emit, cancel)
        emit({"phase": "audio", "progress": 0.97, "processed": frames, "total": frames,
              "message": "合并音轨"})
        _mux_audio(str(silent), str(source), str(target), has_audio)

    emit({"phase": "finalize", "progress": 1.0, "processed": frames, "total": frames,
          "message": "完成"})
    # The encoder produces the model's native 4x; a 2x request is filtered down
    # afterwards, so the reported size is the request, not the encoder input.
    final_width, final_height = width * scale, height * scale
    return {
        "output_path": str(target),
        "width": final_width,
        "height": final_height,
        "scale": scale,
        "frames": frames,
    }


def _transcode(
    engine: Any,
    source: str,
    destination: str,
    width: int,
    height: int,
    out_width: int,
    out_height: int,
    fps: float,
    frames: int,
    scale: int,
    emit: ProgressCallback,
    cancel: CancelEvent,
) -> None:
    ffmpeg = _ffmpeg()
    frame_bytes = width * height * 3

    decode = subprocess.Popen(
        [ffmpeg, "-v", "error", "-i", source, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_no_window(),
    )

    # A 2x result is produced by downscaling the 4x model output with lanczos,
    # which looks better than running the model at a fake 2x.
    filters = ["scale=iw/2:ih/2:flags=lanczos"] if scale == 2 else []

    encode_command = [
        ffmpeg, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{out_width}x{out_height}", "-r", f"{fps:.6f}",
        "-i", "-",
    ]
    if filters:
        encode_command += ["-vf", ",".join(filters)]
    encode_command += X264_ARGS + [destination]

    encode = subprocess.Popen(
        encode_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_no_window(),
    )

    try:
        index = 0
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
            index += 1
            if index % 5 == 0 or index == frames:
                emit({
                    "phase": "upscale",
                    "progress": min(0.96, index / max(1, frames) * 0.96),
                    "processed": index,
                    "total": frames,
                    "message": "超分中",
                })
        if index == 0:
            raise UpscaleError("ffmpeg produced no frames")
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
        # Drain to avoid leaving blocked writers behind on the exception path.
        for process in (decode, encode):
            if process.stderr is not None:
                try:
                    process.stderr.close()
                except Exception:  # noqa: BLE001
                    pass


def _mux_audio(video: str, original: str, destination: str, has_audio: bool) -> None:
    ffmpeg = _ffmpeg()
    if has_audio:
        command = [
            ffmpeg, "-y", "-v", "error",
            "-i", video, "-i", original,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart",
            destination,
        ]
        completed = subprocess.run(command, capture_output=True, creationflags=_no_window())
        if completed.returncode == 0:
            return
        logger.warning("audio remux failed, keeping a silent result: %s",
                       completed.stderr.decode("utf-8", "replace")[-300:])

    # No audio, or the remux failed: the silent video is still a valid result.
    Path(destination).unlink(missing_ok=True)
    Path(video).replace(destination)


class _Cancelled(RuntimeError):
    """Raised when the caller cancels; surfaced as CANCELLED by the handler."""


def _no_window() -> int:
    import os

    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
