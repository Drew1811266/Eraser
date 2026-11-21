"""analyze.* and annotation.compile at the RPC boundary."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.methods import register_all
from engine.platform.ffmpeg import resolve
from engine.rpc.server import RpcServer

FFMPEG = resolve("ffmpeg").path
SCRATCH = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"


def no_detector():
    """Make sure a developer's exported detector cannot leak into a test."""
    return patch.dict(os.environ, {"ERASER_DETECT_STUB": ""})


class _Harness:
    """Drives RpcServer over two in-memory streams."""

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.server = RpcServer(stdin=self.stdin, stdout=self.stdout)
        register_all(self.server)

    def call(self, method: str, params: dict | None = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        self.server._dispatch(json.dumps(payload))  # noqa: SLF001 - test harness
        # A background task may emit notifications on the same stream, so match
        # on the request id instead of assuming the last line is the response.
        for line in reversed(self.stdout.getvalue().strip().splitlines()):
            frame = json.loads(line)
            if frame.get("id") == 1:
                return frame
        raise AssertionError(f"no response frame for {method}")


def _wait_for_task(harness: _Harness, task_id: str, timeout: float = 120.0) -> dict:
    """Poll until terminal. Also keeps a daemon task from outliving the test.

    On Windows the task thread holds the source file open, so a test that leaves
    it running fails later in `TemporaryDirectory` cleanup with WinError 32 —
    a leak that only shows up when the whole module runs.
    """
    deadline = time.time() + timeout
    snapshot: dict = {}
    while time.time() < deadline:
        snapshot = harness.call("process.status", {"task_id": task_id})["result"]
        if snapshot["state"] in {"success", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {snapshot}")


class AnalyzeCapabilitiesTests(unittest.TestCase):
    def test_reports_a_shape_the_ui_can_render(self) -> None:
        frame = _Harness().call("analyze.capabilities")
        result = frame["result"]
        self.assertIn("detectors", result)
        self.assertIn("available", result)

    def test_without_a_detector_nothing_is_available(self) -> None:
        with no_detector():
            frame = _Harness().call("analyze.capabilities", {})
        self.assertFalse(frame["result"]["available"])


class CompileTests(unittest.TestCase):
    def test_compile_returns_segments(self) -> None:
        frame = _Harness().call(
            "annotation.compile",
            {
                "watermarks": [
                    {
                        "id": "wm-1",
                        "intervals": [{"start_frame": 0, "end_frame": 49}],
                        "keyframes": [
                            {"frame": 0, "rect": {"x": 1, "y": 2, "width": 3, "height": 4}}
                        ],
                    }
                ],
                "shots": [{"start_frame": 0, "end_frame": 49}],
                "video": {"width": 640, "height": 360, "frame_count": 50},
            },
        )
        segments = frame["result"]["segments"]
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start_frame"], 0)

    def test_compile_rejects_a_missing_video_block(self) -> None:
        frame = _Harness().call("annotation.compile", {"watermarks": [], "shots": []})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")


class AnalyzeStartTests(unittest.TestCase):
    def test_missing_file_is_rejected(self) -> None:
        frame = _Harness().call("analyze.start", {"input_path": "/nope/missing.mp4"})
        self.assertEqual(frame["error"]["data"]["code"], "INVALID_PARAMS")

    def test_without_a_detector_it_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            with no_detector():
                frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertEqual(frame["error"]["data"]["code"], "DETECTOR_MISSING")

    def test_detector_missing_has_its_own_jsonrpc_code(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            with no_detector():
                frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertEqual(frame["error"]["code"], -32006)

    def test_with_a_stub_detector_it_returns_a_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            script = Path(scratch) / "detections.json"
            script.write_text('{"frames": {}}', encoding="utf-8")
            with patch.dict(os.environ, {"ERASER_DETECT_STUB": str(script)}):
                harness = _Harness()
                frame = harness.call("analyze.start", {"input_path": str(video)})
                self.assertIn(
                    "task_id", frame.get("result", {}), "start must return immediately"
                )
                _wait_for_task(harness, frame["result"]["task_id"])


@unittest.skipUnless(FFMPEG, "ffmpeg is required for the analysis integration test")
class AnalysisIntegrationTests(unittest.TestCase):
    """Run the whole task: probe -> sample -> detect -> associate -> status.

    This is the test that would have caught the missing coordinate mapping: the
    clip is wider than the analysis ceiling, so the detector sees a downscaled
    frame and every box has to be scaled back on the way out.
    """

    @classmethod
    def setUpClass(cls) -> None:
        SCRATCH.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("ERASER_TEMP_DIR")
        os.environ["ERASER_TEMP_DIR"] = str(SCRATCH)
        if previous is None:
            cls.addClassCleanup(os.environ.pop, "ERASER_TEMP_DIR", None)
        else:
            cls.addClassCleanup(os.environ.__setitem__, "ERASER_TEMP_DIR", previous)
        cls._scratch = tempfile.mkdtemp(prefix="eraser-analyze-", dir=SCRATCH)
        cls.addClassCleanup(shutil.rmtree, cls._scratch, ignore_errors=True)

        cls.video = Path(cls._scratch) / "wide.mp4"
        subprocess.run(
            [
                FFMPEG or "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=gray:s=1600x200:r=10",
                "-frames:v", "8", "-pix_fmt", "yuv420p", str(cls.video),
            ],
            check=True,
            capture_output=True,
        )
        # Boxes are given in the *downscaled* 1280-wide space the detector sees.
        cls.script = Path(cls._scratch) / "detections.json"
        cls.script.write_text(
            json.dumps(
                {
                    "frames": {
                        "0": [{"x": 400, "y": 50, "width": 80, "height": 40, "confidence": 0.9}],
                        "2": [{"x": 400, "y": 50, "width": 80, "height": 40, "confidence": 0.9}],
                    }
                }
            ),
            encoding="utf-8",
        )

    def _wait(self, harness: _Harness, task_id: str, timeout: float = 120.0) -> dict:
        return _wait_for_task(harness, task_id, timeout=timeout)

    def test_detections_are_mapped_back_to_source_pixels(self) -> None:
        with patch.dict(os.environ, {"ERASER_DETECT_STUB": str(self.script)}):
            harness = _Harness()
            started = harness.call(
                "analyze.start", {"input_path": str(self.video), "sample_step": 2}
            )
            snapshot = self._wait(harness, started["result"]["task_id"])

        self.assertEqual(snapshot["state"], "success", snapshot.get("error"))
        analysis = snapshot["result"]
        self.assertEqual(analysis["video"]["width"], 1600)
        self.assertEqual(len(analysis["watermarks"]), 1)
        rect = analysis["watermarks"][0]["keyframes"][0]["rect"]
        self.assertAlmostEqual(rect["x"], 500, delta=2)  # 400 * (1600/1280)
        self.assertAlmostEqual(rect["width"], 100, delta=2)
        self.assertEqual(analysis["watermarks"][0]["motion"], "static")

    def test_unreadable_clip_fails_the_task_with_a_reason(self) -> None:
        broken = Path(self._scratch) / "broken.mp4"
        broken.write_bytes(b"not a video")
        with patch.dict(os.environ, {"ERASER_DETECT_STUB": str(self.script)}):
            harness = _Harness()
            started = harness.call("analyze.start", {"input_path": str(broken)})
            snapshot = self._wait(harness, started["result"]["task_id"])
        self.assertEqual(snapshot["state"], "failed")
        self.assertIn("cannot read video", snapshot.get("error", ""))


if __name__ == "__main__":
    unittest.main()
