"""The evaluator's arithmetic, tested without rendering a single video.

The 7-case calibration (`scripts/eval-analysis.py --detector stub`) proves the
metrics against the real pipeline, but it needs ffmpeg and a minute of work. These
tests pin the two rules that are easy to get subtly wrong: an empty ground truth
is vacuous success, and a verdict needs every column to agree.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from engine.pipeline.analysis import Analysis, BBox, Interval, Keyframe, WatermarkTrack

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval-analysis.py"


def _load():
    spec = importlib.util.spec_from_file_location("eval_analysis", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


EVAL = _load()


def _analysis(tracks: list[WatermarkTrack]) -> Analysis:
    return Analysis(
        video={"width": 640, "height": 360, "fps": 10.0, "frame_count": 100, "duration_seconds": 10.0},
        shots=[Interval(0, 99)],
        watermarks=tracks,
        suggested={"engine_id": "lama_roi", "split_by_shot": False, "warnings": []},
        model_id="test",
        elapsed_ms=10,
    )


def _track(*, x=100, intervals=((0, 99),), motion="static") -> WatermarkTrack:
    return WatermarkTrack(
        id="wm-1",
        intervals=[Interval(*item) for item in intervals],
        keyframes=[Keyframe(0, BBox(x, 20, 80, 40))],
        motion=motion,
    )


class EmptyTruthTests(unittest.TestCase):
    def test_a_clean_clip_scores_full_coverage(self) -> None:
        truth = {"case": "clean", "present": [], "rects": {}, "expected": {"tracks": 0, "intervals": 0}}
        row = EVAL._evaluate(truth, _analysis([]))
        self.assertEqual(row["coverage"], 1.0)
        self.assertEqual(row["false"], 0.0)
        self.assertEqual(EVAL._verdict(row), "PASS")

    def test_a_watermark_on_a_clean_clip_fails(self) -> None:
        truth = {"case": "clean", "present": [], "rects": {}, "expected": {"tracks": 0, "intervals": 0}}
        row = EVAL._evaluate(truth, _analysis([_track()]))
        self.assertEqual(row["tracks"], 1)
        self.assertEqual(EVAL._verdict(row), "FAIL")


class MetricTests(unittest.TestCase):
    def _truth(self) -> dict:
        return {
            "case": "corner",
            "present": [[0, 99]],
            "rects": {"0": {"x": 100, "y": 20, "width": 80, "height": 40},
                      "50": {"x": 100, "y": 20, "width": 80, "height": 40}},
            "expected": {"tracks": 1, "motion": "static", "intervals": 1},
        }

    def test_a_perfect_track_hits_every_probe(self) -> None:
        row = EVAL._evaluate(self._truth(), _analysis([_track()]))
        self.assertEqual(row["hit"], 1.0)
        self.assertEqual(row["coverage"], 1.0)
        self.assertEqual(EVAL._verdict(row), "PASS")

    def test_a_misplaced_rect_fails_the_hit_rate(self) -> None:
        row = EVAL._evaluate(self._truth(), _analysis([_track(x=400)]))
        self.assertEqual(row["hit"], 0.0)
        self.assertEqual(EVAL._verdict(row), "FAIL")

    def test_missing_frames_lower_the_coverage(self) -> None:
        row = EVAL._evaluate(self._truth(), _analysis([_track(intervals=((0, 49),))]))
        self.assertAlmostEqual(row["coverage"], 0.5, places=3)

    def test_wrong_motion_fails(self) -> None:
        row = EVAL._evaluate(self._truth(), _analysis([_track(motion="moving")]))
        self.assertEqual(EVAL._verdict(row), "FAIL")

    def test_wrong_interval_count_fails(self) -> None:
        row = EVAL._evaluate(self._truth(), _analysis([_track(intervals=((0, 49), (60, 99)))]))
        self.assertEqual(row["intervals"], 2)
        self.assertEqual(EVAL._verdict(row), "FAIL")


if __name__ == "__main__":
    unittest.main()
