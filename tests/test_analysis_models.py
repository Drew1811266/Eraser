"""The value objects the whole analysis pipeline passes around."""

from __future__ import annotations

import unittest

from engine.pipeline.analysis import (
    Analysis,
    BBox,
    Detection,
    Interval,
    Keyframe,
    WatermarkTrack,
    center_distance,
    iou,
    rescale_detections,
)


class BBoxTests(unittest.TestCase):
    def test_corners_and_area(self) -> None:
        box = BBox(10, 20, 30, 40)
        self.assertEqual((box.x2, box.y2), (40, 60))
        self.assertEqual(box.area, 1200)

    def test_dict_round_trip(self) -> None:
        box = BBox(1, 2, 3, 4)
        self.assertEqual(BBox.from_dict(box.as_dict()), box)

    def test_from_dict_coerces_strings(self) -> None:
        box = BBox.from_dict({"x": "1", "y": "2", "width": "3", "height": "4"})
        self.assertEqual(box, BBox(1, 2, 3, 4))

    def test_clamp_keeps_box_inside_the_frame(self) -> None:
        box = BBox(630, 350, 100, 100).clamp(640, 360)
        self.assertEqual(box, BBox(630, 350, 10, 10))

    def test_clamp_rejects_negative_origin(self) -> None:
        self.assertEqual(BBox(-5, -5, 10, 10).clamp(640, 360), BBox(0, 0, 10, 10))

    def test_clamp_never_returns_zero_size(self) -> None:
        box = BBox(639, 359, 50, 50).clamp(640, 360)
        self.assertGreaterEqual(box.width, 1)
        self.assertGreaterEqual(box.height, 1)

    def test_union_covers_every_box(self) -> None:
        merged = BBox.union([BBox(10, 10, 10, 10), BBox(50, 40, 20, 20)])
        self.assertEqual(merged, BBox(10, 10, 60, 50))


class GeometryTests(unittest.TestCase):
    def test_iou_of_identical_boxes_is_one(self) -> None:
        self.assertAlmostEqual(iou(BBox(0, 0, 10, 10), BBox(0, 0, 10, 10)), 1.0)

    def test_iou_of_disjoint_boxes_is_zero(self) -> None:
        self.assertEqual(iou(BBox(0, 0, 10, 10), BBox(100, 100, 10, 10)), 0.0)

    def test_iou_ignores_zero_sized_input(self) -> None:
        self.assertEqual(iou(BBox(0, 0, 0, 0), BBox(0, 0, 10, 10)), 0.0)

    def test_center_distance(self) -> None:
        self.assertAlmostEqual(center_distance(BBox(0, 0, 10, 10), BBox(30, 0, 10, 10)), 30.0)


class TrackSerialisationTests(unittest.TestCase):
    def test_track_dict_shape(self) -> None:
        track = WatermarkTrack(
            id="wm-1",
            intervals=[Interval(0, 299)],
            keyframes=[Keyframe(0, BBox(1, 2, 3, 4))],
            motion="static",
            confidence=0.9,
        )
        payload = track.as_dict()
        self.assertEqual(payload["id"], "wm-1")
        self.assertEqual(payload["intervals"], [{"start_frame": 0, "end_frame": 299}])
        self.assertEqual(
            payload["keyframes"],
            [{"frame": 0, "rect": {"x": 1, "y": 2, "width": 3, "height": 4}}],
        )
        self.assertEqual(payload["source"], "auto")

    def test_analysis_dict_shape(self) -> None:
        analysis = Analysis(
            video={
                "width": 640,
                "height": 360,
                "fps": 30.0,
                "frame_count": 300,
                "duration_seconds": 10.0,
            },
            shots=[Interval(0, 299)],
            watermarks=[],
            suggested={"engine_id": "lama_roi", "split_by_shot": False, "warnings": []},
            model_id="stub",
            elapsed_ms=12,
        )
        payload = analysis.as_dict()
        self.assertEqual(payload["shots"], [{"start_frame": 0, "end_frame": 299}])
        self.assertEqual(payload["watermarks"], [])
        self.assertEqual(payload["model_id"], "stub")


class RescaleTests(unittest.TestCase):
    """Detections arrive in the sampled frame's space, not the source's.

    `sample_frames` downscales anything wider than 1280, so a detector that saw
    a 1280-wide image reports boxes that must be scaled before they mean
    anything on a 1920-wide source.
    """

    def test_maps_back_to_source_pixels(self) -> None:
        detection = Detection(0, BBox(100, 50, 40, 20), 0.9)
        scaled = rescale_detections([detection], 1.5, 1.5)[0]
        self.assertEqual(scaled.rect, BBox(150, 75, 60, 30))
        self.assertEqual(scaled.frame, 0)
        self.assertAlmostEqual(scaled.confidence, 0.9)

    def test_identity_scale_is_a_no_op(self) -> None:
        detection = Detection(7, BBox(10, 20, 30, 40), 0.5)
        self.assertEqual(rescale_detections([detection], 1.0, 1.0), [detection])

    def test_zero_scale_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rescale_detections([], 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
