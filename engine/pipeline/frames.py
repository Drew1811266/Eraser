"""Extract every N-th frame for analysis, plus the frames a cut lands on.

One ffmpeg pass for the uniform grid rather than one call per sample: a 30 s clip
at a 15-frame step is 60 samples, and 60 process launches costs more than the
decode itself.

The mapping from output file to source frame index relies on `select` keeping
input order and `-fps_mode vfr` writing one file per selected frame, so file *k*
is source frame `k * step`. Cut frames are extracted separately (a handful of
fast seeks) because a 0.4 s shot can fall entirely between two grid samples —
that is precisely the shot whose watermark would otherwise leak through.

Frames come back in the *sampled* image's coordinate system. Callers that need
source pixels (association does) must use `analysis.rescale_detections` with the
ratio between the source size and `image.shape`.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

import cv2

from engine.pipeline.logging import logger
from engine.platform.ffmpeg import resolve
from engine.platform.paths import temp_dir

JPEG_QUALITY = 3
#: Ceiling on how many frames a single analysis keeps in memory at once.
MAX_SAMPLES = 240


class FrameSamplingError(RuntimeError):
    """The clip could not be sampled."""


def _scale_filter(source_width: int, source_height: int, max_width: int) -> str:
    """Downscale by the *longest* side, so portrait video is bounded too."""
    if max_width <= 0:
        return ""
    longest = max(source_width, source_height or 0)
    if longest <= max_width:
        return ""
    if (source_height or 0) > source_width:
        return f"scale=-2:{max_width}"
    return f"scale={max_width}:-2"


def _run(command: list[str], timeout: float) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FrameSamplingError(f"ffmpeg failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace")[-300:]
        raise FrameSamplingError(f"ffmpeg exit {completed.returncode}: {detail}")


def sample_frames(
    path: str,
    *,
    step: int,
    source_width: int,
    source_height: int = 0,
    max_width: int = 1280,
    fps: float = 0.0,
    extra_frames: Sequence[int] = (),
    max_samples: int = MAX_SAMPLES,
    timeout: float = 300.0,
) -> list[tuple[int, Any]]:
    if step < 1:
        raise ValueError("step must be >= 1")
    if step == 1:
        raise ValueError("step=1 would decode the whole clip; sample instead")

    ffmpeg = resolve("ffmpeg").path
    if not ffmpeg:
        raise FrameSamplingError("ffmpeg not found")

    scale = _scale_filter(int(source_width), int(source_height), int(max_width))
    filters = [f"select=not(mod(n\\,{step}))"]
    if scale:
        filters.append(scale)

    # `temp_dir()` documents that callers create the leaf they use: its last
    # fallback is a path that may not exist (or may not be writable) yet, so
    # mkdir is not optional — and a failure has to read as "no scratch space",
    # not as a raw WinError from three frames down.
    scratch_root = temp_dir()
    try:
        scratch_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FrameSamplingError(f"temp directory is not writable: {scratch_root} ({exc})") from exc
    scratch = Path(tempfile.mkdtemp(prefix="analyze-", dir=scratch_root))
    try:
        _run(
            [
                ffmpeg, "-y", "-v", "error",
                "-i", path,
                "-vf", ",".join(filters),
                # `-vsync` was removed in the 2026 builds this project ships;
                # `-fps_mode` is its replacement and means the same thing here.
                "-fps_mode", "vfr",
                "-q:v", str(JPEG_QUALITY),
                str(scratch / "%05d.jpg"),
            ],
            timeout,
        )

        sampled: dict[int, Any] = {}
        for offset, file in enumerate(sorted(scratch.glob("[0-9]*.jpg"))):
            image = cv2.imread(str(file))
            if image is None:
                logger.warning("unreadable sampled frame %s", file)
                continue
            sampled[offset * step] = image

        wanted = {
            int(index)
            for index in extra_frames
            if int(index) > 0 and int(index) % step != 0
        }
        if fps > 0:
            for index in sorted(wanted):
                target = scratch / f"extra-{index}.jpg"
                command = [ffmpeg, "-y", "-v", "error", "-ss", f"{index / fps:.6f}", "-i", path]
                if scale:
                    command += ["-vf", scale]
                command += ["-frames:v", "1", "-q:v", str(JPEG_QUALITY), str(target)]
                try:
                    _run(command, min(timeout, 60.0))
                except FrameSamplingError as exc:
                    # A cut frame we cannot seek to must not fail the whole run;
                    # the grid sample is still there.
                    logger.warning("cannot sample cut frame %d: %s", index, exc)
                    continue
                image = cv2.imread(str(target))
                if image is not None:
                    sampled[index] = image

        if not sampled:
            raise FrameSamplingError(f"no frames extracted from {path}")
        if len(sampled) > max_samples:
            raise FrameSamplingError(
                f"analysis would hold {len(sampled)} frames in memory "
                f"(limit {max_samples}); increase the sample step"
            )
        return sorted(sampled.items(), key=lambda item: item[0])
    finally:
        for file in scratch.glob("*.jpg"):
            try:
                file.unlink()
            except OSError:
                pass
        try:
            scratch.rmdir()
        except OSError:
            pass
