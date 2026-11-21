"""Pins the registry behaviour the analysis milestone depends on.

The ported core (watermark/remover/registry) has no unit coverage today; the
only guard is the end-to-end suite, which cannot say *which* rule broke.
"""

from __future__ import annotations

import unittest

from engine.pipeline.model_registry import (
    LEGACY_MODEL_ALIASES,
    SUPPORTED_MODEL_IDS,
    BaseInpaintEngine,
    ModelRegistry,
)


class _FakeRemover:
    """Stands in for WatermarkRemover; never loads a real model.

    `fail_once` matters: `ModelRegistry.resolve` retries the same engine after a
    load failure to reach the fallback, so a fake that always raises would make
    the fallback branch itself explode and hide the behaviour under test.
    """

    def __init__(self, fail_once: bool = False) -> None:
        self._failures_left = 1 if fail_once else 0
        self.loaded = False

    def is_loaded(self) -> bool:
        return self.loaded

    def load_model(self) -> None:
        if self._failures_left > 0:
            self._failures_left -= 1
            raise RuntimeError("boom")
        self.loaded = True

    def inpaint(self, image, mask):  # noqa: ANN001, ANN201 - test double
        return image


class RegistryTests(unittest.TestCase):
    def test_default_model_is_lama(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id(None), "lama_roi")

    def test_unknown_model_falls_back_to_lama(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id("nope"), "lama_roi")

    def test_legacy_alias_is_rewritten(self) -> None:
        self.assertEqual(ModelRegistry.normalize_model_id("sttn_roi"), "lama_roi")

    def test_supported_ids_are_lowercase(self) -> None:
        for model_id in SUPPORTED_MODEL_IDS:
            self.assertEqual(model_id, model_id.lower())

    def test_alias_targets_are_supported(self) -> None:
        for target in LEGACY_MODEL_ALIASES.values():
            self.assertIn(target, SUPPORTED_MODEL_IDS)

    def test_engine_is_reused_for_the_same_id(self) -> None:
        registry = ModelRegistry(_FakeRemover())
        first, _ = registry.resolve("lama_roi")
        second, _ = registry.resolve("lama_roi")
        self.assertIs(first, second)

    def test_load_failure_falls_back_and_warns(self) -> None:
        registry = ModelRegistry(_FakeRemover(fail_once=True))
        engine, resolved = registry.resolve("lama_roi")
        self.assertIsInstance(engine, BaseInpaintEngine)
        self.assertEqual(resolved.requested_model_id, "lama_roi")
        self.assertEqual(resolved.effective_model_id, "lama_roi")
        self.assertIn("load failed", resolved.warning)


if __name__ == "__main__":
    unittest.main()
