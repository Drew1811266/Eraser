"""Tiling maths for the Real-ESRGAN stage.

The model itself is exercised end to end by `scripts/e2e-upscale.py`; what unit
tests can do cheaply is prove the *assembly* is right. A fake session that
returns a nearest-neighbour 4x enlargement has a property the real network also
has: the output pixel `(4y, 4x)` depends only on input pixel `(y, x)`. So if the
tile-and-crop logic is correct, the assembled frame must equal a single-pass
enlargement exactly — any seam or off-by-one shows up as a difference.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

import numpy as np

from engine.pipeline.upscaler import SCALE, RealEsrganUpscaler


class _FakeSession:
    """Nearest-neighbour 4x enlarger wearing an ONNX session's interface."""

    def __init__(self) -> None:
        self.calls = 0
        self.patch_sizes: list[tuple[int, int]] = []

    def get_inputs(self) -> list[types.SimpleNamespace]:
        return [types.SimpleNamespace(name="input")]

    def run(self, _outputs, feeds):  # noqa: ANN001, ANN201 - mirrors onnxruntime
        self.calls += 1
        patch = feeds["input"][0].transpose(1, 2, 0)
        self.patch_sizes.append((patch.shape[0], patch.shape[1]))
        enlarged = np.repeat(np.repeat(patch, SCALE, axis=0), SCALE, axis=1)
        return [enlarged.transpose(2, 0, 1)[None, ...]]


def _build(tile: int) -> tuple[RealEsrganUpscaler, _FakeSession]:
    upscaler = RealEsrganUpscaler(model_path="unused.onnx")
    session = _FakeSession()
    upscaler._session = session  # noqa: SLF001 - the point of the test
    upscaler._tile = tile  # noqa: SLF001
    return upscaler, session


class UpscalerTilingTests(unittest.TestCase):
    def test_output_is_four_times_larger(self) -> None:
        upscaler, _ = _build(tile=64)
        image = np.zeros((37, 53, 3), dtype=np.uint8)
        result = upscaler.upscale(image)
        self.assertEqual(result.shape, (37 * SCALE, 53 * SCALE, 3))
        self.assertEqual(result.dtype, np.uint8)

    def test_tiles_reassemble_into_the_single_pass_result(self) -> None:
        """No seams, no doubled or dropped pixels at tile boundaries."""
        rng = np.random.default_rng(3)
        image = rng.integers(0, 256, (40, 56, 3), dtype=np.uint8)
        expected = np.repeat(np.repeat(image, SCALE, axis=0), SCALE, axis=1)

        for tile in (16, 24, 56, 128):
            with self.subTest(tile=tile):
                upscaler, session = _build(tile=tile)
                result = upscaler.upscale(image)
                np.testing.assert_array_equal(result, expected)
                self.assertGreater(session.calls, 0)

    def test_every_frame_pixel_is_written(self) -> None:
        """A tiling bug that skips a band would leave zeros behind."""
        rng = np.random.default_rng(11)
        image = rng.integers(1, 256, (33, 45, 3), dtype=np.uint8)
        upscaler, _ = _build(tile=16)
        result = upscaler.upscale(image)
        self.assertFalse((result == 0).all(axis=2).any(), "some pixels were never written")

    def test_tiles_overlap_so_the_model_sees_context(self) -> None:
        """Each patch is fed its neighbours' pixels, not just its own tile."""
        upscaler, session = _build(tile=16)
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        upscaler.upscale(image)
        largest = max(height for height, _ in session.patch_sizes)
        self.assertGreater(largest, 16, "no margin was added around the tiles")

    def test_cancel_is_honoured_between_tiles(self) -> None:
        import threading

        upscaler, _ = _build(tile=16)
        image = np.zeros((64, 64, 3), dtype=np.uint8)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Exception):
            upscaler.upscale(image, cancel=cancel)

    def test_rejects_a_non_rgb_image(self) -> None:
        upscaler, _ = _build(tile=32)
        with self.assertRaises(ValueError):
            upscaler.upscale(np.zeros((10, 10), dtype=np.uint8))


class UpscalerPathTests(unittest.TestCase):
    def test_reports_missing_model_clearly(self) -> None:
        from engine.pipeline import upscaler as module

        with mock.patch.object(module, "candidate_model_paths", return_value=[__import__("pathlib").Path("nope.onnx")]):
            self.assertFalse(module.is_installed())
            with self.assertRaises(module.UpscaleModelNotFound):
                module.resolve_model_path()


if __name__ == "__main__":
    unittest.main()
