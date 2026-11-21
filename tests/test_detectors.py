"""Detector plumbing: the seam a real model will plug into later."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect import available_detectors
from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.detect.stub import ScriptedDetector, load_detections


class ScriptedDetectorTests(unittest.TestCase):
    def test_returns_scripted_detections(self) -> None:
        detector = ScriptedDetector({0: [Detection(0, BBox(1, 2, 3, 4), 0.9)]})
        found = detector.detect(frame=None, frame_index=0)  # noqa: ANN001
        self.assertEqual(found[0].rect, BBox(1, 2, 3, 4))

    def test_unscripted_frame_is_empty(self) -> None:
        detector = ScriptedDetector({})
        self.assertEqual(detector.detect(None, 7), [])

    def test_load_requires_an_existing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_detections("/nope/missing.json")

    def test_load_reads_frame_map(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "detections.json"
            path.write_text(
                json.dumps(
                    {
                        "frames": {
                            "0": [
                                {"x": 5, "y": 6, "width": 7, "height": 8, "confidence": 0.5}
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            detections = load_detections(str(path))
        self.assertEqual(detections[0][0].rect, BBox(5, 6, 7, 8))
        self.assertAlmostEqual(detections[0][0].confidence, 0.5)

    def test_abstract_contract_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            WatermarkDetector()  # type: ignore[abstract]


class RegistryTests(unittest.TestCase):
    def test_no_detector_by_default(self) -> None:
        self.assertEqual(available_detectors(environ={}), [])


if __name__ == "__main__":
    unittest.main()
