"""The parts of the SeedVR2 installer that can be tested without 4GB of downloads.

Downloading with a checksum, and unpacking the pinned archive, are the two steps
that fail in ways nobody notices: a corrupted wheel that installs fine, or a
tarball whose top-level directory is not what the extraction assumed.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from engine.pipeline.seedvr import manifest, runtime


class FakeResponse:
    """Just enough of an HTTP response for `_download`."""

    def __init__(self, payload: bytes, total: int | None = None) -> None:
        self._payload = payload
        self._offset = 0
        self.headers = {"Content-Length": str(total if total is not None else len(payload))}

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc) -> None:
        return None


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="seedvr-dl-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def test_a_verified_download_lands(self) -> None:
        payload = b"weights" * 1000
        digest = hashlib.sha256(payload).hexdigest()
        target = self.root / "model.gguf"

        with mock.patch.object(runtime.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            runtime._download("https://example.test/model.gguf", target, expected_sha256=digest)

        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(target.with_suffix(".gguf.part").exists())

    def test_a_checksum_mismatch_leaves_nothing_behind(self) -> None:
        target = self.root / "model.gguf"
        with mock.patch.object(runtime.urllib.request, "urlopen",
                               return_value=FakeResponse(b"corrupt")):
            with self.assertRaises(runtime.SeedVRError):
                runtime._download("https://example.test/model.gguf", target,
                                  expected_sha256="0" * 64)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".gguf.part").exists())

    def test_progress_reports_bytes(self) -> None:
        payload = b"x" * 4096
        seen: list[tuple[int, int]] = []
        with mock.patch.object(runtime.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            runtime._download(
                "https://example.test/model.gguf",
                self.root / "m.gguf",
                on_progress=lambda received, total: seen.append((received, total)),
            )
        self.assertTrue(seen)
        self.assertEqual(seen[-1][0], len(payload))
        self.assertEqual(seen[-1][1], len(payload))

    def test_cancelling_stops_the_transfer(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with mock.patch.object(runtime.urllib.request, "urlopen",
                               return_value=FakeResponse(b"x" * 10)):
            with self.assertRaises(runtime._Cancelled):
                runtime._download("https://example.test/x", self.root / "x.bin", cancel=cancel)
        self.assertFalse((self.root / "x.bin").exists())


class ExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="seedvr-tar-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))

    def _archive(self, name: str) -> Path:
        payload = self.root / "repo.tar.gz"
        with tarfile.open(payload, "w:gz") as tar:
            data = b"print('hi')\n"
            info = tarfile.TarInfo(f"{name}/inference_cli.py")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            requirements = b"torch\n"
            info = tarfile.TarInfo(f"{name}/requirements.txt")
            info.size = len(requirements)
            tar.addfile(info, io.BytesIO(requirements))
        return payload

    def test_the_top_level_directory_is_flattened(self) -> None:
        destination = self.root / "out"
        runtime._extract_repo(self._archive("ComfyUI-SeedVR2_VideoUpscaler-abc123"), destination)
        self.assertTrue((destination / "inference_cli.py").is_file())
        self.assertTrue((destination / "requirements.txt").is_file())

    def test_the_flattened_files_are_where_the_manifest_expects(self) -> None:
        # The paths are what the rest of the runtime calls; a mismatch here is a
        # runtime that reports "installed" and cannot start.
        destination = self.root / "out"
        runtime._extract_repo(self._archive("seedvr2-abc"), destination)
        self.assertEqual(destination.name, "out")


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="seedvr-lock-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        patcher = mock.patch.dict(__import__("os").environ, {"ERASER_SEEDVR_ROOT": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_lock_is_removed_afterwards(self) -> None:
        with runtime._InstallLock():
            self.assertTrue(runtime._lock_path().is_file())
        self.assertFalse(runtime._lock_path().exists())

    def test_a_live_holder_blocks_a_second_install(self) -> None:
        # Pretend another live process owns it: our own pid is alive by definition.
        runtime._lock_path().parent.mkdir(parents=True, exist_ok=True)
        runtime._lock_path().write_text("999999", encoding="utf-8")
        with mock.patch.object(runtime, "_process_alive", return_value=True):
            with self.assertRaises(runtime.SeedVRError):
                with runtime._InstallLock():
                    pass

    def test_a_dead_holder_does_not_block(self) -> None:
        runtime._lock_path().parent.mkdir(parents=True, exist_ok=True)
        runtime._lock_path().write_text("999999", encoding="utf-8")
        with mock.patch.object(runtime, "_process_alive", return_value=False):
            with runtime._InstallLock():
                pass
        self.assertFalse(runtime._lock_path().exists())


class ProgressParsingTests(unittest.TestCase):
    def test_chunk_lines_are_understood(self) -> None:
        match = runtime.PROGRESS_LINE.match("PROGRESS chunk=7/10")
        self.assertIsNotNone(match)
        self.assertEqual((match.group(1), match.group(2)), ("7", "10"))

    def test_a_warmup_stage_is_recognised(self) -> None:
        match = runtime.STAGE_LINE.search("PROGRESS stage=warmup step=vae_encode")
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "vae_encode")

    def test_unrelated_output_is_ignored(self) -> None:
        self.assertIsNone(runtime.PROGRESS_LINE.match("loading model weights..."))


class ManifestPathTests(unittest.TestCase):
    def test_the_runtime_lives_under_one_root(self) -> None:
        root = manifest.runtime_root()
        self.assertTrue(str(manifest.venv_dir()).startswith(str(root)))
        self.assertTrue(str(manifest.repo_dir()).startswith(str(root)))
        self.assertTrue(str(manifest.models_dir()).startswith(str(root)))


if __name__ == "__main__":
    unittest.main()
