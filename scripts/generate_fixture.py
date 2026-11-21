#!/usr/bin/env python3
"""Synthetic clips for tests and golden cases.

Shared by the video end-to-end test and the golden runner: both need a clip with
a *known* watermark — a rectangle at a known place, in a known colour, on a
background that is easy to reason about — and neither wants two copies of that
code drifting apart.

    python scripts/generate_fixture.py out.mp4 --width 640 --height 360 --frames 48

The colour of the watermark and the gradient behind it are fixed constants:
the tests assert on them numerically ("the watermark colour is gone"), so they
are part of the contract, not decoration.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"

WATERMARK_COLOUR = (230, 40, 60)
WATERMARK_GLYPH = (255, 255, 255)


def background(width: int, height: int, phase: float) -> np.ndarray:
    """A moving gradient: enough structure that inpainting cannot cheat."""
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)
    ys = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    red = 60 + 120 * xs[None, :] * (0.6 + 0.4 * np.sin(phase))
    green = 110 + 80 * ys * (0.7 + 0.3 * np.cos(phase * 1.3))
    blue = 140 + 60 * (1.0 - ys) * (0.6 + 0.4 * np.sin(phase * 0.7))
    frame = np.stack([
        np.broadcast_to(red, (height, width)),
        np.broadcast_to(green, (height, width)),
        np.broadcast_to(blue, (height, width)),
    ], axis=2)
    return np.clip(frame, 0, 255).astype(np.uint8)


def paint_watermark(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray:
    """Draw the watermark: a solid block with a lighter bar through it."""
    x, y, width, height = rect
    canvas = frame.copy()
    frame_height, frame_width = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame_width, x + width), min(frame_height, y + height)
    if x1 <= x0 or y1 <= y0:
        return canvas

    canvas[y0:y1, x0:x1] = WATERMARK_COLOUR
    inset_x = max(1, (x1 - x0) // 8)
    inset_y = max(1, (y1 - y0) // 4)
    canvas[y0 + inset_y:y1 - inset_y, x0 + inset_x:x1 - inset_x] = WATERMARK_GLYPH
    return canvas


def _ffmpeg() -> str:
    if not FFMPEG.is_file():
        found = None
        for candidate in ("ffmpeg", "ffmpeg.exe"):
            import shutil

            found = shutil.which(candidate)
            if found:
                break
        if not found:
            raise SystemExit("ffmpeg not found; run scripts/fetch-assets.py")
        return found
    return str(FFMPEG)


def make_clip(
    path: Path,
    width: int,
    height: int,
    fps: int,
    frames: int,
    rect: tuple[int, int, int, int],
    audio: bool = False,
) -> Path:
    """Encode the synthetic clip; optionally mux a tone so audio can be checked."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    silent = path.with_name(f"{path.stem}_silent.mp4") if audio else path

    command = [
        _ffmpeg(), "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(silent),
    ]
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        for index in range(frames):
            frame = background(width, height, 6.0 * index / max(1, frames))
            frame = paint_watermark(frame, rect)
            process.stdin.write(frame.astype(np.uint8).tobytes())
        process.stdin.close()
    finally:
        stderr = process.stderr.read().decode("utf-8", "replace")
        code = process.wait()
    if code != 0:
        raise SystemExit(f"fixture encode failed: {stderr[-600:]}")

    if audio:
        mixed = subprocess.run(
            [_ffmpeg(), "-y", "-v", "error", "-i", str(silent),
             "-f", "lavfi", "-i", f"sine=frequency=440:duration={frames / fps:.3f}",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)],
            capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        silent.unlink(missing_ok=True)
        if mixed.returncode != 0:
            raise SystemExit(f"audio mux failed: {mixed.stderr.decode('utf-8', 'replace')[-500:]}")

    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--frames", type=int, default=48)
    parser.add_argument("--rect", default="480,20,120,60")
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()

    rect = tuple(int(part) for part in args.rect.split(","))
    make_clip(args.path, args.width, args.height, args.fps, args.frames, rect, args.audio)
    print(args.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
