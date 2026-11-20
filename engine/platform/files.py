"""File operations that survive Windows.

The two facts driving this module:

- Windows refuses to delete or rename a file while another handle is open on it
  (antivirus, an indexer, or our own half-closed preview stream). A bare
  ``rmtree`` therefore fails intermittently and, in the reference build, left
  multi-gigabyte temp frame directories behind.
- A crash mid-write leaves a truncated file that later looks valid. Everything
  that matters is written to a sibling temp file and renamed into place.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

DEFAULT_RETRIES = 6
DEFAULT_DELAY_SECONDS = 0.25


def ensure_dir(path: Path | str) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write(path: Path | str, data: bytes | str) -> Path:
    """Write *data* to *path* so readers never observe a partial file."""
    target = Path(path)
    ensure_dir(target.parent)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")

    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"}
    with open(tmp, mode, **kwargs) as handle:  # noqa: PTH123
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp, target)
    return target


def remove_file(path: Path | str, *, retries: int = DEFAULT_RETRIES) -> bool:
    """Delete a file, retrying while Windows still has a handle on it."""
    target = Path(path)
    for attempt in range(retries):
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt == retries - 1:
                return False
            time.sleep(DEFAULT_DELAY_SECONDS * (attempt + 1))
        except OSError:
            if attempt == retries - 1:
                return False
            time.sleep(DEFAULT_DELAY_SECONDS)
    return False


def remove_tree(path: Path | str, *, retries: int = DEFAULT_RETRIES) -> bool:
    """Delete a directory tree, tolerating transient locks."""
    target = Path(path)
    if not target.exists():
        return True

    for attempt in range(retries):
        try:
            shutil.rmtree(target)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == retries - 1:
                return False
            time.sleep(DEFAULT_DELAY_SECONDS * (attempt + 1))
    return False


def clean_stale(root: Path | str, *, max_age_seconds: float) -> list[str]:
    """Remove entries under *root* older than *max_age_seconds*.

    Called at engine start to collect scratch directories orphaned by a crash.
    """
    base = Path(root)
    if not base.is_dir():
        return []

    now = time.time()
    removed: list[str] = []

    for entry in base.iterdir():
        try:
            modified = entry.stat().st_mtime
        except OSError:
            continue

        if now - modified < max_age_seconds:
            continue  # still fresh, leave it alone

        if entry.is_dir():
            if remove_tree(entry):
                removed.append(str(entry))
        elif remove_file(entry):
            removed.append(str(entry))

    return removed


def sha256_file(path: Path | str, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:  # noqa: PTH123
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
