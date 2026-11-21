"""A detector that replays a fixed set of boxes.

It exists so the analysis pipeline can be exercised end to end (tests, CLI,
demo) before a real vision model is wired in, and so a bug report can carry an
exact detection list instead of "it looked wrong".
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect.base import WatermarkDetector


def load_detections(path: str) -> dict[int, list[Detection]]:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"detection script not found: {path}")
    payload = json.loads(file.read_text(encoding="utf-8"))
    raw_frames = payload.get("frames", payload) if isinstance(payload, dict) else {}
    detections: dict[int, list[Detection]] = {}
    for key, entries in raw_frames.items():
        try:
            frame_index = int(key)
        except (TypeError, ValueError):
            continue
        items: list[Detection] = []
        for entry in entries or []:
            data = entry if isinstance(entry, dict) else {}
            items.append(
                Detection(
                    frame=frame_index,
                    rect=BBox.from_dict(data),
                    confidence=float(data.get("confidence", 1.0) or 1.0),
                )
            )
        if items:
            detections[frame_index] = items
    return detections


class ScriptedDetector(WatermarkDetector):
    model_id = "scripted"
    display_name = "Scripted detections"

    def __init__(self, detections: dict[int, list[Detection]]) -> None:
        self._detections = dict(detections)

    def load(self) -> None:
        return None

    def detect(self, frame: np.ndarray | None, frame_index: int) -> list[Detection]:
        return list(self._detections.get(int(frame_index), ()))
