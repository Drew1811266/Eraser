"""LaMa inpainting over ONNX Runtime.

Why ONNX instead of the reference torch path:

- torch(CUDA) is ~2.5 GB of wheel; onnxruntime is ~60 MB and needs no matching
  driver/toolkit, which removes the single largest Windows packaging risk.
- The reference repository had already proven LaMa works through ONNX in its
  TypeScript rewrite (``inpaintOnnx.ts``), so this is not a new bet.

The public surface mirrors the reference ``WatermarkRemover`` (``is_loaded`` /
``load_model`` / ``inpaint``) so the ported pipeline calls it unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from engine.pipeline.logging import logger
from engine.platform import ort_runtime
from engine.platform.paths import application_root, models_dir

ENV_MODEL_PATH = "ERASER_LAMA_ONNX"
ENV_ORT_PROVIDER = "ERASER_ORT_PROVIDER"
MODEL_FILENAME = "big-lama.onnx"
MODEL_RELATIVE_DIR = ("models", "big-lama", "1.0.0")

# Preference order. Measured on this machine (RTX 5090 D v2, 512x512 LaMa):
# CUDA 47 ms/frame, CPU (8 threads) 657 ms/frame, so CUDA is worth its weight.
# DirectML is deliberately absent: it initialises but fails on this model with
# `MatMul ... E_INVALIDARG`, which silently yields the unmodified frame.
PROVIDER_PREFERENCE = ("CUDAExecutionProvider", "CPUExecutionProvider")

# Logical threads for the CPU provider. Measured 1 -> 2.28 s, 2 -> 1.42 s,
# 4 -> 0.89 s, 8 -> 0.66 s, 16 -> 0.94 s per 512x512 frame, so 8 is the knee.
DEFAULT_CPU_THREADS = 8

# LaMa's encoder downsamples by 8; padding to 32 additionally keeps the
# intermediate feature maps comfortably aligned for every export variant.
PAD_MODULE = 32


class ModelNotFound(RuntimeError):
    """Raised when the ONNX weights are not where we expect them."""


def default_model_path() -> Path:
    override = os.environ.get(ENV_MODEL_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(application_root(), *MODEL_RELATIVE_DIR, MODEL_FILENAME)


def candidate_model_paths() -> list[Path]:
    paths = [default_model_path(), models_dir() / "big-lama" / "1.0.0" / MODEL_FILENAME]
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
    raise ModelNotFound(f"LaMa ONNX model not found. Searched:\n  {searched}")


def _align(value: int, module: int) -> int:
    remainder = value % module
    return value if remainder == 0 else value + module - remainder


class WatermarkRemover:
    """Single-image LaMa inpainting. Interface-compatible with the reference."""

    def __init__(
        self,
        model_path: str | os.PathLike[str] | None = None,
        device: Any = None,
        use_fp16: bool = False,
    ) -> None:
        self._explicit_path = Path(model_path) if model_path else None
        self.device = device
        self.use_fp16 = use_fp16
        self._session: Any = None
        self._input_names: tuple[str, str] | None = None
        self._fixed_size: tuple[int, int] | None = None

    # -- lifecycle ---------------------------------------------------------
    def is_loaded(self) -> bool:
        return self._session is not None

    def load_model(self) -> None:
        if self._session is not None:
            return

        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - environment issue
            raise RuntimeError(
                "onnxruntime is not installed; run: pip install onnxruntime"
            ) from exc

        path = self._explicit_path or resolve_model_path()
        if not Path(path).is_file():
            raise ModelNotFound(f"LaMa ONNX model not found at {path}")

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Errors only. ONNX Runtime logs graph-rewrite trivia to stderr, and
        # stderr is where the engine's own structured log lives.
        options.log_severity_level = 3

        logger.info("Loading LaMa ONNX model: %s", path)
        providers = self._select_providers(ort)
        options.intra_op_num_threads = self._resolve_threads(providers)
        logger.info(
            "ONNX Runtime providers: %s (intra_op threads: %d)",
            providers,
            options.intra_op_num_threads,
        )

        self._session = ort.InferenceSession(str(path), sess_options=options, providers=providers)
        self._provider = self._session.get_providers()[0]
        if self._provider != providers[0]:
            # ONNX Runtime falls back silently; say it out loud so a support
            # report does not have to guess which device ran the job.
            logger.warning(
                "Requested %s but the session is using %s", providers[0], self._provider
            )
        logger.info("Active execution provider: %s", self._provider)
        self._input_names = self._discover_inputs()
        self._fixed_size = self._discover_fixed_size()

    @property
    def execution_provider(self) -> str | None:
        """The provider that is actually running inference, if loaded."""
        return self._provider

    @staticmethod
    def _resolve_threads(providers: list[str]) -> int:
        """Thread count for the CPU provider; the GPU path leaves it at 1."""
        raw = os.environ.get("ERASER_ORT_THREADS")
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                logger.warning("Ignoring invalid ERASER_ORT_THREADS=%r", raw)
        if providers and providers[0] != "CPUExecutionProvider":
            return 1
        return max(1, min(DEFAULT_CPU_THREADS, os.cpu_count() or 1))

    @staticmethod
    def _select_providers(ort: Any) -> list[str]:
        """Pick execution providers, honouring ERASER_ORT_PROVIDER when set."""
        available = set(ort.get_available_providers())
        requested = os.environ.get(ENV_ORT_PROVIDER, "auto").strip().lower()

        # A CUDA wheel is not enough: without the cuBLAS/cuDNN DLLs the provider
        # loads and then quietly runs on the CPU at GPU-session overhead.
        if "CUDAExecutionProvider" in available and not ort_runtime.cuda_usable():
            available.discard("CUDAExecutionProvider")

        wanted = {
            "cpu": "CPUExecutionProvider",
            "cuda": "CUDAExecutionProvider",
        }.get(requested)
        if wanted is not None:
            if wanted in available:
                return [wanted, "CPUExecutionProvider"] if wanted != "CPUExecutionProvider" else [wanted]
            logger.warning("Requested provider %s is unavailable; falling back", requested)

        selected = [name for name in PROVIDER_PREFERENCE if name in available]
        return selected or ["CPUExecutionProvider"]

    def _discover_fixed_size(self) -> tuple[int, int] | None:
        """Return the model's fixed (width, height), or None when dynamic.

        HuggingFace LaMa exports are commonly locked to 512x512. Treating such a
        model as dynamic produces an ONNXRuntime INVALID_ARGUMENT on the very
        first frame, so this detection decides which inference path runs.
        """
        assert self._session is not None
        image_name, _ = self._input_names  # type: ignore[misc]
        for item in self._session.get_inputs():
            if item.name != image_name:
                continue
            shape = getattr(item, "shape", None) or []
            if len(shape) != 4:
                return None
            height, width = shape[2], shape[3]
            if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                return (width, height)
            return None
        return None

    def _discover_inputs(self) -> tuple[str, str]:
        assert self._session is not None
        inputs = self._session.get_inputs()
        if len(inputs) < 2:
            raise RuntimeError("LaMa ONNX model must expose an image and a mask input")

        image_name = inputs[0].name
        mask_name = inputs[1].name
        for item in inputs:
            shape = getattr(item, "shape", None) or []
            # NCHW with 3 channels is the image, 1 channel is the mask.
            if len(shape) == 4 and shape[1] == 3:
                image_name = item.name
            elif len(shape) == 4 and shape[1] == 1:
                mask_name = item.name

        if image_name == mask_name:
            mask_name = next(item.name for item in inputs if item.name != image_name)
        return image_name, mask_name

    # -- inference ---------------------------------------------------------
    def inpaint(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Inpaint masked pixels of a BGR uint8 image.

        Returns a BGR uint8 image of the same shape. On failure the original
        image is returned: a single bad frame must not abort a whole video.
        """
        if image is None or mask is None:
            return image
        if not self.is_loaded():
            self.load_model()

        try:
            return self._inpaint_impl(image, mask)
        except Exception as exc:  # noqa: BLE001 - frame-level resilience is intentional
            logger.error("Inpainting failed: %s", exc)
            return image

    def _inpaint_impl(self, image: np.ndarray, mask: np.ndarray) -> np.ndarray:
        bgr = image if image.ndim == 3 else np.stack([image] * 3, axis=-1)
        height, width = bgr.shape[:2]

        binary = self._binarize_mask(mask, width, height)
        if not binary.any():
            return image

        if self._fixed_size is not None:
            return self._inpaint_fixed(bgr, binary, self._fixed_size)
        return self._inpaint_dynamic(bgr, binary)

    def _inpaint_fixed(
        self,
        bgr: np.ndarray,
        binary: np.ndarray,
        target: tuple[int, int],
    ) -> np.ndarray:
        """Fit the crop into the model's fixed canvas, then warp the result back.

        Two rules learned from measurement, not assumption:

        - Never upscale. Scaling a small ROI up to 512 introduces interpolation
          ringing around the mask edge, and LaMa faithfully reconstructs that
          ringing as if it were real content (measured: a uniform region drifted
          from (200,30,30) to (177,21,55)). At native scale the same case comes
          back at (209,33,28).
        - Keep the aspect ratio and pad rather than stretch, centred, so the
          model sees the region in a plausible context.
        """
        import cv2

        target_w, target_h = target
        height, width = bgr.shape[:2]

        scale = min(1.0, target_w / width, target_h / height)
        new_w = max(1, min(target_w, round(width * scale)))
        new_h = max(1, min(target_h, round(height * scale)))

        if new_w == width and new_h == height:
            resized = bgr
            resized_mask = binary
        else:
            resized = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            resized_mask = cv2.resize(binary, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2

        rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
        image_tensor = np.zeros((1, 3, target_h, target_w), dtype=np.float32)
        image_tensor[0, :, offset_y : offset_y + new_h, offset_x : offset_x + new_w] = np.transpose(
            rgb, (2, 0, 1)
        )

        mask_tensor = np.zeros((1, 1, target_h, target_w), dtype=np.float32)
        mask_tensor[0, 0, offset_y : offset_y + new_h, offset_x : offset_x + new_w] = resized_mask.astype(
            np.float32
        )

        output = self._run(image_tensor, mask_tensor)
        cropped = output[offset_y : offset_y + new_h, offset_x : offset_x + new_w]

        if new_w != width or new_h != height:
            cropped = cv2.resize(cropped, (width, height), interpolation=cv2.INTER_LINEAR)

        alpha = binary.astype(np.float32)[:, :, None]
        blended = cropped.astype(np.float32) * alpha + bgr.astype(np.float32) * (1.0 - alpha)
        return np.clip(blended, 0, 255).astype(np.uint8)

    def _inpaint_dynamic(self, bgr: np.ndarray, binary: np.ndarray) -> np.ndarray:
        height, width = bgr.shape[:2]
        padded_h = _align(height, PAD_MODULE)
        padded_w = _align(width, PAD_MODULE)

        rgb = bgr[:, :, ::-1].astype(np.float32) / 255.0
        image_tensor = np.zeros((1, 3, padded_h, padded_w), dtype=np.float32)
        image_tensor[0, :, :height, :width] = np.transpose(rgb, (2, 0, 1))

        mask_tensor = np.zeros((1, 1, padded_h, padded_w), dtype=np.float32)
        mask_tensor[0, 0, :height, :width] = binary.astype(np.float32)

        output = self._run(image_tensor, mask_tensor)[:height, :width]
        alpha = binary.astype(np.float32)[:, :, None]
        blended = output.astype(np.float32) * alpha + bgr.astype(np.float32) * (1.0 - alpha)
        return np.clip(blended, 0, 255).astype(np.uint8)

    def _run(self, image_tensor: np.ndarray, mask_tensor: np.ndarray) -> np.ndarray:
        """Execute the session and return a BGR uint8 image of the tensor size."""
        assert self._session is not None and self._input_names is not None
        image_name, mask_name = self._input_names

        outputs = self._session.run(None, {image_name: image_tensor, mask_name: mask_tensor})
        if not outputs:
            raise RuntimeError("LaMa ONNX model returned no outputs")

        result = np.asarray(outputs[0])[0]
        # Exporters disagree on the output scale: some emit 0..1, others 0..255.
        # Assuming 0..1 when the model emits 0..255 clips everything to white.
        peak = float(result.max()) if result.size else 0.0
        if peak > 2.0:
            scaled = np.clip(result, 0.0, 255.0)
        else:
            scaled = np.clip(result, 0.0, 1.0) * 255.0

        rgb = scaled.round().astype(np.uint8).transpose(1, 2, 0)
        return np.ascontiguousarray(rgb[:, :, ::-1])

    @staticmethod
    def _binarize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
        array = np.asarray(mask)
        if array.ndim == 3:
            array = array[:, :, 0]

        threshold = 127 if array.dtype == np.uint8 and array.max(initial=0) > 1 else 0
        binary = (array > threshold).astype(np.uint8)

        if binary.shape[:2] != (height, width):
            import cv2

            binary = cv2.resize(binary, (width, height), interpolation=cv2.INTER_NEAREST)
        return binary

    # -- batch -------------------------------------------------------------
    def inpaint_batch(
        self,
        images: Iterable[np.ndarray],
        masks: Iterable[np.ndarray],
        progress_callback: Any = None,
    ) -> list[np.ndarray]:
        images_list = list(images)
        masks_list = list(masks)
        total = min(len(images_list), len(masks_list))
        results: list[np.ndarray] = []

        for index in range(total):
            results.append(self.inpaint(images_list[index], masks_list[index]))
            if progress_callback and (index + 1 == total or (index + 1) % 2 == 0):
                progress_callback((index + 1) / max(1, total))
        return results
