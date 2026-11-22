"""Detector catalog.

A detector is chosen here, never in the UI: the app asks what can run and picks
the first available one. Florence is always listed so the interface can offer to
install it; the scripted detector exists only when a developer asks for it.
"""

from __future__ import annotations

import os
from typing import Mapping

from engine.pipeline.detect.base import DetectorError, WatermarkDetector
from engine.pipeline.detect.florence import FlorenceDetector
from engine.pipeline.detect.stub import ScriptedDetector, load_detections

ENV_DETECT_STUB = "ERASER_DETECT_STUB"

__all__ = [
    "DetectorError",
    "WatermarkDetector",
    "FlorenceDetector",
    "ScriptedDetector",
    "load_detections",
    "catalog",
    "available_detectors",
]


def _scripted(environ: Mapping[str, str]) -> WatermarkDetector | None:
    path = (environ.get(ENV_DETECT_STUB) or "").strip()
    if not path:
        return None
    try:
        return ScriptedDetector(load_detections(path))
    except (OSError, ValueError, TypeError, AttributeError):
        # A broken script must read as "no detector available", not as an
        # internal error escaping through the RPC layer.
        return None


def catalog(environ: Mapping[str, str] | None = None) -> list[WatermarkDetector]:
    """Everything this build knows about, installed or not."""
    env = os.environ if environ is None else environ
    detectors: list[WatermarkDetector] = [FlorenceDetector()]
    scripted = _scripted(env)
    if scripted is not None:
        detectors.append(scripted)
    return detectors


def _usable(detector: WatermarkDetector) -> bool:
    try:
        return bool(detector.is_available())
    except Exception:  # noqa: BLE001 - a broken probe is not an availability
        return False


def available_detectors(environ: Mapping[str, str] | None = None) -> list[WatermarkDetector]:
    """Detectors that can run right now, best first."""
    return [detector for detector in catalog(environ) if _usable(detector)]
