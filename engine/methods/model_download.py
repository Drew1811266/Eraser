"""model.download.*: fetch weights that are not in the bundle.

Everything ships with the installer, so this is the repair path rather than the
normal flow: a user who deleted the model folder, or someone running the engine
from source without `scripts/fetch-assets.py`, can pull the weights back from
inside the app.

Only LaMa has a URL that can be verified end to end. Real-ESRGAN's ONNX file is
built locally by `packaging/convert-esrgan.py` (no upstream release exists), so
its status reports "not downloadable" with the reason instead of pretending to
download something.
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.platform.files import atomic_write, sha256_file
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer

LAMA_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
LAMA_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"

REGISTRY: dict[str, dict[str, Any]] = {
    "lama_roi": {
        "url": LAMA_URL,
        "sha256": LAMA_SHA256,
        "target": lambda: _lama_path(),
    },
    "realesrgan_general_x4v3": {
        "url": "",
        "sha256": "",
        "target": lambda: _esrgan_path(),
    },
    "realesrgan_x4": {
        "url": "",
        "sha256": "",
        "target": lambda: _esrgan_path(),
    },
}


def _lama_path() -> Path:
    from engine.pipeline.remover import default_model_path

    return default_model_path()


def _esrgan_path() -> Path:
    from engine.pipeline.upscaler import default_model_path

    return default_model_path()


class Downloader:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self.state = "idle"
        self.model_id = ""
        self.progress = 0.0
        self.received = 0
        self.total = 0
        self.error = ""
        self.message = ""

    # -- status ---------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            installed = _is_installed(self.model_id or "lama_roi")
            return {
                "state": self.state,
                "model_id": self.model_id,
                "progress": round(self.progress, 4),
                "received_bytes": self.received,
                "total_bytes": self.total,
                "error": self.error,
                "message": self.message,
                "installed": installed,
            }

    def start(self, model_id: str) -> None:
        # SeedVR2 models are not a file in a folder: installing one also creates
        # its PyTorch runtime, which is why it goes through its own installer.
        from engine.pipeline.seedvr import manifest as seedvr_manifest

        if model_id in seedvr_manifest.MODEL_SPECS:
            self._start_seedvr(model_id)
            return

        entry = REGISTRY.get(model_id)
        if entry is None:
            raise RpcError("INVALID_PARAMS", f"Unknown model: {model_id}", {"model_id": model_id})

        with self._lock:
            if self.state == "running":
                raise RpcError("INVALID_PARAMS", "a download is already running")
            if not entry["url"]:
                raise RpcError(
                    "NOT_IMPLEMENTED",
                    "This model has no downloadable release; build it with packaging/convert-esrgan.py",
                    {"model_id": model_id},
                )
            target = entry["target"]()
            if target.is_file() and target.stat().st_size > 0:
                self.model_id = model_id
                self.state = "success"
                self.progress = 1.0
                self.error = ""
                return

            self.model_id = model_id
            self.state = "running"
            self.progress = 0.0
            self.received = 0
            self.total = 0
            self.error = ""
            self._cancel.clear()
            self._thread = threading.Thread(target=self._run, args=(entry, target), daemon=True,
                                            name="model-download")
            self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    # -- SeedVR2 -------------------------------------------------------
    def _start_seedvr(self, model_id: str) -> None:
        from engine.pipeline.seedvr import capability as seedvr_capability
        from engine.pipeline.seedvr import manifest as seedvr_manifest
        from engine.pipeline.seedvr.runtime import SeedVRRuntime

        with self._lock:
            if self.state == "running":
                raise RpcError("INVALID_PARAMS", "a download is already running")
            if seedvr_manifest.model_installed(model_id) and seedvr_capability.runtime_installed():
                self.model_id = model_id
                self.state = "success"
                self.progress = 1.0
                return

            self.model_id = model_id
            self.state = "running"
            self.progress = 0.0
            self.received = 0
            self.total = seedvr_manifest.get_spec(model_id).size_bytes
            self.error = ""
            self._cancel.clear()

        def on_progress(payload: dict[str, Any]) -> None:
            with self._lock:
                if "progress" in payload:
                    self.progress = max(self.progress, min(0.99, float(payload["progress"])))
                if payload.get("message"):
                    self.message = str(payload["message"])

        def worker() -> None:
            runtime = SeedVRRuntime()
            try:
                runtime.install(model_id, progress=on_progress, cancel=self._cancel)
                with self._lock:
                    self.state = "success"
                    self.progress = 1.0
            except Exception as exc:  # noqa: BLE001 - reported through status
                cancelled = self._cancel.is_set()
                with self._lock:
                    self.state = "cancelled" if cancelled else "failed"
                    if not cancelled:
                        self.error = str(exc)[:400]

        self._thread = threading.Thread(target=worker, name="seedvr-install", daemon=True)
        self._thread.start()

    # -- worker ---------------------------------------------------------
    def _run(self, entry: dict[str, Any], target: Path) -> None:
        partial = target.with_suffix(target.suffix + ".part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(entry["url"], headers={"User-Agent": "eraser-engine/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length") or 0)
                with self._lock:
                    self.total = total
                received = 0
                with partial.open("wb") as sink:
                    while True:
                        if self._cancel.is_set():
                            raise _Cancelled()
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        sink.write(chunk)
                        received += len(chunk)
                        with self._lock:
                            self.received = received
                            if total:
                                self.progress = min(0.99, received / total)

            digest = sha256_file(partial)
            if digest != entry["sha256"]:
                partial.unlink(missing_ok=True)
                raise RuntimeError(f"checksum mismatch: expected {entry['sha256']}, got {digest}")

            partial.replace(target)
            with self._lock:
                self.state = "success"
                self.progress = 1.0
            logger.info("downloaded %s to %s", self.model_id, target)
        except _Cancelled:
            partial.unlink(missing_ok=True)
            with self._lock:
                self.state = "cancelled"
                self.error = ""
        except Exception as exc:  # noqa: BLE001 - reported through status
            partial.unlink(missing_ok=True)
            with self._lock:
                self.state = "failed"
                self.error = str(exc)[:300]
            logger.warning("model download failed: %s", exc)


class _Cancelled(RuntimeError):
    """Internal: the user cancelled the download."""


def _is_installed(model_id: str) -> bool:
    from engine.pipeline.seedvr import manifest as seedvr_manifest

    if model_id in seedvr_manifest.MODEL_SPECS:
        return seedvr_manifest.model_installed(model_id)
    if model_id in {"lama_roi"}:
        from engine.pipeline.remover import candidate_model_paths

        return any(path.is_file() and path.stat().st_size > 0 for path in candidate_model_paths())
    if model_id.startswith("realesrgan"):
        from engine.pipeline import upscaler

        return upscaler.is_installed()
    return False


_downloader = Downloader()


def register(server: RpcServer) -> None:
    def handle_status(_params: dict[str, Any]) -> dict[str, Any]:
        return _downloader.snapshot()

    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        _downloader.start(str(params.get("model_id", "")))
        return {"ok": True}

    def handle_cancel(_params: dict[str, Any]) -> dict[str, Any]:
        _downloader.cancel()
        return {"ok": True}

    server.register("model.download.status", handle_status)
    server.register("model.download.start", handle_start)
    server.register("model.download.cancel", handle_cancel)
