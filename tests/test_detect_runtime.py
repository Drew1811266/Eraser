"""The on-demand detector runtime: pinned weights and a private venv."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.pipeline.detect import runtime, weights
from engine.pipeline.detect.weights import DetectorInstallError


class ManifestTests(unittest.TestCase):
    def test_manifest_is_pinned(self) -> None:
        self.assertEqual(len(weights.FILES), 9)
        for item in weights.FILES:
            with self.subTest(file=item.name):
                self.assertEqual(len(item.sha256), 64)
                self.assertGreater(item.bytes, 0)

    def test_the_weights_blob_is_the_big_one(self) -> None:
        blob = next(item for item in weights.FILES if item.name == "model.safetensors")
        self.assertGreater(blob.bytes, 400 * 1024 * 1024)

    def test_urls_are_pinned_to_a_revision(self) -> None:
        url = weights.url_for("config.json")
        self.assertIn(weights.REVISION, url)
        self.assertTrue(url.endswith("/config.json"))


class DownloadTests(unittest.TestCase):
    def _source(self, scratch: str, payload: bytes) -> Path:
        source = Path(scratch) / "src.bin"
        source.write_bytes(payload)
        return source

    def test_verifies_sha256_and_lands_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = self._source(scratch, b"payload")
            destination = Path(scratch) / "out.bin"
            weights.download_weight(
                source.as_uri(),
                destination,
                expected_sha256=hashlib.sha256(b"payload").hexdigest(),
                expected_bytes=len(b"payload"),
            )
            self.assertEqual(destination.read_bytes(), b"payload")

    def test_a_bad_hash_leaves_no_file_behind(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = self._source(scratch, b"payload")
            destination = Path(scratch) / "out.bin"
            with self.assertRaises(DetectorInstallError):
                weights.download_weight(
                    source.as_uri(),
                    destination,
                    expected_sha256="0" * 64,
                    expected_bytes=len(b"payload"),
                )
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".bin.part").exists())

    def test_an_already_valid_file_is_not_downloaded_again(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch) / "out.bin"
            destination.write_bytes(b"payload")
            weights.download_weight(
                (Path(scratch) / "missing.bin").as_uri(),
                destination,
                expected_sha256=hashlib.sha256(b"payload").hexdigest(),
                expected_bytes=len(b"payload"),
            )
            self.assertEqual(destination.read_bytes(), b"payload")


class StateTests(unittest.TestCase):
    def _home(self, scratch: str):
        return patch.dict(os.environ, {"ERASER_DETECTOR_HOME": scratch})

    def test_missing_state_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            self.assertEqual(runtime.state().get("phase"), "missing")
            self.assertFalse(runtime.is_ready())

    def test_a_ready_state_without_weights_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            runtime.write_state(
                {"phase": "ready", "python": str(Path(scratch) / "venv" / "python.exe")}
            )
            self.assertFalse(runtime.is_ready(), "a state file alone must not count")

    def test_ready_requires_state_python_and_weights(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            python = runtime.venv_python()
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"x")
            weights_dir = runtime.weights_dir()
            weights_dir.mkdir(parents=True, exist_ok=True)
            (weights_dir / "model.safetensors").write_bytes(b"x")
            runtime.write_state({"phase": "ready", "python": str(python)})
            self.assertTrue(runtime.is_ready())


class InstallTests(unittest.TestCase):
    def _home(self, scratch: str):
        return patch.dict(os.environ, {"ERASER_DETECTOR_HOME": scratch})

    def test_install_creates_the_runtime_and_marks_it_ready(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            def fake_venv(cancel=None, progress=None):
                path = runtime.venv_python()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            def fake_deps(cancel=None, progress=None):
                return None

            def fake_weights(destination, progress=None, cancel=None):
                target = runtime.weights_dir()
                target.mkdir(parents=True, exist_ok=True)
                (target / "model.safetensors").write_bytes(b"x")

            with patch.object(runtime, "_ensure_venv", fake_venv), patch.object(
                runtime, "_install_dependencies", fake_deps
            ), patch.object(runtime.weights, "download_all", fake_weights), patch.object(
                runtime, "_detect_cuda", return_value=True
            ):
                state = runtime.install()
            self.assertEqual(state["phase"], "ready")
            self.assertTrue(state["cuda"])
            self.assertTrue(runtime.is_ready())

    def test_a_failed_download_marks_the_state_failed(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            def fake_venv(cancel=None, progress=None):
                path = runtime.venv_python()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")

            def boom(*_args, **_kwargs):
                raise DetectorInstallError("network down")

            with patch.object(runtime, "_ensure_venv", fake_venv), patch.object(
                runtime, "_install_dependencies", lambda *a, **k: None
            ), patch.object(runtime.weights, "download_all", boom):
                with self.assertRaises(DetectorInstallError):
                    runtime.install()
            state = runtime.state()
            self.assertEqual(state["phase"], "failed")
            self.assertIn("network down", state["error"])
            self.assertFalse(runtime.is_ready())

    def test_liveness_check_does_not_kill_the_process(self) -> None:
        # `os.kill(pid, 0)` terminates a process on Windows; using it as a probe
        # would kill the install the lock exists to protect. If this ever
        # regresses, this test takes the runner down with it.
        self.assertTrue(runtime.InstallLock._alive(os.getpid()))
        self.assertFalse(runtime.InstallLock._alive(0))
        self.assertFalse(runtime.InstallLock._alive(999_999_999))

    def test_a_second_install_is_refused_while_one_runs(self) -> None:
        with tempfile.TemporaryDirectory() as scratch, self._home(scratch):
            holder = runtime.InstallLock()
            holder.__enter__()
            try:
                with self.assertRaises(DetectorInstallError) as caught:
                    runtime.install()
                self.assertIn("another", str(caught.exception).lower())
            finally:
                holder.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
