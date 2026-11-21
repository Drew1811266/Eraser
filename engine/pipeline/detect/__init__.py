"""Detector registry.

Only the scripted detector exists in this milestone. A real one is added by
registering another instance here; nothing else in the codebase changes.
"""

from __future__ import annotations

import os
from typing import Mapping

from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.detect.stub import ScriptedDetector, load_detections

ENV_DETECT_STUB = "ERASER_DETECT_STUB"

__all__ = [
    "WatermarkDetector",
    "ScriptedDetector",
    "load_detections",
    "available_detectors",
]


def available_detectors(environ: Mapping[str, str] | None = None) -> list[WatermarkDetector]:
    """Detectors this build can run right now.

    `ERASER_DETECT_STUB` is a deliberate test/demo hook — the same pattern as
    `ERASER_STDERR_NOISE` — and is documented as such in docs/09.
    """
    env = os.environ if environ is None else environ
    path = (env.get(ENV_DETECT_STUB) or "").strip()
    if not path:
        return []
    try:
        return [ScriptedDetector(load_detections(path))]
    except (OSError, ValueError):
        return []
