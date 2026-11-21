"""Verify the shipped installers, not just the tree they were built from.

"Builds" and "installs" are different claims. This script unpacks each NSIS
package, checks the payload is complete, and then *runs the engine out of the
unpacked tree* — the exact layout the installer produces — to prove the shipped
binary and its bundled models, ffmpeg and CUDA DLLs still work after being
compressed into an installer.

The install itself cannot be exercised here (NSIS needs an interactive desktop,
same as Electron), so this is the closest verifiable step:

    installer → payload on disk → real inference

    python scripts/e2e-installer.py [--keep]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLERS = ROOT / "dist" / "installer"
FFPROBE = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"
CLIP = ROOT / ".cache" / "esrgan" / "clip12.mp4"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def newest_source_mtime() -> float:
    """When the shipped code last changed.

    An installer older than the code it was built from is worse than no check at
    all: it passes, and the reader believes the artefact matches HEAD.
    """
    newest = 0.0
    roots = ["engine", "contract", "desktop/electron", "desktop/renderer", "desktop/shared"]
    for root in roots:
        for path in (ROOT / root).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in {".pyc", ".map"}:
                continue
            newest = max(newest, path.stat().st_mtime)
    return newest


def find_7zip() -> Path | None:
    for pattern in (
        ".cache/electron-builder/**/7za.exe",
        "desktop/node_modules/7zip-bin/**/7za.exe",
    ):
        for candidate in ROOT.glob(pattern):
            return candidate
    on_path = shutil.which("7z") or shutil.which("7za")
    return Path(on_path) if on_path else None


def extract(seven: Path, installer: Path, target: Path) -> bool:
    if target.exists():
        shutil.rmtree(target)
    completed = subprocess.run(
        [str(seven), "x", "-y", str(installer), f"-o{target}"],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        print(completed.stderr.decode("utf-8", "replace")[-800:])
    return completed.returncode == 0


def run_engine(engine: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(engine), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def probe(path: Path) -> tuple[int, int, int]:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,nb_frames", "-of", "csv=p=0", str(path)],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    parts = result.stdout.decode("utf-8", "replace").strip().split(",")
    return int(parts[0]), int(parts[1]), int(parts[2] or 0)


def check_installer(seven: Path, installer: Path, expect_cuda: bool, scratch: Path) -> None:
    print(f"\n== {installer.name} ==")
    size = installer.stat().st_size / 1e6
    print(f"  installer: {size:.0f} MB")

    target = scratch / installer.stem
    if not extract(seven, installer, target):
        check("installer unpacks", False)
        return
    check("installer unpacks", True)

    engine = target / "resources" / "engine" / "eraser-engine.exe"
    required = [
        target / "Eraser.exe",
        target / "resources" / "app.asar",
        engine,
        target / "resources" / "engine" / "models" / "big-lama" / "1.0.0" / "big-lama.onnx",
        target / "resources" / "engine" / "models" / "real-esrgan" / "1.0.0" / "real-esrgan-x4plus.onnx",
        target / "resources" / "engine" / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe",
    ]
    missing = [path.name for path in required if not path.is_file()]
    check("payload is complete", not missing, ", ".join(missing) if missing else f"{len(required)} files")

    cuda_dir = target / "resources" / "engine" / "cuda"
    cuda_dlls = sorted(cuda_dir.glob("*.dll")) if cuda_dir.is_dir() else []
    if expect_cuda:
        names = {dll.name for dll in cuda_dlls}
        check("CUDA runtime is bundled", {"cudart64_13.dll", "cublas64_13.dll", "cudnn64_9.dll"} <= names,
              f"{len(cuda_dlls)} DLLs")
    else:
        check("CUDA runtime is absent (CPU build)", not cuda_dlls, f"{len(cuda_dlls)} DLLs")

    if not engine.is_file():
        return

    # The engine import graph grew an optional runtime (`seedvr`), imported
    # inside handlers. If the freezer missed it, the packaged app would offer
    # SeedVR2 and fail when asked — so ask the shipped binary directly.
    # The engine speaks JSON-RPC on stdin; one line, then EOF, is a whole session.
    capabilities = subprocess.run(
        [str(engine)],
        input='{"jsonrpc":"2.0","id":1,"method":"upscale.capabilities","params":{}}\n',
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    capability_text = (capabilities.stdout or "") + (capabilities.stderr or "")
    check("the shipped engine answers upscale.capabilities",
          "realesrgan" in capability_text and "seedvr2" in capability_text,
          (capability_text.strip().splitlines() or [""])[-1][:80])

    info = run_engine(engine, ["info"])
    text = (info.stdout or "") + (info.stderr or "")
    check("engine reports its runtime", "onnxruntime" in text,
          next((line.strip() for line in text.splitlines() if line.strip().startswith("runtime")), "")[:90])

    # The CPU build must not claim CUDA even on a machine that has a GPU.
    claims_cuda = "cuda_usable': True" in text.replace('"', "'")
    check("engine reports CUDA availability truthfully", claims_cuda == expect_cuda,
          f"cuda_usable={claims_cuda}")

    if not CLIP.is_file():
        check("a short clip is available to process", False, str(CLIP))
        return

    output = scratch / f"{installer.stem}-out.mp4"
    job = run_engine(engine, ["process", str(CLIP), "--rect", "480,20,120,60",
                              "--output", str(output), "--quiet"])
    finished = job.returncode == 0 and output.is_file()
    check("engine processes a video from the unpacked payload", finished,
          "" if finished else (job.stderr or "")[-200:].replace("\n", " "))
    if finished:
        width, height, frames = probe(output)
        check("output keeps its geometry", (width, height) == (640, 360), f"{width}x{height}")
        check("output keeps its frame count", frames == 12, str(frames))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    seven = find_7zip()
    if seven is None:
        print("7-Zip not found; skipping (build the installers first)")
        return 0

    installers = sorted(INSTALLERS.glob("Eraser-Setup-*.exe"))
    if not installers:
        print("no installers in dist/installer; run scripts/release.py first")
        return 1

    newest = newest_source_mtime()
    stale = [installer.name for installer in installers if installer.stat().st_mtime < newest]
    check("installers are newer than the code they ship", not stale,
          f"stale: {', '.join(stale)} — run scripts/release.py" if stale else "")

    scratch = Path(tempfile.mkdtemp(prefix="eraser-installer-", dir=str(ROOT / ".cache")))
    try:
        for installer in installers:
            is_gpu = "-GPU-" in installer.name
            check_installer(seven, installer, expect_cuda=is_gpu, scratch=scratch)
    finally:
        if args.keep:
            print(f"\nkept {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
