"""Every filesystem location the engine uses, resolved per platform.

Rules this module exists to enforce:

- No other module may hardcode ``/tmp``, ``~``, ``Downloads`` or a path
  separator. The reference implementation leaked all three.
- Windows paths that may exceed MAX_PATH get the ``\\\\?\\`` prefix; frame
  extraction writes tens of thousands of files into deep temp trees, which is
  exactly where 260-character paths break.
- Every location is overridable by an environment variable, so tests and the
  packaged app can redirect them without touching code.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "Eraser"

ENV_USER_DATA = "ERASER_USER_DATA_DIR"
ENV_TEMP = "ERASER_TEMP_DIR"
ENV_OUTPUT = "ERASER_OUTPUT_DIR"

# Windows rejects paths longer than this unless the extended-length form is used.
WINDOWS_MAX_PATH = 260


def application_root() -> Path:
    """Directory that contains ``vendor/`` and, in development, ``engine/``."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # engine/platform/paths.py -> engine/platform -> engine -> <root>
    return Path(__file__).resolve().parents[2]


def user_data_dir() -> Path:
    override = os.environ.get(ENV_USER_DATA, "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


def temp_dir() -> Path:
    """Scratch space for previews and temporary encodes.

    Three candidates in order: the explicit override, the system temp, and the
    application data directory. The last one exists because `tempfile.gettempdir()`
    can answer with the *current working directory* when the system temp is not
    writable — which is how a scratch folder ended up inside the user's project
    instead of a temp directory. An application must never write scratch into
    wherever it happened to be launched from.
    """
    override = os.environ.get(ENV_TEMP, "").strip()
    if override:
        return Path(override).expanduser()

    working = Path.cwd().resolve()
    for root in (Path(tempfile.gettempdir()) / APP_NAME.lower(), user_data_dir() / "temp"):
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved == working or working in resolved.parents:
            # `gettempdir()` falls back to the working directory when the system
            # temp is unusable; writing scratch beside the user's files is worse
            # than failing to find a temp directory.
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return root
        except OSError:
            continue

    # Nothing is writable; the caller's mkdir will fail with a real error rather
    # than silently scattering files through the working directory.
    return user_data_dir() / "temp"


def models_dir() -> Path:
    return user_data_dir() / "models"


def logs_dir() -> Path:
    return user_data_dir() / "logs"


def runtime_dir() -> Path:
    """Location for tools the app downloaded itself (vs. the bundled ones)."""
    return user_data_dir() / "runtime"


def default_output_dir() -> Path:
    override = os.environ.get(ENV_OUTPUT, "").strip()
    if override:
        return Path(override).expanduser()
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads / APP_NAME
    return user_data_dir() / "output"


def work_dir(task_id: str) -> Path:
    """Scratch space for one task. Caller is responsible for removing it."""
    safe = "".join(char for char in task_id if char.isalnum() or char in "-_") or "task"
    return temp_dir() / safe


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def extended_path(path: Path | str) -> str:
    """Return a form of *path* that Windows will accept even when long.

    On POSIX this is the identity function. Paths that are already extended are
    returned unchanged so the helper is safe to nest.
    """
    text = str(path)
    if os.name != "nt":
        return text

    if text.startswith("\\\\?\\"):
        return text

    absolute = os.path.abspath(text)
    if len(absolute) < WINDOWS_MAX_PATH and not absolute.startswith("\\\\"):
        return absolute

    if absolute.startswith("\\\\"):  # UNC share
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute
