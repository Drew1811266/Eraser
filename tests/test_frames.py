"""Frame sampling maps output files back to *source* frame numbers."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.pipeline.frames import FrameSamplingError, sample_frames
from engine.platform.ffmpeg import resolve

FFMPEG = resolve("ffmpeg").path
#: `tempfile.gettempdir()` can answer with the working directory on Windows
#: (see engine/platform/paths.py), so the scratch root is pinned explicitly —
#: the same thing scripts/check.sh does for the gates.
SCRATCH = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"


def _make_video(
    path: Path, frames: int = 12, width: int = 64, height: int = 48
) -> None:
    command = [
        FFMPEG or "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=gray:s={width}x{height}:r=10",
        "-frames:v", str(frames),
        "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(command, check=True, capture_output=True)


@unittest.skipUnless(FFMPEG, "ffmpeg is required for frame sampling")
class SampleFramesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("ERASER_TEMP_DIR")
        os.environ["ERASER_TEMP_DIR"] = str(SCRATCH)
        # addClassCleanup runs even when setUpClass itself fails, which is what
        # keeps the env var from leaking into the rest of the suite.
        if previous is None:
            cls.addClassCleanup(os.environ.pop, "ERASER_TEMP_DIR", None)
        else:
            cls.addClassCleanup(os.environ.__setitem__, "ERASER_TEMP_DIR", previous)
        cls._scratch = tempfile.mkdtemp(prefix="eraser-frames-", dir=SCRATCH)
        cls.addClassCleanup(shutil.rmtree, cls._scratch, ignore_errors=True)
        cls.video = Path(cls._scratch) / "clip.mp4"
        _make_video(cls.video)

    def test_returns_every_step_th_frame(self) -> None:
        frames = sample_frames(str(self.video), step=5, source_width=64)
        self.assertEqual([index for index, _ in frames], [0, 5, 10])

    def test_images_are_bgr_arrays(self) -> None:
        frames = sample_frames(str(self.video), step=5, source_width=64)
        image = frames[0][1]
        self.assertEqual(image.ndim, 3)
        self.assertEqual(image.shape[2], 3)

    def test_downscales_wide_sources(self) -> None:
        frames = sample_frames(str(self.video), step=10, source_width=2560, max_width=1280)
        self.assertEqual(frames[0][1].shape[1], 1280)

    def test_portrait_sources_are_bounded_by_the_longest_side(self) -> None:
        portrait = Path(self._scratch) / "portrait.mp4"
        _make_video(portrait, frames=4, width=48, height=64)
        frames = sample_frames(
            str(portrait), step=2, source_width=48, source_height=2560, max_width=1280
        )
        self.assertEqual(frames[0][1].shape[0], 1280)

    def test_forces_extra_frames_that_the_grid_misses(self) -> None:
        frames = sample_frames(
            str(self.video), step=5, source_width=64, fps=10.0, extra_frames=[3]
        )
        self.assertEqual([index for index, _ in frames], [0, 3, 5, 10])

    def test_sample_cap_is_enforced(self) -> None:
        with self.assertRaises(FrameSamplingError):
            sample_frames(str(self.video), step=5, source_width=64, max_samples=2)

    def test_step_below_one_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            sample_frames(str(self.video), step=0, source_width=64)


if __name__ == "__main__":
    unittest.main()
