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

    def detect_many(
        self, frames: list[tuple[int, np.ndarray]]
    ) -> dict[int, list[Detection]]:
        """Detect a batch of frames.

        Overridden by models that want the whole batch in one call (a runtime
        worker is one process start, not one per frame). The default keeps every
        simple detector working unchanged.
        """
        return {index: self.detect(image, index) for index, image in frames}

    def is_available(self) -> bool:
        return True

    def get_warning(self) -> str:
        return ""
