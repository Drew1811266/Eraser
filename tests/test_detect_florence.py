"""The Florence adapter: engine-side, torch-free, one subprocess per batch."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from engine.pipeline.analysis import BBox
from engine.pipeline.detect.base import DetectorError
from engine.pipeline.detect.florence import FlorenceDetector

SCRATCH = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"

FAKE_WORKER = """
import json, sys
job = json.loads(open(sys.argv[2], encoding="utf-8").read())
for item in job["images"]:
    print(json.dumps({"type": "frame", "frame": item["frame"],
                      "boxes": [[10, 20, 40, 60]]}), flush=True)
print(json.dumps({"type": "done"}), flush=True)
"""


def _frame() -> np.ndarray:
    return np.zeros((40, 80, 3), dtype=np.uint8)


class _Scratch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("ERASER_TEMP_DIR")
        os.environ["ERASER_TEMP_DIR"] = str(SCRATCH)
        if previous is None:
            cls.addClassCleanup(os.environ.pop, "ERASER_TEMP_DIR", None)
        else:
            cls.addClassCleanup(os.environ.__setitem__, "ERASER_TEMP_DIR", previous)

    def _detector(self, script: str, weights: Path | None = None) -> FlorenceDetector:
        scratch = Path(tempfile.mkdtemp(dir=SCRATCH))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        worker = scratch / "fake_worker.py"
        worker.write_text(script, encoding="utf-8")
        weights_dir = weights or scratch / "weights"
        weights_dir.mkdir(parents=True, exist_ok=True)
        (weights_dir / "model.safetensors").write_bytes(b"x")
        return FlorenceDetector(
            weights_dir=weights_dir, python=sys.executable, worker=worker
        )


class BatchCallTests(_Scratch):
    def test_boxes_come_back_in_sample_space(self) -> None:
        detector = self._detector(FAKE_WORKER)
        found = detector.detect_many([(0, _frame()), (15, _frame())])
        self.assertEqual(sorted(found), [0, 15])
        self.assertEqual(found[0][0].rect, BBox(10, 20, 30, 40))
        self.assertEqual(found[0][0].confidence, 1.0)

    def test_empty_batch_does_not_start_a_process(self) -> None:
        detector = self._detector(FAKE_WORKER)
        self.assertEqual(detector.detect_many([]), {})

    def test_single_frame_detect_delegates_to_the_batch(self) -> None:
        detector = self._detector(FAKE_WORKER)
        self.assertEqual(detector.detect(_frame(), 7)[0].rect, BBox(10, 20, 30, 40))

    def test_temp_directory_is_cleaned_up(self) -> None:
        detector = self._detector(FAKE_WORKER)
        before = {p.name for p in SCRATCH.iterdir()}
        detector.detect_many([(0, _frame())])
        after = {p.name for p in SCRATCH.iterdir()}
        self.assertEqual(before, after, "sample frames must not accumulate on disk")


class WorkerEnvironmentTests(_Scratch):
    def test_the_worker_gets_a_private_offline_cache(self) -> None:
        detector = self._detector(FAKE_WORKER)
        env = detector.worker_env()
        self.assertTrue(env["HF_HOME"].endswith("hf"))
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(env["TRANSFORMERS_OFFLINE"], "1")

    def test_the_environment_is_actually_passed_to_the_process(self) -> None:
        detector = self._detector(FAKE_WORKER)
        with patch(
            "engine.pipeline.detect.florence.subprocess.run", wraps=subprocess.run
        ) as run:
            detector.detect_many([(0, _frame())])
        self.assertTrue(run.called)
        env = run.call_args.kwargs.get("env")
        self.assertIsInstance(env, dict)
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")


class FailureTests(_Scratch):
    def test_non_zero_exit_becomes_a_detector_error(self) -> None:
        detector = self._detector("import sys; sys.stderr.write('boom'); sys.exit(3)")
        with self.assertRaises(DetectorError) as caught:
            detector.detect_many([(0, _frame())])
        self.assertIn("boom", str(caught.exception))

    def test_stream_without_done_is_an_error(self) -> None:
        detector = self._detector('print(\'{"type":"frame","frame":0,"boxes":[]}\')')
        with self.assertRaises(DetectorError) as caught:
            detector.detect_many([(0, _frame())])
        self.assertIn("done", str(caught.exception))

    def test_error_payload_is_surfaced(self) -> None:
        script = 'print(\'{"type":"error","message":"no cuda"}\', flush=True)'
        detector = self._detector(script)
        with self.assertRaises(DetectorError) as caught:
            detector.detect_many([(0, _frame())])
        self.assertIn("no cuda", str(caught.exception))


class AvailabilityTests(_Scratch):
    def test_missing_weights_means_not_available(self) -> None:
        detector = FlorenceDetector(weights_dir="/nope", python=sys.executable)
        self.assertFalse(detector.is_available())
        self.assertIn("weights", detector.get_warning())

    def test_missing_interpreter_means_not_available(self) -> None:
        with tempfile.TemporaryDirectory(dir=SCRATCH) as scratch:
            detector = FlorenceDetector(
                weights_dir=scratch, python=str(Path(scratch) / "nope.exe")
            )
            self.assertFalse(detector.is_available())
            self.assertIn("runtime", detector.get_warning())

    def test_cpu_runtime_warns_about_speed(self) -> None:
        detector = self._detector(FAKE_WORKER)
        with patch.object(FlorenceDetector, "runtime_cuda", return_value=False):
            self.assertIn("CPU", detector.get_warning())


if __name__ == "__main__":
    unittest.main()
