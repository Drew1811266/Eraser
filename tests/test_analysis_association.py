"""Association is the hard part: these tests are the specification."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Detection,
    Interval,
    WatermarkTrack,
    associate,
    classify_motion,
    compile_tracks,
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
        # A static watermark carries one representative rectangle (spec §5.1),
        # not one per sampled frame.
        self.assertEqual(len(tracks[0].keyframes), 1)

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

    def test_track_cap_keeps_the_most_confident(self) -> None:
        weak = [Detection(0, BBox(100 + index * 40, 20, 30, 40), 0.4) for index in range(11)]
        loud = Detection(0, BBox(20, 20, 30, 40), 0.99)
        tracks = _run({0: weak + [loud]})
        self.assertEqual(len(tracks), 8)
        self.assertIn(
            20,
            {track.keyframes[0].rect.x for track in tracks},
            "the most confident detection must survive the cap",
        )


    def test_nearby_tracks_do_not_steal_each_others_boxes(self) -> None:
        def small(frame: int, x: int) -> Detection:
            return Detection(frame, BBox(x, 20, 30, 30), 0.9)

        detections = {
            0: [small(0, 40), small(0, 90)],
            # The left watermark jumps past the association window, the right one
            # stays put. A per-track greedy match would hand the right one's box
            # to the left track and call it "moving".
            15: [small(15, 100), small(15, 90)],
        }
        tracks = _run(detections)
        segments = compile_tracks(
            tracks, shots=[Interval(0, 15)], frame_width=W, frame_height=H
        )
        self.assertTrue(
            all(segment["rect"]["width"] <= 40 for segment in segments),
            "a segment wider than one box means two tracks were cross-bound",
        )


class MotionTests(unittest.TestCase):
    def test_static_track(self) -> None:
        track = _run({0: [_det(0, 100)], 15: [_det(15, 100)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "static")

    def test_large_displacement_is_moving(self) -> None:
        # 40 px of travel: inside the 8%-of-width association window (51 px), so
        # this is one track — and past the 5%-of-width motion threshold (32 px).
        track = _run({0: [_det(0, 100)], 15: [_det(15, 140)]})[0]
        self.assertEqual(classify_motion(track, frame_width=W, frame_height=H), "moving")


class DistanceFallbackTests(unittest.TestCase):
    def test_small_box_without_overlap_still_continues(self) -> None:
        # A 30 px box moving 40 px: zero IoU with the previous sample, but well
        # inside the 8%-of-width association window. The fallback must fire when
        # *no* candidate overlaps, which is the small-watermark case.
        detections = {
            0: [Detection(0, BBox(100, 20, 30, 30), 0.9)],
            15: [Detection(15, BBox(140, 20, 30, 30), 0.9)],
        }
        tracks = _run(detections)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].motion, "moving")


class ReopenIdentityTests(unittest.TestCase):
    def test_two_watermarks_keep_their_identity_after_a_shared_miss(self) -> None:
        detections = {
            0: [
                Detection(0, BBox(40, 20, 80, 40), 0.9),
                Detection(0, BBox(200, 20, 80, 40), 0.9),
            ],
            15: [],
            # Detection order is reversed on purpose: identity must come from
            # position, not from the order a detector happened to emit.
            30: [
                Detection(30, BBox(200, 20, 80, 40), 0.9),
                Detection(30, BBox(40, 20, 80, 40), 0.9),
            ],
        }
        tracks = _run(detections)
        self.assertEqual(len(tracks), 2)
        segments = compile_tracks(
            tracks, shots=[Interval(0, 30)], frame_width=W, frame_height=H
        )
        self.assertEqual(sorted(segment["rect"]["x"] for segment in segments), [40, 200])


    def test_same_shot_jump_beyond_the_window_is_a_new_track(self) -> None:
        # 160 px apart in the same shot is two watermarks, not one that jumped.
        # Merging them is the failure mode the spec calls out first.
        tracks = _run({0: [_det(0, 40)], 15: [_det(15, 200)]})
        self.assertEqual(len(tracks), 2)


class ShotBoundaryTests(unittest.TestCase):
    def test_cut_starts_a_new_interval_but_keeps_the_track(self) -> None:
        shots = [Interval(0, 14), Interval(15, 44)]
        detections = {frame: [_det(frame, 100)] for frame in (0, 15, 30)}
        tracks = _run(detections, shots=shots)
        self.assertEqual(len(tracks), 1, "a cut must not create a second watermark")
        self.assertEqual(len(tracks[0].intervals), 2)

    def test_motion_is_measured_per_interval_not_across_the_cut(self) -> None:
        # The same logo sits at 500 in shot 1 and at 100 in shot 2. Comparing
        # the two positions would call it moving; each shot is static.
        shots = [Interval(0, 14), Interval(15, 44)]
        detections = {
            0: [Detection(0, BBox(500, 20, 60, 40), 0.9)],
            15: [Detection(15, BBox(100, 20, 60, 40), 0.9)],
            30: [Detection(30, BBox(100, 20, 60, 40), 0.9)],
        }
        tracks = _run(detections, shots=shots)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].motion, "static")


if __name__ == "__main__":
    unittest.main()
