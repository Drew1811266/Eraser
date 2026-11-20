"""Real-ESRGAN x4 upscaling, on the ONNX Runtime we already ship.

The reference implementation treats upscaling as a separate product: a second
Python 3.12 virtualenv, torch, and a worker subprocess. This port treats it as
one more ONNX model — the same runtime, the same provider selection, the same
cancellation story — because carrying torch on Windows would double the
installer and reintroduce everything ADR-0001 moved away from.

The model is fully convolutional, so a frame can be upscaled in tiles. Tiles
overlap: each one is fed a margin of neighbouring pixels and the margin is
cropped off the result, which keeps seams invisible without any blending pass.

    packaging/convert-esrgan.py builds the .onnx from the official weights.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from engine.pipeline.logging import logger
from engine.platform import ort_runtime
from engine.platform.paths import application_root, models_dir

SCALE = 4
MODEL_FILENAME = "real-esrgan-x4plus.onnx"
MODEL_RELATIVE_DIR = ("models", "real-esrgan", "1.0.0")
ENV_MODEL_PATH = "ERASER_ESRGAN_ONNX"
ENV_TILE = "ERASER_ESRGAN_TILE"

# 256 px tiles keep peak memory modest (the network holds 64 channels at full
# resolution) while still giving the GPU enough work per launch.
DEFAULT_TILE = 256
DEFAULT_MARGIN = 24


class UpscaleModelNotFound(RuntimeError):
    """Raised when the Real-ESRGAN weights are not installed."""


def default_model_path() -> Path:
    return application_root().joinpath(*MODEL_RELATIVE_DIR, MODEL_FILENAME)


def candidate_model_paths() -> list[Path]:
    env = os.environ.get(ENV_MODEL_PATH, "").strip()
    paths = []
    if env:
        paths.append(Path(env).expanduser())
    paths.append(default_model_path())
    paths.append(models_dir().joinpath(*MODEL_RELATIVE_DIR[1:], MODEL_FILENAME))
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def resolve_model_path() -> Path:
    for path in candidate_model_paths():
        if path.is_file() and path.stat().st_size > 0:
            return path
    searched = "\n  ".join(str(path) for path in candidate_model_paths())
    raise UpscaleModelNotFound(f"Real-ESRGAN ONNX model not found. Searched:\n  {searched}")


def is_installed() -> bool:
    try:
        resolve_model_path()
    except UpscaleModelNotFound:
        return False
    return True


class RealEsrganUpscaler:
    """4x upscaler over ONNX Runtime, tiled and interruptible."""

    def __init__(self, model_path: str | os.PathLike[str] | None = None) -> None:
        self._explicit_path = Path(model_path) if model_path else None
        self._session: Any = None
        self._tile = _positive_int(os.environ.get(ENV_TILE), DEFAULT_TILE)
        self._provider: str | None = None

    @property
    def execution_provider(self) -> str | None:
        return self._provider

    def is_loaded(self) -> bool:
        return self._session is not None

    def load_model(self) -> None:
        if self._session is not None:
            return

        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - environment issue
            raise RuntimeError("onnxruntime is not installed") from exc

        path = self._explicit_path or resolve_model_path()
        if not Path(path).is_file():
            raise UpscaleModelNotFound(f"Real-ESRGAN ONNX model not found at {path}")

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3

        providers = [name for name in _provider_preference(ort) if name in set(ort.get_available_providers())]
        if "CUDAExecutionProvider" in providers and not ort_runtime.cuda_usable():
            providers = [name for name in providers if name != "CUDAExecutionProvider"]
        if not providers:
            providers = ["CPUExecutionProvider"]

        threads = _cpu_threads() if providers[0] == "CPUExecutionProvider" else 1
        options.intra_op_num_threads = threads

        logger.info("Loading Real-ESRGAN ONNX model: %s", path)
        self._session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
        self._provider = self._session.get_providers()[0]
        logger.info("Upscaler provider: %s (threads %d)", self._provider, threads)

    def upscale(self, image: np.ndarray, cancel: Any = None) -> np.ndarray:
        """Upscale an RGB uint8 HxWx3 image by `SCALE`."""
        self.load_model()
        assert self._session is not None
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"expected an RGB image, got shape {image.shape}")

        height, width = image.shape[:2]
        output = np.zeros((height * SCALE, width * SCALE, 3), dtype=np.uint8)
        margin = DEFAULT_MARGIN

        for top in range(0, height, self._tile):
            if cancel is not None and cancel.is_set():
                raise _Cancelled()
            for left in range(0, width, self._tile):
                bottom = min(top + self._tile, height)
                right = min(left + self._tile, width)

                # Context from the neighbours, so the convolution does not see a
                # hard border at the tile edge.
                pad_top = max(0, top - margin)
                pad_left = max(0, left - margin)
                pad_bottom = min(height, bottom + margin)
                pad_right = min(width, right + margin)

                patch = image[pad_top:pad_bottom, pad_left:pad_right]
                upscaled = self._run(patch)

                offset_top = (top - pad_top) * SCALE
                offset_left = (left - pad_left) * SCALE
                output[top * SCALE : bottom * SCALE, left * SCALE : right * SCALE] = upscaled[
                    offset_top : offset_top + (bottom - top) * SCALE,
                    offset_left : offset_left + (right - left) * SCALE,
                ]

        return output

    def _run(self, patch: np.ndarray) -> np.ndarray:
        assert self._session is not None
        tensor = patch.astype(np.float32) / 255.0
        tensor = np.ascontiguousarray(np.transpose(tensor, (2, 0, 1))[None, ...])
        inputs = self._session.get_inputs()
        name = inputs[0].name if inputs else "input"
        raw = self._session.run(None, {name: tensor})[0]
        # The network is unbounded, so clamp rather than trusting the range.
        return np.clip(raw[0].transpose(1, 2, 0) * 255.0, 0, 255).astype(np.uint8)


class _Cancelled(RuntimeError):
    """Internal: raised when the caller's cancel event fires mid-frame."""


def _provider_preference(ort: Any) -> list[str]:
    return ["CUDAExecutionProvider", "CPUExecutionProvider"]


def _positive_int(raw: str | None, fallback: int) -> int:
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid ERASER_ESRGAN_TILE=%r", raw)
        return fallback
    return value if value > 0 else fallback


def _cpu_threads() -> int:
    raw = os.environ.get("ERASER_ORT_THREADS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, min(8, os.cpu_count() or 1))
