"""Locate ffmpeg/ffprobe.

Search order (first hit wins):
  1. ``ERASER_FFMPEG_DIR`` override
  2. ``<app_root>/vendor/ffmpeg/<platform_key>/`` (bundled)
  3. ``<user_data>/runtime/ffmpeg/<platform_key>/`` (downloaded at runtime)
  4. ``PATH``

The reference implementation computed the platform key as ``windows-x86_64``
and looked for ``ffmpeg`` with no ``.exe``, so the bundled binary was never
found on Windows and the app silently fell back to PATH. Both are fixed here
and pinned by tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from engine.platform import current_platform_key
from engine.platform.paths import application_root, runtime_dir

ENV_FFMPEG_DIR = "ERASER_FFMPEG_DIR"

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

SOURCE_OVERRIDE = "override"
SOURCE_BUNDLED = "bundled"
SOURCE_RUNTIME = "runtime"
SOURCE_PATH = "path"


@dataclass(frozen=True)
class ToolResolution:
    tool: str
    path: str
    source: str

    @property
    def available(self) -> bool:
        return bool(self.path)


def executable_name(tool: str, platform_key: str | None = None) -> str:
    key = platform_key or current_platform_key()
    return f"{tool}.exe" if key.startswith("win32-") else tool


def candidate_dirs(platform_key: str | None = None) -> list[tuple[Path, str]]:
    """Directories to search, in priority order, paired with their source tag."""
    key = platform_key or current_platform_key()
    directories: list[tuple[Path, str]] = []

    override = os.environ.get(ENV_FFMPEG_DIR, "").strip()
    if override:
        directories.append((Path(override).expanduser(), SOURCE_OVERRIDE))

    directories.append((application_root() / "vendor" / "ffmpeg" / key, SOURCE_BUNDLED))
    directories.append((runtime_dir() / "ffmpeg" / key, SOURCE_RUNTIME))
    return directories


def _is_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    if os.name == "nt":
        # os.access(X_OK) is meaningless on Windows; existence is the check.
        return True
    return os.access(path, os.X_OK)


def resolve(tool: str, platform_key: str | None = None) -> ToolResolution:
    filename = executable_name(tool, platform_key)

    for directory, source in candidate_dirs(platform_key):
        candidate = directory / filename
        if _is_executable(candidate):
            return ToolResolution(tool=tool, path=str(candidate), source=source)

    on_path = shutil.which(filename) or shutil.which(tool)
    if on_path:
        return ToolResolution(tool=tool, path=on_path, source=SOURCE_PATH)

    return ToolResolution(tool=tool, path="", source="")


def describe_search_paths(platform_key: str | None = None) -> list[str]:
    """Human-readable search order, for the error message users actually read."""
    return [str(directory) for directory, _ in candidate_dirs(platform_key)]


@lru_cache(maxsize=8)
def _version_of(path: str) -> str:
    try:
        completed = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first_line = (completed.stdout or "").splitlines()
    return first_line[0].strip() if first_line else ""


def version(resolution: ToolResolution) -> str:
    return _version_of(resolution.path) if resolution.path else ""


def clear_cache() -> None:
    _version_of.cache_clear()
