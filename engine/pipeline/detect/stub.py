"""A detector that replays a fixed set of boxes.

It exists so the analysis pipeline can be exercised end to end (tests, CLI,
demo) before a real vision model is wired in, and so a bug report can carry an
exact detection list instead of "it looked wrong".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect.base import WatermarkDetector


def _confidence(raw: Any) -> float:
    """`0` is a real confidence and must not be promoted by `or`."""
    if raw is None:
        return 1.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def load_detections(path: str) -> dict[int, list[Detection]]:
    """Read a detection script.

    Boxes are in the coordinate space of the frame the detector receives, which
    `frames.sample_frames` may have downscaled; `analyze_job` maps them back to
    source pixels. A script written against the original video would be scaled
    twice on a clip wider than the analysis ceiling.
    """
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"detection script not found: {path}")
    payload = json.loads(file.read_text(encoding="utf-8"))
    raw_frames = payload.get("frames", payload) if isinstance(payload, dict) else {}
    if not isinstance(raw_frames, dict):
        raise ValueError("detection script must map frame numbers to boxes")
    detections: dict[int, list[Detection]] = {}
    for key, entries in raw_frames.items():
        try:
            frame_index = int(key)
        except (TypeError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        items: list[Detection] = []
        for entry in entries:
            data = entry if isinstance(entry, dict) else {}
            items.append(
                Detection(
                    frame=frame_index,
                    rect=BBox.from_dict(data),
                    confidence=_confidence(data.get("confidence")),
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
