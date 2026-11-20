"""Device discovery without importing torch.

Probing through nvidia-smi keeps engine startup fast: importing torch costs
seconds and we only need a name and a VRAM figure here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

CPU_PROBE_TIMEOUT = 1.5


def _no_window_kwargs() -> dict[str, Any]:
    """Suppress the console window that a child process would flash on Windows."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _cpu_device() -> dict[str, Any]:
    cores = os.cpu_count() or 1
    return {"kind": "cpu", "name": f"CPU ({cores} logical cores)"}


def _find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        candidate = os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def _nvidia_devices(timeout: float) -> list[dict[str, Any]]:
    executable = _find_nvidia_smi()
    if not executable:
        return []

    command = [
        executable,
        "--query-gpu=name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **_no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if completed.returncode != 0:
        return []

    devices: list[dict[str, Any]] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        entry: dict[str, Any] = {"kind": "cuda", "name": parts[0]}
        if len(parts) > 1:
            try:
                entry["memory_mb"] = float(parts[1])
            except ValueError:
                pass
        devices.append(entry)
    return devices


def detect_devices(timeout: float = CPU_PROBE_TIMEOUT) -> list[dict[str, Any]]:
    """Return the devices the engine may use, most capable first."""
    return [*_nvidia_devices(timeout), _cpu_device()]
