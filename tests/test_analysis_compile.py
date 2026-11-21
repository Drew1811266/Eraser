"""Tracks compile down to the Segment shape the pipeline already eats."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    BBox,
    Interval,
    Keyframe,
    WatermarkTrack,
    compile_tracks,
)


def _track(intervals, keyframes, motion="static") -> WatermarkTrack:
    return WatermarkTrack(
        id="wm-1",
        intervals=[Interval(*item) for item in intervals],
        keyframes=[Keyframe(frame, BBox(*rect)) for frame, rect in keyframes],
        motion=motion,
    )


class CompileTests(unittest.TestCase):
    def test_static_single_shot_yields_one_segment(self) -> None:
        track = _track([(0, 99)], [(0, (10, 20, 30, 40))])
        segments = compile_tracks(
            [track], shots=[Interval(0, 99)], frame_width=640, frame_height=360
        )
        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual((segment["start_frame"], segment["end_frame"]), (0, 99))
        self.assertEqual(segment["rect"], {"x": 10, "y": 20, "width": 30, "height": 40})
        self.assertEqual(segment["expand_px"], 5)
        self.assertEqual(segment["feather_px"], 3)
        self.assertTrue(segment["enabled"])

    def test_multi_shot_splits_the_track(self) -> None:
        track = _track([(0, 99)], [(0, (10, 20, 30, 40))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["end_frame"], 49)
        self.assertEqual(segments[1]["start_frame"], 50)

    def test_moving_track_uses_the_union_box(self) -> None:
        track = _track(
            [(0, 49)],
            [(0, (10, 20, 30, 30)), (40, (200, 20, 30, 30))],
            motion="moving",
        )
        segment = compile_tracks(
            [track], shots=[Interval(0, 49)], frame_width=640, frame_height=360
        )[0]
        self.assertEqual(segment["rect"], {"x": 10, "y": 20, "width": 220, "height": 30})

    def test_keyframes_are_kept_and_clipped_to_the_segment(self) -> None:
        track = _track([(0, 99)], [(0, (10, 10, 10, 10)), (60, (50, 10, 10, 10))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual([item["frame"] for item in segments[0]["keyframes"]], [0])
        self.assertEqual([item["frame"] for item in segments[1]["keyframes"]], [60])

    def test_segment_ids_are_unique(self) -> None:
        track = _track([(0, 99)], [(0, (10, 10, 10, 10))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 49), Interval(50, 99)],
            frame_width=640,
            frame_height=360,
        )
        self.assertEqual(len({item["id"] for item in segments}), len(segments))

    def test_box_is_clamped_to_the_frame(self) -> None:
        track = _track([(0, 9)], [(0, (630, 350, 100, 100))])
        segment = compile_tracks(
            [track], shots=[Interval(0, 9)], frame_width=640, frame_height=360
        )[0]
        self.assertEqual(segment["rect"], {"x": 630, "y": 350, "width": 10, "height": 10})

    def test_empty_track_list_compiles_to_nothing(self) -> None:
        self.assertEqual(
            compile_tracks([], shots=[Interval(0, 9)], frame_width=640, frame_height=360), []
        )

    def test_compile_respects_frame_count(self) -> None:
        track = _track([(0, 500)], [(0, (10, 10, 10, 10))])
        segments = compile_tracks(
            [track],
            shots=[Interval(0, 500)],
            frame_width=640,
            frame_height=360,
            frame_count=100,
        )
        self.assertEqual(segments[0]["end_frame"], 99)

    def test_fallback_keyframe_is_relabelled_into_the_segment(self) -> None:
        track = _track([(100, 200)], [(0, (10, 10, 10, 10))])
        segments = compile_tracks(
            [track], shots=[Interval(100, 200)], frame_width=640, frame_height=360
        )
        self.assertEqual(segments[0]["keyframes"][0]["frame"], 100)

    def test_keyframe_rects_are_clamped_like_the_segment_rect(self) -> None:
        track = _track([(0, 9)], [(0, (630, 350, 100, 100))])
        segment = compile_tracks(
            [track], shots=[Interval(0, 9)], frame_width=640, frame_height=360
        )[0]
        self.assertEqual(
            segment["keyframes"][0]["rect"],
            {"x": 630, "y": 350, "width": 10, "height": 10},
        )


if __name__ == "__main__":
    unittest.main()
