"""Facts about the ONNX Runtime build we are running on.

Both the remover (which has to pick execution providers before building a
session) and `system.capabilities` (which tells the shell what the machine can
do) need the same answers, so they are computed once here.

The important subtlety is that `onnxruntime.get_available_providers()` lies on
Windows: an `onnxruntime-gpu` wheel reports `CUDAExecutionProvider` even when
the CUDA/cuDNN DLLs are missing, and only fails once a session is created.  The
fallback is silent, so a user would see "GPU" in the UI while everything ran on
the CPU.  `cuda_usable()` therefore loads the provider DLL itself, which fails
for exactly the same reason ONNX Runtime's own load fails.
"""

from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path
from typing import Any

# Re-entrant on purpose: `cuda_usable()` holds the lock while probing, and the
# probe preloads the DLLs, which takes the same lock.
_lock = threading.RLock()
_preloaded = False
_cuda_usable: bool | None = None
# `os.add_dll_directory` hands back a handle that must outlive the call.
_dll_handles: list[Any] = []


def _import_ort() -> Any | None:
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    return ort


def bundled_cuda_dir() -> Path | None:
    """The CUDA runtime shipped beside a frozen engine, if there is one.

    PyInstaller cannot carry the `nvidia/*/bin` DLLs usefully: they are loaded
    by name at runtime, not at import time. The installer therefore drops them
    into `<install>/cuda` and this is where they are picked up.
    """
    import sys

    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    for candidate in (root / "cuda", root / "_internal" / "cuda"):
        if candidate.is_dir() and any(candidate.glob("*.dll")):
            return candidate
    return None


def available_providers() -> list[str]:
    """Execution providers this ONNX Runtime build claims to support."""
    ort = _import_ort()
    if ort is None:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001 - a broken install must not kill the engine
        return []


def preload_cuda_dlls() -> list[str]:
    """Load the CUDA DLLs shipped by the `nvidia-*` wheels.

    Modern wheels put cuBLAS/cuDNN under `site-packages/nvidia/*/bin`, which is
    not on the DLL search path.  `onnxruntime.preload_dlls()` registers those
    directories; without it the CUDA provider never initialises.

    Returns a list of human-readable notes (empty when everything went fine).
    """
    global _preloaded
    with _lock:
        if _preloaded:
            return []
        _preloaded = True

        bundled = bundled_cuda_dir()
        if bundled is not None:
            # Frozen layout: every CUDA DLL sits in one folder next to the exe.
            if os.name == "nt" and hasattr(os, "add_dll_directory"):
                _dll_handles.append(os.add_dll_directory(str(bundled)))
            os.environ["PATH"] = f"{bundled}{os.pathsep}{os.environ.get('PATH', '')}"
            return []

        ort = _import_ort()
        if ort is None:
            return ["onnxruntime is not installed"]
        preload = getattr(ort, "preload_dlls", None)
        if preload is None:
            return []

        try:
            preload()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return [f"preload_dlls failed: {exc}"]
        return []


def cuda_usable() -> bool:
    """True when the CUDA provider can actually be initialised.

    Cached: the probe dlopens the provider library, which is not free, and the
    answer cannot change while the process lives.
    """
    global _cuda_usable
    with _lock:
        if _cuda_usable is not None:
            return _cuda_usable
        _cuda_usable = _probe_cuda()
        return _cuda_usable


def _probe_cuda() -> bool:
    ort = _import_ort()
    if ort is None:
        return False
    if "CUDAExecutionProvider" not in available_providers():
        return False

    preload_cuda_dlls()

    capi = Path(ort.__file__).resolve().parent / "capi"
    library = capi / "onnxruntime_providers_cuda.dll"
    if not library.is_file():
        # Non-Windows wheels use a .so; linking it with the CUDA runtime absent
        # fails the same way, so the shared branch is fine.
        candidates = list(capi.glob("onnxruntime_providers_cuda.*"))
        if not candidates:
            return False
        library = candidates[0]

    try:
        if os.name == "nt":
            ctypes.WinDLL(str(library))
        else:
            ctypes.CDLL(str(library))
    except OSError:
        return False
    return True


def summary() -> dict[str, Any]:
    """Compact description for `system.capabilities`."""
    providers = available_providers()
    return {
        "available_providers": providers,
        "cuda_usable": cuda_usable() if "CUDAExecutionProvider" in providers else False,
        "onnxruntime": _version(),
    }


def _version() -> str | None:
    ort = _import_ort()
    return getattr(ort, "__version__", None) if ort is not None else None
