"""Pinned Florence-2 weights.

The files and their hashes are fixed here rather than resolved at runtime: the
model ships remote code (`modeling_florence2.py`), so a "latest" download is
executable code from the network. Pinning the revision and verifying every byte
is the difference between installing a model and running whatever the hub serves
today.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REVISION = "5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac"
BASE_URL = f"https://huggingface.co/microsoft/Florence-2-base/resolve/{REVISION}"

ProgressCallback = Callable[[float, str], None]
CHUNK = 1024 * 256


class DetectorInstallError(RuntimeError):
    """The detector runtime could not be installed."""


@dataclass(frozen=True)
class WeightFile:
    name: str
    bytes: int
    sha256: str


# 444.3 MB in total. Order matters only for progress reporting.
FILES: tuple[WeightFile, ...] = (
    WeightFile("config.json", 2_430, "c666d0fe0172d46e115e8fba6cd93cd83714575b33a73005cab8d24ce2a3aa8f"),
    WeightFile(
        "configuration_florence2.py",
        15_119,
        "de2e45a975b3582de05d2f4d963a3e9f9a3d20dccf78d28e0052932a0be93bdf",
    ),
    WeightFile(
        "model.safetensors",
        463_221_266,
        "03075d2d2d2bbd3e180b9ba0afae4aa8563226e2d32911656966e05b2f2ee060",
    ),
    WeightFile(
        "modeling_florence2.py",
        127_455,
        "5162bf465e61b6e29cc113a467630ec3cb56ed8e4d46eb6207157f10fb9b8a24",
    ),
    WeightFile("preprocessor_config.json", 806, "2f5921bbc53c7cc04251e1027b45b1cec726276be6db23d1bb40641bfbe2cf29"),
    WeightFile(
        "processing_florence2.py",
        48_676,
        "f146023a507c009f425a49ee39aa037f4f25c64e14336e3e4f3f1d7377a68e98",
    ),
    WeightFile("tokenizer.json", 1_355_863, "847bbeab6174d66a88898f729d52fa8d355fafe1bea101cf960dd404581df70e"),
    WeightFile("tokenizer_config.json", 34, "79ffcf43af8ebda99d165f61d243180da2e2639952e41e71e11611c18770489c"),
    WeightFile("vocab.json", 1_099_884, "394fdc63c71aabe0a9b97117f5d62fb5fcc4d59b2b3ea929a3929e6a53217b3c"),
)


def missing(destination: str | Path, *, verify_hash: bool = False) -> list[str]:
    """Which manifest files are not on disk (and, optionally, not intact).

    Readiness must consider *every* file: the 444 MB blob is the third of nine,
    so a cancelled install can leave the big one present and the model's own
    Python absent. Checking only `model.safetensors` reports "installed" for a
    runtime that cannot load.
    """
    root = Path(destination)
    absent: list[str] = []
    for item in FILES:
        path = root / item.name
        if not path.is_file() or path.stat().st_size != item.bytes:
            absent.append(item.name)
        elif verify_hash and _digest(path) != item.sha256:
            absent.append(item.name)
    return absent


def url_for(name: str, *, base_url: str = BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{name}"


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            hasher.update(block)
    return hasher.hexdigest()


def download_weight(
    url: str,
    destination: str | Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
    timeout: float = 600.0,
    progress: ProgressCallback | None = None,
    cancel: object | None = None,
) -> None:
    """Download one file, verifying size and hash; a bad file never survives."""
    target = Path(destination)
    if target.is_file() and target.stat().st_size == expected_bytes:
        if _digest(target) == expected_sha256:
            if progress is not None:
                progress(1.0, f"{target.name} already verified")
            return

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "eraser-engine/1"})
    hasher = hashlib.sha256()
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open(
            "wb"
        ) as sink:
            while True:
                if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                    raise DetectorInstallError(f"cancelled while downloading {target.name}")
                block = response.read(CHUNK)
                if not block:
                    break
                sink.write(block)
                hasher.update(block)
                received += len(block)
                if progress is not None:
                    share = min(1.0, received / max(1, expected_bytes))
                    progress(share, f"{target.name} {received // 1_048_576}/{expected_bytes // 1_048_576} MB")
        if received != expected_bytes:
            raise DetectorInstallError(
                f"size mismatch for {target.name}: expected {expected_bytes}, got {received}"
            )
        if hasher.hexdigest() != expected_sha256:
            raise DetectorInstallError(
                f"checksum mismatch for {target.name}: expected {expected_sha256}, "
                f"got {hasher.hexdigest()}"
            )
        os.replace(partial, target)
    except DetectorInstallError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - network errors become install errors
        partial.unlink(missing_ok=True)
        raise DetectorInstallError(f"cannot download {target.name}: {exc}") from exc


def download_all(
    destination: str | Path,
    *,
    progress: ProgressCallback | None = None,
    cancel: object | None = None,
    base_url: str = BASE_URL,
) -> None:
    root = Path(destination)
    total = sum(item.bytes for item in FILES)
    done = 0
    for item in FILES:
        def report(share: float, message: str, *, _done: int = done) -> None:
            if progress is not None:
                overall = (_done + share * item.bytes) / total
                progress(min(1.0, overall), message)

        download_weight(
            url_for(item.name, base_url=base_url),
            root / item.name,
            expected_sha256=item.sha256,
            expected_bytes=item.bytes,
            progress=report,
            cancel=cancel,
        )
        done += item.bytes
    if progress is not None:
        progress(1.0, "weights ready")
