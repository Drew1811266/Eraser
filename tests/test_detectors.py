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

    def test_zero_confidence_is_preserved(self) -> None:
        # `confidence or 1.0` would turn a box the model was sure about *not*
        # being a watermark into the most confident one.
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "d.json"
            path.write_text(
                json.dumps(
                    {"frames": {"0": [{"x": 1, "y": 2, "width": 3, "height": 4, "confidence": 0}]}}
                ),
                encoding="utf-8",
            )
            detections = load_detections(str(path))
        self.assertEqual(detections[0][0].confidence, 0.0)

    def test_frames_must_be_a_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "d.json"
            path.write_text(json.dumps({"frames": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_detections(str(path))

    def test_a_broken_script_reads_as_no_detector(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "d.json"
            path.write_text(json.dumps({"frames": []}), encoding="utf-8")
            self.assertEqual(available_detectors({"ERASER_DETECT_STUB": str(path)}), [])


class BatchTests(unittest.TestCase):
    def test_default_batch_calls_detect_per_frame(self) -> None:
        class Counter(ScriptedDetector):
            calls: list[int] = []

            def detect(self, frame, frame_index):  # noqa: ANN001, ANN201
                Counter.calls.append(frame_index)
                return super().detect(frame, frame_index)

        detector = Counter({0: [Detection(0, BBox(1, 1, 2, 2), 1.0)]})
        result = detector.detect_many([(0, None), (5, None)])
        self.assertEqual(Counter.calls, [0, 5])
        self.assertEqual(sorted(result), [0, 5])
        self.assertEqual(result[0][0].rect, BBox(1, 1, 2, 2))

    def test_batch_of_no_frames_is_empty(self) -> None:
        self.assertEqual(ScriptedDetector({}).detect_many([]), {})


class RegistryTests(unittest.TestCase):
    def test_no_detector_by_default(self) -> None:
        self.assertEqual(available_detectors(environ={}), [])


if __name__ == "__main__":
    unittest.main()
