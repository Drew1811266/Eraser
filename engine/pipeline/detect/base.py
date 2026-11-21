"""The detector seam.

A detector answers one question per frame: is a watermark visible, and where.
Everything downstream (association, tracks, the UI) is detector-agnostic, so a
new model is a new subclass here and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from engine.pipeline.analysis import Detection


class WatermarkDetector(ABC):
    model_id: str = "base"
    display_name: str = "Base detector"

    @abstractmethod
    def load(self) -> None:
        """Make the model ready; may download/initialise. Must be idempotent."""

    @abstractmethod
    def detect(self, frame: np.ndarray | None, frame_index: int) -> list[Detection]:
        """Return watermark candidates for one frame. Never raises for a bad frame."""

    def is_available(self) -> bool:
        return True

    def get_warning(self) -> str:
        return ""
