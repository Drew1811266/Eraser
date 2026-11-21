"""Guards on the contract itself, and on generated artifacts staying in sync."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from engine.generated.contract import (
    CONTRACT_VERSION,
    ERROR_CODES,
    IMPLEMENTED_METHODS,
    METHOD_LIST,
    METHODS,
    TYPES,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class ContractShapeTests(unittest.TestCase):
    def test_version_is_present(self) -> None:
        self.assertRegex(CONTRACT_VERSION, r"^\d+\.\d+\.\d+$")

    def test_method_names_are_unique(self) -> None:
        names = [method["name"] for method in METHOD_LIST]
        self.assertEqual(len(names), len(set(names)))

    def test_method_names_are_namespaced(self) -> None:
        for name in METHODS:
            self.assertIn(".", name, f"{name} should be namespace.action")
            self.assertEqual(name, name.strip())

    def test_every_method_declares_params_and_result(self) -> None:
        for name, spec in METHODS.items():
            with self.subTest(method=name):
                self.assertIn("params", spec)
                self.assertIn("result", spec)
                self.assertIsInstance(spec.get("implemented"), bool)

    def test_every_ref_points_at_a_known_type(self) -> None:
        def walk(node: object) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if ref:
                    self.assertIn(ref.rsplit("/", 1)[-1], TYPES, f"dangling ref: {ref}")
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(METHOD_LIST)

    def test_implemented_methods_are_a_subset(self) -> None:
        self.assertTrue(set(IMPLEMENTED_METHODS) <= set(METHODS))
        self.assertGreater(len(IMPLEMENTED_METHODS), 0)

    def test_live_surface_matches_the_delivered_engine(self) -> None:
        # Pins which methods are actually wired. Changing this set must be a
        # deliberate contract edit paired with a real handler, not a accident.
        self.assertEqual(
            set(IMPLEMENTED_METHODS),
            {
                "system.ping",
                "system.capabilities",
                "system.shutdown",
                "media.probe",
                # The ported reference UI needs frames for its canvas, a home
                # for its annotations, and the ability to repair a missing model.
                "preview.open",
                "preview.frame",
                "preview.close",
                "annotation.normalize",
                "annotation.load",
                "annotation.save",
                "annotation.delete",
                "model.download.start",
                "model.download.status",
                "model.download.cancel",
                "process.start",
                "process.cancel",
                "process.status",
                "upscale.start",
                "upscale.capabilities",
                "model.list",
                "assets.verify",
                # The analysis stage: a detector surface, the analysis task, and
                # the compile step that turns tracks into pipeline segments.
                "analyze.capabilities",
                "analyze.start",
                "annotation.compile",
            },
        )
        self.assertLess(set(IMPLEMENTED_METHODS), set(METHODS), "some methods must remain stubbed")


class ErrorCodeTests(unittest.TestCase):
    def test_jsonrpc_numbers_are_unique(self) -> None:
        numbers = [entry["jsonrpc"] for entry in ERROR_CODES.values()]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_every_code_has_message_key_and_retryable(self) -> None:
        for code, entry in ERROR_CODES.items():
            with self.subTest(code=code):
                self.assertTrue(entry["message_key"].startswith("err."))
                self.assertIsInstance(entry["retryable"], bool)


class GeneratedArtifactTests(unittest.TestCase):
    def test_codegen_reports_no_drift(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "contract" / "codegen" / "generate.py"), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"generated artifacts are stale.\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_python_artifact_matches_source_of_truth(self) -> None:
        source = json.loads((REPO_ROOT / "contract" / "methods.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [method["name"] for method in source["methods"]],
            [method["name"] for method in METHOD_LIST],
        )

    def test_typescript_artifact_exists_and_names_every_method(self) -> None:
        artifact = REPO_ROOT / "desktop" / "electron" / "generated" / "contract.ts"
        self.assertTrue(artifact.exists(), "run contract/codegen/generate.py")
        text = artifact.read_text(encoding="utf-8")
        for name in METHODS:
            self.assertIn(f'"{name}"', text, f"{name} missing from the TS artifact")


if __name__ == "__main__":
    unittest.main()
