"""Florence-2 as a `WatermarkDetector`: engine-side, torch-free, one process per batch.

The model lives in the on-demand runtime (see `runtime.py`); this module only
writes the sampled frames to JPEG, runs the worker, and parses its NDJSON. That
split is what keeps torch out of the shipped engine while still letting the
detector be selected, advertised and cancelled like any other task.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect import weights
from engine.pipeline.detect.base import DetectorError, WatermarkDetector
from engine.pipeline.detect.worker_protocol import collect_frames, encode_job, parse_line
from engine.pipeline.logging import logger
from engine.platform.paths import temp_dir

#: Florence-2 reports boxes, never scores. A constant keeps the field honest
#: instead of inventing confidence the model did not express.
BOX_CONFIDENCE = 1.0
DEBUG_WORKER_ENV = "ERASER_FLORENCE_WORKER"


class FlorenceDetector(WatermarkDetector):
    model_id = "florence2"
    display_name = "Florence-2 (watermark)"

    def __init__(
        self,
        *,
        weights_dir: str | Path | None = None,
        python: str | Path | None = None,
        worker: str | Path | None = None,
        batch: int = 8,
        timeout: float = 900.0,
        weights_check: Any = None,
    ) -> None:
        self._weights_dir = Path(weights_dir) if weights_dir else None
        self._python = str(python) if python else None
        self._worker = Path(worker) if worker else None
        self._batch = max(1, int(batch))
        self._timeout = float(timeout)
        self._weights_check = weights_check or weights.missing

    # -- paths ---------------------------------------------------------

    @property
    def weights_dir(self) -> Path:
        if self._weights_dir is not None:
            return self._weights_dir
        from engine.pipeline.detect import runtime

        return runtime.weights_dir()

    @property
    def worker_script(self) -> Path:
        if self._worker is not None:
            return self._worker
        override = os.environ.get(DEBUG_WORKER_ENV)
        if override:
            return Path(override)
        return Path(__file__).with_name("florence_worker.py")

    def python_executable(self) -> str:
        if self._python:
            return self._python
        from engine.pipeline.detect import runtime

        return str(runtime.worker_python())

    @staticmethod
    def runtime_cuda() -> bool:
        from engine.pipeline.detect import runtime

        try:
            return bool(runtime.state().get("cuda"))
        except Exception:  # noqa: BLE001 - availability must never raise
            return False

    # -- contract ------------------------------------------------------

    def load(self) -> None:
        """Nothing to load here: the worker loads the model when it starts."""

    def is_installed(self) -> bool:
        """The runtime exists, even if the weights were wiped or half-downloaded."""
        return Path(self.python_executable()).is_file()

    def is_available(self) -> bool:
        if not self.is_installed():
            return False
        return not self._weights_check(self.weights_dir)

    def get_warning(self) -> str:
        if not self.is_installed():
            return "detector runtime is not installed"
        absent = self._weights_check(self.weights_dir)
        if absent:
            return f"{len(absent)} detector weight file(s) missing; reinstall the detector"
        if not self.runtime_cuda():
            return "Florence-2 will run on the CPU (slow, a few seconds per frame)"
        return ""

    def detect(self, frame: np.ndarray | None, frame_index: int) -> list[Detection]:
        return self.detect_many([(int(frame_index), frame)]).get(int(frame_index), [])

    def worker_env(self) -> dict[str, str]:
        """A private, offline environment for the worker.

        `trust_remote_code` makes transformers copy the model's own Python into
        `~/.cache/huggingface`; on a locked-down Windows profile that write is
        denied and the detector looks broken. Keeping the cache under the model
        directory also means uninstalling the model removes all of it, and
        `HF_HUB_OFFLINE` guarantees inference never reaches the network.
        """
        env = dict(os.environ)
        cache = self.weights_dir.parent / "hf"
        env["HF_HOME"] = str(cache)
        env["TRANSFORMERS_CACHE"] = str(cache / "hub")
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def detect_many(
        self,
        frames: list[tuple[int, np.ndarray]],
        *,
        cancel: object | None = None,
    ) -> dict[int, list[Detection]]:
        if not frames:
            return {}

        python = Path(self.python_executable())
        if not python.is_file():
            raise DetectorError(f"detector runtime is not installed: {python}")
        weights_path = self.weights_dir
        absent = self._weights_check(weights_path)
        if absent:
            raise DetectorError(
                f"detector weights are incomplete ({len(absent)} file(s) missing): "
                f"{weights_path}"
            )
        worker = self.worker_script
        if not worker.is_file():
            raise DetectorError(f"detector worker is missing: {worker}")

        root = temp_dir()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DetectorError(f"temp directory is not writable: {root} ({exc})") from exc
        work = Path(tempfile.mkdtemp(prefix="florence-", dir=root))
        try:
            items: list[tuple[int, Path]] = []
            for index, image in frames:
                if image is None:
                    raise DetectorError(f"frame {index} has no pixels")
                path = work / f"{int(index):08d}.jpg"
                if not cv2.imwrite(str(path), image):
                    raise DetectorError(f"cannot write sampled frame {index}")
                items.append((int(index), path))

            job_path = work / "job.json"
            job_path.write_text(
                json.dumps(encode_job(weights_path, items, batch=self._batch), ensure_ascii=False),
                encoding="utf-8",
            )
            lines = self._run_worker(
                [str(python), str(worker), "--job", str(job_path)], cancel=cancel
            )
            for line in lines:
                payload = parse_line(line)
                if payload.get("type") == "error":
                    raise DetectorError(str(payload.get("message") or "detector failed"))

            if not any(parse_line(line).get("type") == "done" for line in lines):
                raise DetectorError("detector worker ended without a done line")

            boxes: dict[int, list[BBox]] = collect_frames(lines, total=len(frames))
            expected = {int(index) for index, _image in frames}
            missing = sorted(expected - set(boxes))
            if missing:
                # A skipped frame must not become "this frame has no watermark":
                # that is how a broken runtime reports a clean video.
                raise DetectorError(
                    f"detector worker skipped {len(missing)} frame(s): {missing[:5]}"
                )
            return {
                index: [
                    Detection(frame=index, rect=box, confidence=BOX_CONFIDENCE)
                    for box in boxes.get(index, [])
                ]
                for index in expected
            }
        finally:
            try:
                shutil.rmtree(work)
            except OSError as exc:
                logger.warning("cannot remove detector scratch %s: %s", work, exc)

    def _run_worker(self, command: list[str], *, cancel: object | None) -> list[str]:
        """Run the worker, staying answerable to cancellation while it runs."""
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.worker_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise DetectorError(f"cannot start the detector worker: {exc}") from exc

        lines: list[str] = []
        errors: list[str] = []
        readers = [
            threading.Thread(
                target=lambda stream=process.stdout: lines.extend(stream or []), daemon=True
            ),
            threading.Thread(
                target=lambda stream=process.stderr: errors.append((stream.read() if stream else "")),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        deadline = time.monotonic() + self._timeout
        reason = ""
        while process.poll() is None:
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                reason = "detector cancelled"
                break
            if time.monotonic() > deadline:
                reason = f"detector timed out after {self._timeout:.0f}s"
                break
            time.sleep(0.15)
        if reason:
            process.kill()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
        for reader in readers:
            reader.join(timeout=5)
        if reason:
            raise DetectorError(reason)
        if process.returncode != 0:
            tail = "".join(errors).strip()[-400:]
            raise DetectorError(f"detector worker exited {process.returncode}: {tail}")
        return [line for line in lines if line.strip()]
