"""analyze.* and annotation.compile at the RPC boundary."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.methods import register_all
from engine.rpc.server import RpcServer


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


class AnalyzeCapabilitiesTests(unittest.TestCase):
    def test_reports_a_shape_the_ui_can_render(self) -> None:
        frame = _Harness().call("analyze.capabilities")
        result = frame["result"]
        self.assertIn("detectors", result)
        self.assertIn("available", result)

    def test_without_a_detector_nothing_is_available(self) -> None:
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
            frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertEqual(frame["error"]["data"]["code"], "DETECTOR_MISSING")

    def test_with_a_stub_detector_it_returns_a_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"not really a video")
            script = Path(scratch) / "detections.json"
            script.write_text('{"frames": {}}', encoding="utf-8")
            with patch.dict(os.environ, {"ERASER_DETECT_STUB": str(script)}):
                frame = _Harness().call("analyze.start", {"input_path": str(video)})
        self.assertIn("task_id", frame.get("result", {}), "start must return immediately")


if __name__ == "__main__":
    unittest.main()
