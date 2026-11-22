"""model.install / model.installStatus and what capabilities say about Florence."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.methods import register_all
from engine.pipeline.detect import FlorenceDetector
from engine.pipeline.detect.weights import DetectorInstallError
from engine.rpc.server import RpcServer
from engine.tasks import TERMINAL_STATES


class _Harness:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.server = RpcServer(stdin=self.stdin, stdout=self.stdout)
        register_all(self.server)

    def call(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        self.server._dispatch(json.dumps(payload))  # noqa: SLF001 - test harness
        for line in reversed(self.stdout.getvalue().strip().splitlines()):
            frame = json.loads(line)
            if frame.get("id") == 1:
                return frame
        raise AssertionError(f"no response for {method}")


def _wait(harness: _Harness, task_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    snapshot: dict = {}
    while time.time() < deadline:
        snapshot = harness.call("model.installStatus", {"task_id": task_id})["result"]
        if snapshot["state"] in TERMINAL_STATES:
            return snapshot
        time.sleep(0.02)
    raise AssertionError(f"task did not finish: {snapshot}")


class _IsolatedHome(unittest.TestCase):
    """A temp detector home, so a real install on this machine cannot leak in."""

    @classmethod
    def setUpClass(cls) -> None:
        home = tempfile.mkdtemp(prefix="eraser-detector-")
        cls.addClassCleanup(__import__("shutil").rmtree, home, True)
        previous = os.environ.get("ERASER_DETECTOR_HOME")
        os.environ["ERASER_DETECTOR_HOME"] = home
        if previous is None:
            cls.addClassCleanup(os.environ.pop, "ERASER_DETECTOR_HOME", None)
        else:
            cls.addClassCleanup(os.environ.__setitem__, "ERASER_DETECTOR_HOME", previous)


class InstallTests(_IsolatedHome):
    def test_unknown_model_is_rejected(self) -> None:
        frame = _Harness().call("model.install", {"model_id": "nope"})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")

    def test_install_runs_and_reports_the_runtime_state(self) -> None:
        with patch("engine.methods.model_install.runtime") as fake:
            fake.is_ready.return_value = False
            fake.install.return_value = {"phase": "ready", "cuda": True, "model_id": "florence2"}
            harness = _Harness()
            started = harness.call("model.install", {"model_id": "florence2"})
            snapshot = _wait(harness, started["result"]["task_id"])
        self.assertEqual(snapshot["state"], "success", snapshot.get("error"))
        self.assertEqual(snapshot["result"]["phase"], "ready")
        fake.install.assert_called_once()

    def test_a_failed_install_fails_the_task_with_the_reason(self) -> None:
        with patch("engine.methods.model_install.runtime") as fake:
            fake.is_ready.return_value = False
            fake.install.side_effect = DetectorInstallError("network down")
            harness = _Harness()
            started = harness.call("model.install", {"model_id": "florence2"})
            snapshot = _wait(harness, started["result"]["task_id"])
        self.assertEqual(snapshot["state"], "failed")
        self.assertIn("network down", snapshot.get("error", ""))

    def test_an_installed_runtime_is_not_reinstalled(self) -> None:
        with patch("engine.methods.model_install.runtime") as fake:
            fake.is_ready.return_value = True
            fake.state.return_value = {"phase": "ready", "model_id": "florence2"}
            harness = _Harness()
            started = harness.call("model.install", {"model_id": "florence2"})
            snapshot = _wait(harness, started["result"]["task_id"])
        self.assertEqual(snapshot["state"], "success")
        fake.install.assert_not_called()

    def test_status_for_an_unknown_task_is_an_error(self) -> None:
        frame = _Harness().call("model.installStatus", {"task_id": "nope"})
        self.assertEqual(frame["error"]["data"]["code"], "TASK_NOT_FOUND")


class CapabilityTests(_IsolatedHome):
    def test_florence_is_listed_even_when_not_installed(self) -> None:
        with patch.dict(os.environ, {"ERASER_DETECT_STUB": ""}):
            result = _Harness().call("analyze.capabilities")["result"]
        entry = next(item for item in result["detectors"] if item["model_id"] == "florence2")
        self.assertFalse(entry["installed"])
        self.assertFalse(entry["available"])
        self.assertTrue(entry["blocked_reason"], "the UI needs to know why")

    def test_an_installed_florence_is_available(self) -> None:
        with patch.object(FlorenceDetector, "is_installed", return_value=True), patch.object(
            FlorenceDetector, "is_available", return_value=True
        ), patch.object(FlorenceDetector, "get_warning", return_value=""), patch.dict(
            os.environ, {"ERASER_DETECT_STUB": ""}
        ):
            result = _Harness().call("analyze.capabilities")["result"]
        entry = next(item for item in result["detectors"] if item["model_id"] == "florence2")
        self.assertTrue(entry["installed"])
        self.assertTrue(entry["available"])
        self.assertEqual(entry["blocked_reason"], "")


if __name__ == "__main__":
    unittest.main()
