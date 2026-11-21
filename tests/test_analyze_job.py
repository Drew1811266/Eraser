"""The orchestration layer: what a single analysis run samples and in what order."""

from __future__ import annotations

import unittest

from engine.pipeline.analyze_job import (
    MAX_EXTRA_FRAMES,
    AnalysisError,
    _cut_frames,
    run_analysis,
)


class CutFrameTests(unittest.TestCase):
    def test_samples_the_cut_and_the_frame_before_it(self) -> None:
        # A shot shorter than the step would otherwise contribute no sample at
        # all, and its last frame is the only place a watermark could be seen.
        self.assertEqual(_cut_frames([1.0], 10.0), [9, 10])

    def test_ignores_the_cut_at_frame_zero(self) -> None:
        self.assertEqual(_cut_frames([0.0], 10.0), [])

    def test_merges_adjacent_cuts(self) -> None:
        self.assertEqual(_cut_frames([1.0, 1.1], 10.0), [9, 10, 11])

    def test_respects_the_extra_frame_cap(self) -> None:
        cuts = [index * 0.5 for index in range(200)]
        self.assertLessEqual(len(_cut_frames(cuts, 10.0)), MAX_EXTRA_FRAMES)

    def test_no_fps_means_no_cut_frames(self) -> None:
        self.assertEqual(_cut_frames([1.0], 0.0), [])


class RunAnalysisTests(unittest.TestCase):
    def test_without_a_detector_it_is_a_structured_error(self) -> None:
        # No file access happens first: the detector check is the cheap guard.
        with self.assertRaises(AnalysisError):
            run_analysis("whatever.mp4", None)


if __name__ == "__main__":
    unittest.main()
