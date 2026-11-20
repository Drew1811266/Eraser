"""Platform abstraction: the single place where OS differences are allowed.

Nothing else in the engine may branch on `sys.platform` / `os.name`, or build
paths from hardcoded separators. See docs/02-target-architecture.md section 4.
"""

from __future__ import annotations

import platform as _pyplatform
import sys

__all__ = ["current_platform_key", "normalize_arch"]


def normalize_arch(machine: str | None) -> str:
    """Map vendor arch spellings onto the two names we use everywhere."""
    value = str(machine or "").strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64", "x64"}:
        return "x86_64"
    return value or "unknown"


def current_platform_key(
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the asset-directory key, e.g. ``win32-x86_64``.

    This must stay byte-identical to the keys produced by the Electron side
    (``getRuntimePlatformKey`` in desktop/electron/services/ffmpeg.ts), because
    both ends point at the same ``vendor/ffmpeg/<key>/`` directory.

    Note the Windows spelling is ``win32-x86_64``, not ``win32-x64``.
    """
    system = (system or sys.platform).strip().lower()
    resolved_machine = machine if machine is not None else _pyplatform.machine()
    arch = normalize_arch(resolved_machine)

    if system.startswith("win"):
        return f"win32-{arch}"
    if system == "darwin":
        return f"darwin-{arch}"
    if system.startswith("linux"):
        return f"linux-{arch}"
    return f"{system}-{arch}"
