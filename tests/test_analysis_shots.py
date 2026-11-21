"""Shot boundaries and the advice shown next to them."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Interval,
    Keyframe,
    WatermarkTrack,
    build_shots,
    suggest_strategy,
)


class ShotTests(unittest.TestCase):
    def test_no_cuts_means_one_shot(self) -> None:
        self.assertEqual(build_shots([], fps=10.0, frame_count=100), [Interval(0, 99)])

    def test_cuts_split_the_timeline(self) -> None:
        shots = build_shots([1.0, 2.0], fps=10.0, frame_count=30)
        self.assertEqual(shots, [Interval(0, 9), Interval(10, 19), Interval(20, 29)])

    def test_cut_at_the_very_start_is_ignored(self) -> None:
        self.assertEqual(build_shots([0.0], fps=10.0, frame_count=30), [Interval(0, 29)])

    def test_cut_past_the_end_is_ignored(self) -> None:
        self.assertEqual(build_shots([99.0], fps=10.0, frame_count=30), [Interval(0, 29)])

    def test_zero_length_video(self) -> None:
        self.assertEqual(build_shots([], fps=10.0, frame_count=0), [Interval(0, 0)])


class StrategyTests(unittest.TestCase):
    @staticmethod
    def _track(motion: str = "static") -> WatermarkTrack:
        return WatermarkTrack(
            id="wm-1",
            intervals=[Interval(0, 99)],
            keyframes=[Keyframe(0, BBox(10, 10, 20, 20))],
            motion=motion,
        )

    def test_no_watermark_does_not_start_processing(self) -> None:
        advice = suggest_strategy([Interval(0, 99)], [])
        self.assertIn("no_watermark", advice["warnings"])

    def test_single_shot_static_prefers_fast_engine(self) -> None:
        advice = suggest_strategy(
            [Interval(0, 99)], [self._track()], engines=["lama_roi", "propainter_roi"]
        )
        self.assertEqual(advice["engine_id"], "lama_roi")
        self.assertFalse(advice["split_by_shot"])

    def test_multi_shot_requests_splitting(self) -> None:
        advice = suggest_strategy(
            [Interval(0, 49), Interval(50, 99)],
            [self._track()],
            engines=["lama_roi", "propainter_roi"],
        )
        self.assertTrue(advice["split_by_shot"])

    def test_moving_watermark_requests_the_quality_engine(self) -> None:
        advice = suggest_strategy(
            [Interval(0, 99)],
            [self._track("moving")],
            engines=["lama_roi", "propainter_roi"],
        )
        self.assertEqual(advice["engine_id"], "propainter_roi")
        self.assertIn("moving_watermark", advice["warnings"])

    def test_quality_engine_unavailable_falls_back_with_a_warning(self) -> None:
        advice = suggest_strategy([Interval(0, 99)], [self._track("moving")], engines=["lama_roi"])
        self.assertEqual(advice["engine_id"], "lama_roi")
        self.assertIn("quality_engine_unavailable", advice["warnings"])


if __name__ == "__main__":
    unittest.main()
