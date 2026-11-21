"""Association is the hard part: these tests are the specification."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Detection,
    WatermarkTrack,
    associate,
    classify_motion,
)

W, H, FPS = 640, 360, 30.0
FRAMES = 600


def _det(frame: int, x: int, y: int = 20) -> Detection:
    return Detection(frame, BBox(x, y, 80, 40), 0.9)


def _run(detections: dict[int, list[Detection]], **kwargs) -> list[WatermarkTrack]:
    params = dict(frame_count=FRAMES, frame_width=W, frame_height=H, fps=FPS)
    params.update(kwargs)
    return associate(detections, **params)


class ContinuationTests(unittest.TestCase):
    def test_single_static_watermark_is_one_track(self) -> None:
        tracks = _run({frame: [_det(frame, 100)] for frame in (0, 15, 30)})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].intervals[0].start_frame, 0)
        self.assertEqual(tracks[0].intervals[0].end_frame, 30)
        self.assertEqual(len(tracks[0].keyframes), 3)

    def test_slow_motion_still_continues(self) -> None:
        tracks = _run({frame: [_det(frame, 100 + frame // 2)] for frame in (0, 15, 30)})
        self.assertEqual(len(tracks), 1)

    def test_single_missed_sample_does_not_split_the_interval(self) -> None:
        # 30 fps, samples every 15 frames: one missed sample is a 0.5 s gap,
        # well inside the 1.5 s merge window.
        tracks = _run({0: [_det(0, 100)], 15: [], 30: [_det(30, 100)]})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].intervals), 1)

    def test_gap_between_the_two_windows_starts_a_second_interval(self) -> None:
        # 90 frames at 30 fps = 3 s: past the 1.5 s merge window,
        # still inside the 5 s reopen window.
        tracks = _run({0: [_det(0, 100)], 90: [_det(90, 100)]})
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0].intervals), 2)

    def test_very_long_absence_creates_a_new_track(self) -> None:
        # 300 frames at 30 fps = 10 s: past the reopen window.
        tracks = _run({0: [_det(0, 100)], 300: [_det(300, 100)]})
        self.assertEqual(len(tracks), 2)


class SeparationTests(unittest.TestCase):
    def test_two_simultaneous_watermarks_stay_separate(self) -> None:
        detections = {frame: [_det(frame, 40), _det(frame, 480)] for frame in (0, 15, 30)}
        self.assertEqual(len(_run(detections)), 2)

    def test_low_confidence_is_dropped(self) -> None:
        detections = {0: [Detection(0, BBox(10, 10, 20, 20), 0.1)]}
        self.assertEqual(_run(detections), [])

    def test_oversized_box_is_dropped(self) -> None:
        detections = {0: [Detection(0, BBox(0, 0, W, H), 0.99)]}
        self.assertEqual(_run(detections), [])

    def test_track_cap_is_enforced(self) -> None:
        detections = {0: [_det(0, 10 + index * 100) for index in range(12)]}
        self.assertEqual(len(_run(detections)), 8)


class MotionTests(unittest.TestCase):
    def test_static_track(self) -> None:
        track = _run({0: [_det(0, 100)], 15: [_det(15, 100)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "static")

    def test_large_displacement_is_moving(self) -> None:
        # 40 px of travel: inside the 8%-of-width association window (51 px), so
        # this is one track — and past the 5%-of-width motion threshold (32 px).
        track = _run({0: [_det(0, 100)], 15: [_det(15, 140)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "moving")


if __name__ == "__main__":
    unittest.main()
