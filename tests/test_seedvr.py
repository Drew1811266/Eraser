"""SeedVR2 availability and install state.

The point of these tests is the *reason* a user sees. "不可用" with no cause
sends people to reinstall the app; each state below needs a different fix, so
each one is checked to name itself.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine.pipeline.seedvr import capability, manifest


class ManifestTests(unittest.TestCase):
    def test_every_model_is_downloadable_and_verifiable(self) -> None:
        for model_id in manifest.UPSCALE_MODELS:
            spec = manifest.get_spec(model_id)
            self.assertTrue(spec.url.startswith("https://"), model_id)
            self.assertEqual(len(spec.sha256), 64, model_id)
            self.assertGreater(spec.min_memory_gb, 0, model_id)
            self.assertTrue(spec.install_hint, model_id)

    def test_the_short_hash_is_not_used(self) -> None:
        # A commit hash that is not 40 characters cannot be pinned reliably.
        self.assertEqual(len(manifest.REPO_REF), 40)

    def test_unknown_models_are_rejected(self) -> None:
        with self.assertRaises(KeyError):
            manifest.get_spec("seedvr2_7b_fp16")


class CapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="seedvr-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self.env = mock.patch.dict(os.environ, {"ERASER_SEEDVR_ROOT": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.model = manifest.DEFAULT_MODEL_ID

    def _fake_runtime(self) -> None:
        (self.root / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
        manifest.repo_dir().mkdir(parents=True, exist_ok=True)
        manifest.inference_script().write_text("# cli", encoding="utf-8")

    def _fake_model(self) -> None:
        # An installed model is the DiT *and* its shared VAE; only one of them is
        # a model that cannot run.
        manifest.models_dir().mkdir(parents=True, exist_ok=True)
        manifest.model_path(self.model).write_bytes(b"weights")
        manifest.vae_path().write_bytes(b"vae")

    def test_missing_python_is_named(self) -> None:
        with mock.patch.object(capability, "find_python", return_value=""):
            result = capability.check(self.model)
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "no_python")
        self.assertIn("3.12", result.hint)

    def test_missing_runtime_is_named(self) -> None:
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model)
        self.assertEqual(result.reason, "no_runtime")
        self.assertIn("下载", result.hint)

    def test_missing_repo_is_named(self) -> None:
        (self.root / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model)
        self.assertEqual(result.reason, "no_repo")

    def test_missing_model_is_named(self) -> None:
        self._fake_runtime()
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model)
        self.assertEqual(result.reason, "no_model")
        self.assertTrue(result.detail)

    def test_a_missing_vae_counts_as_not_installed(self) -> None:
        # The DiT alone cannot decode a frame; reporting it as installed would
        # produce a runtime error on the first job.
        self._fake_runtime()
        manifest.models_dir().mkdir(parents=True, exist_ok=True)
        manifest.model_path(self.model).write_bytes(b"weights")
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model)
        self.assertEqual(result.reason, "no_model")

    def test_a_small_gpu_is_a_refusal_with_a_number(self) -> None:
        self._fake_runtime()
        self._fake_model()
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model, total_memory_gb=8.0)
        self.assertEqual(result.reason, "no_memory")
        self.assertIn("12", result.detail)

    def test_a_complete_install_is_available(self) -> None:
        self._fake_runtime()
        self._fake_model()
        with mock.patch.object(capability, "find_python", return_value="C:/python.exe"):
            result = capability.check(self.model, total_memory_gb=24.0)
        self.assertTrue(result.available)
        self.assertEqual(result.reason, "")

    def test_unknown_model_is_refused_by_the_engine_not_the_ui(self) -> None:
        result = capability.check("nope")
        self.assertEqual(result.reason, "unknown_model")


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="seedvr-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        patcher = mock.patch.dict(os.environ, {"ERASER_SEEDVR_ROOT": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_absent_state_reads_as_absent(self) -> None:
        self.assertEqual(capability.read_state().phase, "absent")

    def test_state_round_trips(self) -> None:
        state = capability.RuntimeState(phase="installing", progress=0.42, message="安装依赖",
                                        model_id=manifest.DEFAULT_MODEL_ID, python="C:/py.exe",
                                        installed_models=["seedvr2_3b_q8_0_gguf"])
        capability.write_state(state)
        loaded = capability.read_state()
        self.assertEqual(loaded.phase, "installing")
        self.assertAlmostEqual(loaded.progress, 0.42)
        self.assertEqual(loaded.installed_models, ["seedvr2_3b_q8_0_gguf"])

    def test_a_corrupt_state_file_does_not_crash_the_probe(self) -> None:
        path = manifest.state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(capability.read_state().phase, "absent")

    def test_describe_reports_every_model_and_the_python_it_found(self) -> None:
        payload = capability.describe()
        self.assertEqual(payload["engine"], "seedvr2")
        self.assertEqual(len(payload["models"]), len(manifest.UPSCALE_MODELS))
        self.assertIn("runtime_installed", payload)


if __name__ == "__main__":
    unittest.main()
