"""Install and locate the Florence-2 runtime.

Same shape as the SeedVR2 runtime on purpose: a private venv, pinned
dependencies, verified weights, a state file and an install lock. The difference
is scope — this one only has to make a detector run, and it must never make the
engine import torch.

    state.json  {"phase": "ready"|"installing"|"failed"|"missing",
                 "model_id": "florence2", "python": "...", "cuda": true,
                 "error": "..."}
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from engine.pipeline.detect import weights
from engine.pipeline.detect.weights import DetectorInstallError, ProgressCallback
from engine.pipeline.logging import logger

DETECTOR_ID = "florence2"
HOME_ENV = "ERASER_DETECTOR_HOME"
STATE_FILE = "state.json"
LOCK_FILE = "install.lock"
#: transformers 5.x breaks Florence-2's remote code (`forced_bos_token_id`).
#: The other pins are the versions the spike proved.
PINNED = ("transformers==4.46.3", "timm==1.0.31", "einops==0.8.1", "pillow>=10")
CUDA_INDEX = "https://download.pytorch.org/whl/cu128"


def detector_home() -> Path:
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override)
    from engine.platform.paths import models_dir

    return models_dir() / DETECTOR_ID


def venv_dir() -> Path:
    return detector_home() / "venv"


def venv_python() -> Path:
    if sys.platform == "win32":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def weights_dir() -> Path:
    return detector_home() / "weights"


def state_path() -> Path:
    return detector_home() / STATE_FILE


def read_state() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {"phase": "missing", "model_id": DETECTOR_ID}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"phase": "missing", "model_id": DETECTOR_ID}
    return payload if isinstance(payload, dict) else {"phase": "missing", "model_id": DETECTOR_ID}


def write_state(payload: dict[str, Any]) -> None:
    import json

    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {"model_id": DETECTOR_ID, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **payload}
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def state() -> dict[str, Any]:
    return read_state()


def worker_python() -> Path:
    recorded = str(read_state().get("python") or "")
    if recorded:
        candidate = Path(recorded)
        if candidate.is_file():
            return candidate
    return venv_python()


def is_ready() -> bool:
    """Ready means the model can actually run, not that a flag was written.

    A state file survives a user deleting `weights/` or a half-finished install;
    trusting it is how "available" turns into a mysterious crash later.
    """
    if read_state().get("phase") != "ready":
        return False
    if not worker_python().is_file():
        return False
    return (weights_dir() / "model.safetensors").is_file()


def _detect_cuda() -> bool:
    return shutil.which("nvidia-smi") is not None


def _run(
    command: list[str],
    *,
    cancel: object | None = None,
    timeout: float = 3600.0,
) -> subprocess.CompletedProcess[str]:
    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
        raise DetectorInstallError("cancelled")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DetectorInstallError(f"cannot run {command[0]}: {exc}") from exc
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip()[-500:]
        raise DetectorInstallError(
            f"{Path(command[0]).name} exited {completed.returncode}: {tail}"
        )
    return completed


def _ensure_venv(*, cancel: object | None = None, progress: ProgressCallback | None = None) -> None:
    python = venv_python()
    if python.is_file():
        return
    if progress is not None:
        progress(0.0, "creating the detector runtime")
    _run([sys.executable, "-m", "venv", str(venv_dir())], cancel=cancel, timeout=600)
    if not python.is_file():
        raise DetectorInstallError(f"venv creation produced no interpreter at {python}")


def _install_dependencies(
    *, cancel: object | None = None, progress: ProgressCallback | None = None
) -> None:
    python = str(venv_python())
    if _detect_cuda():
        _run(
            [
                python, "-m", "pip", "install", "--no-cache-dir",
                "torch", "torchvision", "--index-url", CUDA_INDEX,
            ],
            cancel=cancel,
        )
        if progress is not None:
            progress(0.6, "PyTorch (CUDA) installed")
    else:
        _run(
            [python, "-m", "pip", "install", "--no-cache-dir", "torch", "torchvision"],
            cancel=cancel,
        )
        if progress is not None:
            progress(0.5, "PyTorch (CPU) installed")

    _run([python, "-m", "pip", "install", "--no-cache-dir", *PINNED], cancel=cancel)
    if progress is not None:
        progress(1.0, "detector dependencies installed")


class InstallLock:
    """One install at a time; a stale pid file is cleared rather than trusted."""

    def __init__(self) -> None:
        self._path = detector_home() / LOCK_FILE
        self._held = False

    @staticmethod
    def _alive(pid: int) -> bool:
        """Is this pid a live process?

        On Windows `os.kill(pid, 0)` is NOT a liveness probe — CPython forwards
        anything that is not CTRL_C_EVENT/CTRL_BREAK_EVENT to TerminateProcess,
        so using it here would kill the install we are trying to detect. Ask the
        kernel instead.
        """
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _owner(self) -> int:
        try:
            return int(self._path.read_text(encoding="utf-8").strip() or 0)
        except (OSError, ValueError):
            return 0

    def __enter__(self) -> "InstallLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                handle = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pid = self._owner()
                if self._alive(pid):
                    raise DetectorInstallError(
                        f"another detector install is running (pid {pid})"
                    )
                self._path.unlink(missing_ok=True)
                continue
            with os.fdopen(handle, "w", encoding="utf-8") as sink:
                sink.write(str(os.getpid()))
            self._held = True
            return self
        raise DetectorInstallError("cannot take the install lock")

    def __exit__(self, *_exc: object) -> None:
        if self._held:
            self._path.unlink(missing_ok=True)
            self._held = False


def install(
    *, progress: ProgressCallback | None = None, cancel: object | None = None
) -> dict[str, Any]:
    """Provision the runtime and the weights; resumable and idempotent."""
    with InstallLock():
        write_state({"phase": "installing"})
        cuda = _detect_cuda()
        try:
            _ensure_venv(cancel=cancel, progress=progress)
            _install_dependencies(cancel=cancel, progress=progress)
            weights.download_all(weights_dir(), progress=progress, cancel=cancel)
            payload = {
                "phase": "ready",
                "python": str(worker_python()),
                "cuda": cuda,
            }
            write_state(payload)
            logger.info("detector runtime ready (cuda=%s)", cuda)
            return read_state()
        except Exception as exc:  # noqa: BLE001 - the state file is the report
            write_state({"phase": "failed", "error": str(exc)})
            if isinstance(exc, DetectorInstallError):
                raise
            raise DetectorInstallError(str(exc)) from exc
