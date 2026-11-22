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

import json
import os
import shutil
import socket
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
#: A lock older than this is presumed abandoned even if its pid looks alive
#: (pids get reused; a wedged install must not lock the feature forever).
STALE_LOCK_SECONDS = 6 * 3600
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


def _torch_cuda() -> bool:
    """Ask torch inside the runtime whether CUDA actually works.

    `nvidia-smi` on PATH means a driver exists, not that this torch build can use
    it; reporting the two as the same thing is how a user ends up waiting on a
    GPU that is silently running on the CPU.
    """
    try:
        completed = _run(
            [str(venv_python()), "-c", "import torch;print(1 if torch.cuda.is_available() else 0)"],
            timeout=180,
        )
    except DetectorInstallError as exc:
        logger.warning("cannot verify CUDA in the detector runtime: %s", exc)
        return False
    return completed.stdout.strip().endswith("1")


def _versions() -> dict[str, str]:
    """Record what was actually installed; the eval numbers only mean something
    next to the versions that produced them."""
    script = (
        "import json,torch,transformers,timm;"
        "print(json.dumps({'torch': torch.__version__,"
        " 'transformers': transformers.__version__, 'timm': timm.__version__}))"
    )
    try:
        completed = _run([str(venv_python()), "-c", script], timeout=180)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        return {str(key): str(value) for key, value in payload.items()}
    except (DetectorInstallError, ValueError, IndexError) as exc:
        logger.warning("cannot record detector versions: %s", exc)
        return {}


def _run(
    command: list[str],
    *,
    cancel: object | None = None,
    timeout: float = 3600.0,
) -> subprocess.CompletedProcess[str]:
    """Run a provisioning command, killing it promptly when cancelled.

    pip and venv setup can take minutes; a cancel that only takes effect after
    pip returns is not a cancel.
    """
    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
        raise DetectorInstallError("cancelled")
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise DetectorInstallError(f"cannot run {command[0]}: {exc}") from exc

    deadline = time.monotonic() + timeout
    reason = ""
    while process.poll() is None:
        if cancel is not None and getattr(cancel, "is_set", lambda: False)():
            reason = "cancelled"
            break
        if time.monotonic() > deadline:
            reason = f"timed out after {timeout:.0f}s"
            break
        time.sleep(0.2)
    if reason:
        process.kill()
    stdout, stderr = process.communicate()
    if reason:
        raise DetectorInstallError(f"{Path(command[0]).name} {reason}")
    if process.returncode != 0:
        tail = (stderr or stdout or "").strip()[-500:]
        raise DetectorInstallError(
            f"{Path(command[0]).name} exited {process.returncode}: {tail}"
        )
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


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
            ERROR_ACCESS_DENIED = 5
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # Access denied means the process exists and is not ours to open:
                # treating that as "dead" would let a second install trample it.
                return ctypes.get_last_error() == ERROR_ACCESS_DENIED
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
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

    def _owner(self) -> tuple[int, float]:
        """Return (pid, started_at). Older lock files held a bare pid."""
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return 0, 0.0
        try:
            payload = json.loads(raw)
            return int(payload.get("pid", 0)), float(payload.get("started_at", 0.0))
        except (ValueError, TypeError, AttributeError):
            try:
                return int(raw), 0.0
            except ValueError:
                return 0, 0.0

    def __enter__(self) -> "InstallLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                handle = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pid, started = self._owner()
                stale = started > 0 and (time.time() - started) > STALE_LOCK_SECONDS
                if pid and not stale and self._alive(pid):
                    raise DetectorInstallError(
                        f"another detector install is running (pid {pid}); "
                        f"if it is not, delete {self._path}"
                    )
                self._path.unlink(missing_ok=True)
                continue
            payload = {
                "pid": os.getpid(),
                "started_at": time.time(),
                "host": socket.gethostname(),
            }
            with os.fdopen(handle, "w", encoding="utf-8") as sink:
                sink.write(json.dumps(payload))
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

        def report(share: float, message: str) -> None:
            if progress is not None:
                progress(max(0.0, min(1.0, share)), message)

        try:
            # Dependencies and weights share one progress bar: reporting 1.0 when
            # pip finishes leaves the bar pinned at 100% through a 444 MB
            # download. Task progress is monotonic, so this cannot be undone later.
            _ensure_venv(
                cancel=cancel, progress=lambda share, message: report(0.05 * share, message)
            )
            _install_dependencies(
                cancel=cancel,
                progress=lambda share, message: report(0.05 + 0.55 * share, message),
            )
            weights.download_all(
                weights_dir(),
                progress=lambda share, message: report(0.6 + 0.4 * share, message),
                cancel=cancel,
            )
            report(0.99, "verifying the detector runtime")
            payload = {
                "phase": "ready",
                "python": str(worker_python()),
                "cuda": _torch_cuda(),
                "versions": _versions(),
            }
            write_state(payload)
            logger.info("detector runtime ready (cuda=%s)", payload["cuda"])
            return read_state()
        except Exception as exc:  # noqa: BLE001 - the state file is the report
            # A cancel is not a failure: the UI would keep offering "retry" for
            # something the user deliberately stopped.
            cancelled = "cancel" in str(exc).lower()
            write_state(
                {"phase": "missing" if cancelled else "failed", "error": str(exc)}
            )
            if isinstance(exc, DetectorInstallError):
                raise
            raise DetectorInstallError(str(exc)) from exc
