"""Extract every N-th frame for analysis.

One ffmpeg pass rather than one call per sample: a 30 s clip at a 15-frame step
is 60 samples, and 60 process launches costs more than the decode itself.

The mapping from output file to source frame index relies on `select` keeping
input order and `-vsync vfr` writing one file per selected frame, so file *k* is
source frame `k * step`. The order is verified by construction (sorted filenames)
and a clip that yields nothing is an error rather than a silently empty analysis.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2

from engine.pipeline.logging import logger
from engine.platform.ffmpeg import resolve
from engine.platform.paths import temp_dir

JPEG_QUALITY = 3


class FrameSamplingError(RuntimeError):
    """The clip could not be sampled."""


def sample_frames(
    path: str,
    *,
    step: int,
    source_width: int,
    max_width: int = 1280,
    timeout: float = 300.0,
) -> list[tuple[int, Any]]:
    if step < 1:
        raise ValueError("step must be >= 1")
    if step == 1:
        raise ValueError("step=1 would decode the whole clip; sample instead")

    ffmpeg = resolve("ffmpeg").path
    if not ffmpeg:
        raise FrameSamplingError("ffmpeg not found")

    filters = [f"select=not(mod(n\\,{step}))"]
    if source_width > max_width > 0:
        filters.append(f"scale={max_width}:-2")

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
        pattern = str(scratch / "%05d.jpg")
        command = [
            ffmpeg, "-y", "-v", "error",
            "-i", path,
            "-vf", ",".join(filters),
            # `-vsync` was removed in the 2026 builds this project ships;
            # `-fps_mode` is its replacement and means the same thing here.
            "-fps_mode", "vfr",
            "-q:v", str(JPEG_QUALITY),
            pattern,
        ]
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

        files = sorted(scratch.glob("*.jpg"))
        if not files:
            raise FrameSamplingError(f"no frames extracted from {path}")

        frames: list[tuple[int, Any]] = []
        for offset, file in enumerate(files):
            image = cv2.imread(str(file))
            if image is None:
                logger.warning("unreadable sampled frame %s", file)
                continue
            frames.append((offset * step, image))
        if not frames:
            raise FrameSamplingError(f"no readable frames in {path}")
        return frames
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
