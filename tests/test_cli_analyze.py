"""The CLI is the way to prove the analysis works without Electron."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from engine import cli


class ParserTests(unittest.TestCase):
    def test_analyze_subcommand_requires_a_path(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(["analyze"])
        self.assertEqual(caught.exception.code, 2)

    def test_analyze_accepts_detection_script(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["analyze", "clip.mp4", "--detections", "d.json", "--json"])
        self.assertEqual(args.input, "clip.mp4")
        self.assertTrue(args.json)


class CommandTests(unittest.TestCase):
    def test_missing_file_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            script = Path(scratch) / "d.json"
            script.write_text('{"frames": {}}', encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = cli.main(
                    ["analyze", "/nope/missing.mp4", "--detections", str(script)]
                )
        self.assertEqual(code, 2)

    def test_missing_detection_script_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"x")
            with redirect_stdout(io.StringIO()):
                code = cli.main(["analyze", str(video), "--detections", "/nope/d.json"])
        self.assertEqual(code, 2)

    def test_success_prints_json(self) -> None:
        payload = {"shots": [{"start_frame": 0, "end_frame": 9}], "watermarks": []}
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "clip.mp4"
            video.write_bytes(b"x")
            script = Path(scratch) / "d.json"
            script.write_text(json.dumps({"frames": {}}), encoding="utf-8")
            with patch("engine.cli._analyze", return_value=payload):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cli.main(
                        ["analyze", str(video), "--detections", str(script), "--json"]
                    )
        self.assertEqual(code, 0)
        self.assertIn('"watermarks"', buffer.getvalue())

    def test_unreadable_video_reports_a_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            video = Path(scratch) / "broken.mp4"
            video.write_bytes(b"not a video")
            script = Path(scratch) / "d.json"
            script.write_text('{"frames": {}}', encoding="utf-8")
            buffer = io.StringIO()
            with redirect_stdout(buffer), redirect_stderr(buffer):
                code = cli.main(["analyze", str(video), "--detections", str(script)])
        self.assertEqual(code, 1)
        self.assertIn("analysis failed", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
