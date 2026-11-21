"""Upscale planning: the arithmetic that decides what gets produced.

These are the decisions that look obvious and are not: what "1080p" means for a
360p source, what happens when the source is already 1080p, and how a long shot
with no cuts is split. Getting them wrong produces a file with the wrong size
and no error anywhere.
"""

from __future__ import annotations

import unittest

from engine.pipeline.upscale_plan import (
    ConfigError,
    UpscaleConfig,
    build_segments,
    plan_output_name,
    resolve_plan,
)

from pathlib import Path


class ConfigParsingTests(unittest.TestCase):
    def test_defaults_match_what_the_ui_sends(self) -> None:
        config = UpscaleConfig.from_params({})
        self.assertEqual(config.mode, "upscale_resolution")
        self.assertEqual(config.target_preset, "1080p")
        self.assertTrue(config.keep_audio)
        # Scene splitting is automatic in the reference, not a user toggle.
        self.assertEqual(config.scene_split, "rule")

    def test_rejects_unknown_modes_and_presets(self) -> None:
        for params in ({"mode": "zoom"}, {"target_preset": "8k"}, {"scene_split": "vibes"}):
            with self.assertRaises(ConfigError):
                UpscaleConfig.from_params(params)

    def test_denoise_is_clamped_to_the_documented_range(self) -> None:
        with self.assertRaises(ConfigError):
            UpscaleConfig.from_params({"denoise_strength": 1.5})
        with self.assertRaises(ConfigError):
            UpscaleConfig.from_params({"denoise_strength": "loud"})

    def test_a_negligible_denoise_is_treated_as_off(self) -> None:
        self.assertEqual(UpscaleConfig.from_params({"denoise_strength": 0.001}).denoise_strength, 0.0)
        self.assertEqual(UpscaleConfig.from_params({"denoise_strength": 0.4}).denoise_strength, 0.4)


class PlanTests(unittest.TestCase):
    def plan(self, params: dict, width: int, height: int):
        return resolve_plan(UpscaleConfig.from_params(params), width, height)

    def test_landscape_source_reaches_the_preset(self) -> None:
        plan = self.plan({}, 640, 360)
        self.assertEqual((plan.target_width, plan.target_height), (1920, 1080))
        self.assertFalse(plan.same_resolution)

    def test_width_follows_the_aspect_ratio_and_stays_even(self) -> None:
        plan = self.plan({"target_preset": "1080p"}, 854, 480)
        self.assertEqual(plan.target_height, 1080)
        self.assertEqual(plan.target_width % 2, 0)
        # 854/480 * 1080 = 1921.5 -> 1922, not a stretched 1920.
        self.assertEqual(plan.target_width, 1922)

    def test_a_source_at_the_preset_is_enhanced_not_enlarged(self) -> None:
        plan = self.plan({}, 1920, 1080)
        self.assertTrue(plan.same_resolution)
        self.assertEqual((plan.target_width, plan.target_height), (1920, 1080))

    def test_a_portrait_source_is_measured_by_height(self) -> None:
        # 1080x1920 already exceeds the 1080p preset on height, so it is enhanced
        # at its own size instead of being blown up for no reason.
        plan = self.plan({}, 1080, 1920)
        self.assertTrue(plan.same_resolution)

    def test_same_resolution_mode_always_returns_to_the_source_size(self) -> None:
        plan = self.plan({"mode": "enhance_same_resolution"}, 1280, 720)
        self.assertTrue(plan.same_resolution)
        self.assertEqual((plan.target_width, plan.target_height), (1280, 720))
        self.assertEqual(plan.scale, 4)

    def test_an_empty_frame_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            self.plan({}, 0, 0)

    def test_output_names_say_what_was_done(self) -> None:
        source = Path("C:/v/clip.mp4")
        self.assertEqual(plan_output_name(source, self.plan({}, 640, 360)), "clip_1080p.mp4")
        self.assertEqual(
            plan_output_name(source, self.plan({"mode": "enhance_same_resolution"}, 640, 360)),
            "clip_enhanced.mp4",
        )


class SegmentTests(unittest.TestCase):
    def test_no_cuts_is_one_segment(self) -> None:
        self.assertEqual(build_segments(4.0, []), [(0.0, 4.0)])

    def test_cuts_become_contiguous_segments(self) -> None:
        self.assertEqual(build_segments(10.0, [3.0, 7.5]),
                         [(0.0, 3.0), (3.0, 4.5), (7.5, 2.5)])

    def test_cuts_at_the_edges_and_duplicates_are_dropped(self) -> None:
        self.assertEqual(build_segments(5.0, [0.0, 5.0, 2.0, 2.01]), [(0.0, 2.0), (2.0, 3.0)])

    def test_hybrid_cuts_long_shots_that_rule_leaves_alone(self) -> None:
        rule = build_segments(50.0, [10.0])
        hybrid = build_segments(50.0, [10.0], max_length=20.0)
        self.assertEqual(rule, [(0.0, 10.0), (10.0, 40.0)])
        self.assertEqual(hybrid, [(0.0, 10.0), (10.0, 20.0), (30.0, 20.0)])

    def test_hybrid_leaves_a_short_clip_as_one_segment(self) -> None:
        self.assertEqual(build_segments(5.0, [], max_length=20.0), [(0.0, 5.0)])

    def test_a_zero_length_video_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            build_segments(0.0, [])


if __name__ == "__main__":
    unittest.main()
