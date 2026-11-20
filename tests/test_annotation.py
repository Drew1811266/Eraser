"""Behaviour lock for annotation.normalize.

Every assertion here mirrors a rule in the reference Python implementation.
If one of these has to change, existing .wmr.json sidecars change meaning too.
"""

from __future__ import annotations

import unittest

from engine.methods.annotation import normalize_segments

VIDEO = {"width": 1920, "height": 1080, "frame_count": 900}
FIXED_NOW = "2026-01-01T00:00:00+00:00"


def normalize(segments: list, video: dict | None = None) -> list[dict]:
    return normalize_segments(segments, video or VIDEO, now=lambda: FIXED_NOW)


class RectTests(unittest.TestCase):
    def test_rect_inside_frame_is_untouched(self) -> None:
        result = normalize([{"rect": {"x": 100, "y": 50, "width": 200, "height": 80}}])
        self.assertEqual(result[0]["rect"], {"x": 100, "y": 50, "width": 200, "height": 80})

    def test_rect_overflowing_right_edge_is_truncated_not_scaled(self) -> None:
        result = normalize([{"rect": {"x": 1900, "y": 100, "width": 300, "height": 50}}])
        self.assertEqual(result[0]["rect"]["width"], 20)
        self.assertEqual(result[0]["rect"]["x"], 1900)

    def test_rect_overflowing_bottom_right_corner_is_clipped(self) -> None:
        result = normalize([{"rect": {"x": 1900, "y": 1060, "width": 300, "height": 200}}])
        self.assertEqual(result[0]["rect"], {"x": 1900, "y": 1060, "width": 20, "height": 20})

    def test_negative_origin_is_clamped_and_zero_size_becomes_one(self) -> None:
        result = normalize([{"rect": {"x": -20, "y": -20, "width": 0, "height": 0}}])
        self.assertEqual(result[0]["rect"], {"x": 0, "y": 0, "width": 1, "height": 1})

    def test_non_numeric_coordinates_fall_back_to_zero(self) -> None:
        result = normalize([{"rect": {"x": "abc", "y": None, "width": 10, "height": 10}}])
        self.assertEqual(result[0]["rect"]["x"], 0)
        self.assertEqual(result[0]["rect"]["y"], 0)


class FrameRangeTests(unittest.TestCase):
    def test_range_is_clamped_to_frame_count(self) -> None:
        result = normalize([{"start_frame": -5, "end_frame": 9999, "rect": {}}])
        self.assertEqual((result[0]["start_frame"], result[0]["end_frame"]), (0, 899))

    def test_inverted_range_is_swapped(self) -> None:
        result = normalize([{"start_frame": 100, "end_frame": 10, "rect": {}}])
        self.assertEqual((result[0]["start_frame"], result[0]["end_frame"]), (10, 100))

    def test_missing_end_frame_defaults_to_start(self) -> None:
        result = normalize([{"start_frame": 42, "rect": {}}])
        self.assertEqual((result[0]["start_frame"], result[0]["end_frame"]), (42, 42))


class DefaultsTests(unittest.TestCase):
    def test_defaults_match_reference_implementation(self) -> None:
        result = normalize([{"rect": {}}])
        segment = result[0]
        self.assertEqual(segment["expand_px"], 5)
        self.assertEqual(segment["feather_px"], 3)
        self.assertTrue(segment["enabled"])
        self.assertEqual(segment["created_at"], FIXED_NOW)
        self.assertEqual(segment["updated_at"], FIXED_NOW)

    def test_negative_expand_and_feather_are_floored_at_zero(self) -> None:
        result = normalize([{"rect": {}, "expand_px": -9, "feather_px": -4}])
        self.assertEqual(result[0]["expand_px"], 0)
        self.assertEqual(result[0]["feather_px"], 0)

    def test_explicit_values_are_preserved(self) -> None:
        result = normalize([{"rect": {}, "expand_px": 12, "feather_px": 7, "enabled": False}])
        segment = result[0]
        self.assertEqual(segment["expand_px"], 12)
        self.assertEqual(segment["feather_px"], 7)
        self.assertFalse(segment["enabled"])

    def test_created_at_is_preserved_but_updated_at_refreshed(self) -> None:
        result = normalize([{"rect": {}, "created_at": "2020-05-05T00:00:00+00:00"}])
        self.assertEqual(result[0]["created_at"], "2020-05-05T00:00:00+00:00")
        self.assertEqual(result[0]["updated_at"], FIXED_NOW)

    def test_existing_id_is_preserved(self) -> None:
        result = normalize([{"rect": {}, "id": "keep-me"}])
        self.assertEqual(result[0]["id"], "keep-me")

    def test_missing_id_is_generated_and_unique(self) -> None:
        result = normalize([{"rect": {}}, {"rect": {}}])
        first, second = result[0]["id"], result[1]["id"]
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, second)


class FilteringTests(unittest.TestCase):
    def test_non_dict_segments_are_dropped(self) -> None:
        result = normalize(["nope", 42, None, {"rect": {}}])
        self.assertEqual(len(result), 1)

    def test_segment_with_non_dict_rect_is_dropped(self) -> None:
        result = normalize([{"rect": "nope"}, {"rect": {}}])
        self.assertEqual(len(result), 1)

    def test_input_order_is_preserved(self) -> None:
        result = normalize(
            [
                {"rect": {}, "start_frame": 500},
                {"rect": {}, "start_frame": 10},
            ]
        )
        self.assertEqual([segment["start_frame"] for segment in result], [500, 10])

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(normalize([]), [])


class DegenerateVideoTests(unittest.TestCase):
    def test_zero_frame_count_collapses_range_to_zero(self) -> None:
        result = normalize([{"rect": {}, "start_frame": 5, "end_frame": 50}], {"width": 100, "height": 100, "frame_count": 0})
        self.assertEqual((result[0]["start_frame"], result[0]["end_frame"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
