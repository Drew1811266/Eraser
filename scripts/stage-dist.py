"""Assemble the portable app folder that the installer packs.

Layout, and why:

    dist/Eraser/
      engine/eraser-engine.exe   frozen Python sidecar (onedir)
      engine/models/...          LaMa weights — the engine searches next to
                                 its own executable first
      engine/vendor/ffmpeg/...   ffmpeg/ffprobe, same reason
      engine/cuda/               optional CUDA runtime DLLs

The engine and its payload sit in one directory because `application_root()`
resolves to the executable's folder when frozen; keeping them together means the
packaged app and the development tree follow the same lookup order.

    python scripts/stage-dist.py            # CPU build
    python scripts/stage-dist.py --cuda     # + CUDA runtime (~2 GB)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
FROZEN = DIST / "eraser-engine"
STAGE = DIST / "Eraser"
ENGINE_OUT = STAGE / "engine"


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode})")


def freeze(python: str) -> None:
    run([
        python, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / ".cache" / "pyi-build"),
        str(ROOT / "packaging" / "engine.spec"),
    ])


def copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    print(f"  {target.relative_to(ROOT)}  ({size / 1e6:.1f} MB)")


def stage_cuda(engine_out: Path) -> None:
    """Copy the cuBLAS/cuDNN DLLs the CUDA provider needs at runtime.

    The wheels do not agree on a layout — `nvidia-cublas` puts everything under
    `nvidia/cu13/bin/x86_64`, while `nvidia-cudnn-cu13` uses `nvidia/cudnn/bin` —
    so the copy is a recursive glob flattened into one folder. Flat is also what
    the runtime wants: the provider loads its dependencies by bare filename.
    """
    site = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not site.is_dir():
        raise SystemExit(
            "no nvidia packages in this environment; install them first:\n"
            "  pip install nvidia-cuda-runtime nvidia-cublas nvidia-cudnn-cu13"
        )

    target = engine_out / "cuda"
    target.mkdir(parents=True, exist_ok=True)
    total = 0
    seen: set[str] = set()
    for dll in sorted(site.rglob("*.dll")):
        if "nvrtc" in dll.name:
            # Only needed when kernels are JIT-compiled; the LaMa graph is not.
            continue
        if dll.name in seen:
            raise SystemExit(f"duplicate CUDA dll {dll.name} at {dll}; remove the stale package")
        seen.add(dll.name)
        shutil.copy2(dll, target / dll.name)
        total += dll.stat().st_size
    print(f"  {target.relative_to(ROOT)}  ({len(seen)} dlls, {total / 1e6:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=str(ROOT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--cuda", action="store_true", help="bundle the CUDA runtime DLLs")
    parser.add_argument("--skip-freeze", action="store_true", help="reuse dist/eraser-engine")
    args = parser.parse_args()

    if not args.skip_freeze or not (FROZEN / "eraser-engine.exe").is_file():
        print("== freezing the engine ==")
        freeze(args.python)

    if not (FROZEN / "eraser-engine.exe").is_file():
        raise SystemExit(f"frozen engine missing at {FROZEN}")

    print("== staging ==")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    copy_tree(FROZEN, ENGINE_OUT)
    copy_tree(ROOT / "models", ENGINE_OUT / "models")
    copy_tree(ROOT / "vendor" / "ffmpeg", ENGINE_OUT / "vendor" / "ffmpeg")
    if args.cuda:
        stage_cuda(ENGINE_OUT)

    total = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    print()
    print(f"staged {STAGE.relative_to(ROOT)}  ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
