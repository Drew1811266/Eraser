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
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from engine.pipeline.analysis import BBox, Detection
from engine.pipeline.detect.base import DetectorError, WatermarkDetector
from engine.pipeline.detect.worker_protocol import collect_frames, encode_job, parse_line
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
    ) -> None:
        self._weights_dir = Path(weights_dir) if weights_dir else None
        self._python = str(python) if python else None
        self._worker = Path(worker) if worker else None
        self._batch = max(1, int(batch))
        self._timeout = float(timeout)

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

    def is_available(self) -> bool:
        if not Path(self.python_executable()).is_file():
            return False
        return (self.weights_dir / "model.safetensors").is_file()

    def get_warning(self) -> str:
        if not Path(self.python_executable()).is_file():
            return "detector runtime is not installed"
        if not (self.weights_dir / "model.safetensors").is_file():
            return "detector weights are missing"
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
        self, frames: list[tuple[int, np.ndarray]]
    ) -> dict[int, list[Detection]]:
        if not frames:
            return {}

        python = Path(self.python_executable())
        if not python.is_file():
            raise DetectorError(f"detector runtime is not installed: {python}")
        weights = self.weights_dir
        if not (weights / "model.safetensors").is_file():
            raise DetectorError(f"detector weights are missing: {weights}")
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
                json.dumps(encode_job(weights, items, batch=self._batch), ensure_ascii=False),
                encoding="utf-8",
            )
            try:
                completed = subprocess.run(
                    [str(python), str(worker), "--job", str(job_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self._timeout,
                    check=False,
                    env=self.worker_env(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise DetectorError(
                    f"detector timed out after {self._timeout:.0f}s"
                ) from exc
            except OSError as exc:
                raise DetectorError(f"cannot start the detector worker: {exc}") from exc

            lines = (completed.stdout or "").splitlines()
            for line in lines:
                payload = parse_line(line)
                if payload.get("type") == "error":
                    raise DetectorError(str(payload.get("message") or "detector failed"))

            if completed.returncode != 0:
                tail = (completed.stderr or "").strip()[-400:]
                raise DetectorError(
                    f"detector worker exited {completed.returncode}: {tail}"
                )
            if not any(parse_line(line).get("type") == "done" for line in lines):
                raise DetectorError("detector worker ended without a done line")

            boxes: dict[int, list[BBox]] = collect_frames(lines, total=len(frames))
            return {
                index: [
                    Detection(frame=index, rect=box, confidence=BOX_CONFIDENCE)
                    for box in boxes.get(index, [])
                ]
                for index, _image in frames
            }
        finally:
            shutil.rmtree(work, ignore_errors=True)
