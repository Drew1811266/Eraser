"""SeedVR2: an optional diffusion upscaler with its own runtime.

The engine ships as a frozen binary with onnxruntime; SeedVR2 needs PyTorch and
a multi-gigabyte model, so it lives in a separate managed runtime that is
installed on demand (see `manifest` for what, `capability` for whether, and
`runtime` for how).
"""

from engine.pipeline.seedvr.capability import Availability, check, describe

__all__ = ["Availability", "check", "describe"]
