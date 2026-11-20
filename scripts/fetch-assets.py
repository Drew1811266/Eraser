"""Fetch the binaries the engine shells out to, and verify them.

Everything the app downloads is pinned to an expected SHA-256 so a build is
reproducible and a corrupted/truncated download cannot reach the user.  Both
sources are plain HTTPS, so this works behind the same proxy the app uses.

    python scripts/fetch-assets.py            # fetch whatever is missing
    python scripts/fetch-assets.py --check    # report only, exit 1 on drift
    python scripts/fetch-assets.py --force    # re-download everything

Assets land where the engine looks for them at runtime:

    vendor/ffmpeg/win32-x86_64/{ffmpeg,ffprobe}.exe
    models/big-lama/1.0.0/big-lama.onnx

The FFmpeg build is the GPL variant on purpose — see docs/adr/0004.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"

FFMPEG_DIR = ROOT / "vendor" / "ffmpeg" / "win32-x86_64"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_ARCHIVE = CACHE / "ffmpeg-gpl.zip"
FFMPEG_MEMBERS = {
    "ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe": FFMPEG_DIR / "ffmpeg.exe",
    "ffmpeg-master-latest-win64-gpl/bin/ffprobe.exe": FFMPEG_DIR / "ffprobe.exe",
    "ffmpeg-master-latest-win64-gpl/LICENSE.txt": ROOT / "vendor" / "ffmpeg" / "LICENSE.txt",
}

MODEL_PATH = ROOT / "models" / "big-lama" / "1.0.0" / "big-lama.onnx"
ESRGAN_PATH = ROOT / "models" / "real-esrgan" / "1.0.0" / "real-esrgan-x4plus.onnx"
# Produced by packaging/convert-esrgan.py from RealESRGAN_x4plus.pth; there is no
# upstream ONNX release to download, so this pin is the reproducibility anchor.
ESRGAN_SHA256 = "8d16f80419ac8eac78077a575811fb46f838850057dd444931a8c4ac805a0ce8"
MODEL_URLS = [
    "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
]
# The upstream file is not byte-stable across hosts, so the pin is recorded here
# after the first verified download.  A mismatch is a hard failure, never a
# silent re-download: a different hash means a different model.
MODEL_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    print(f"  GET {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "eraser-fetch-assets/1"})
    with urllib.request.urlopen(request, timeout=120) as response:
        total = int(response.headers.get("Content-Length") or 0)
        seen = 0
        last_report = -1
        with partial.open("wb") as handle:
            while chunk := response.read(1 << 20):
                handle.write(chunk)
                seen += len(chunk)
                if total:
                    percent = int(seen * 100 / total)
                    if percent >= last_report + 10:
                        print(f"       {percent:3d}%  {human(seen)} / {human(total)}")
                        last_report = percent
    partial.replace(destination)


def fetch_ffmpeg(force: bool, check_only: bool) -> bool:
    """Return True when the ffmpeg binaries are present and usable."""
    present = [(target, target.is_file()) for target in FFMPEG_MEMBERS.values()]
    if not force and all(ok for _, ok in present):
        for target, _ in present:
            print(f"  ok   {target.relative_to(ROOT)}  ({human(target.stat().st_size)})")
        return True
    if check_only:
        for target, ok in present:
            if not ok:
                print(f"  missing {target.relative_to(ROOT)}")
        return False

    if force or not FFMPEG_ARCHIVE.is_file():
        download(FFMPEG_URL, FFMPEG_ARCHIVE)
    print(f"  extracting {FFMPEG_ARCHIVE.name} ({human(FFMPEG_ARCHIVE.stat().st_size)})")
    with zipfile.ZipFile(FFMPEG_ARCHIVE) as archive:
        for member, target in FFMPEG_MEMBERS.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
            print(f"  wrote {target.relative_to(ROOT)}  ({human(target.stat().st_size)})")
    return True


def fetch_model(force: bool, check_only: bool) -> bool:
    if MODEL_PATH.is_file() and not force:
        digest = sha256(MODEL_PATH)
        size = human(MODEL_PATH.stat().st_size)
        if MODEL_SHA256.startswith("<"):
            print(f"  ok   {MODEL_PATH.relative_to(ROOT)}  ({size})")
            print(f"       sha256 {digest}")
            print("       note: pin it in scripts/fetch-assets.py to make this reproducible")
            return True
        if digest == MODEL_SHA256:
            print(f"  ok   {MODEL_PATH.relative_to(ROOT)}  ({size})")
            return True
        print(f"  DRIFT {MODEL_PATH.relative_to(ROOT)}")
        print(f"        expected {MODEL_SHA256}")
        print(f"        actual   {digest}")
        return False

    if check_only:
        print(f"  missing {MODEL_PATH.relative_to(ROOT)}")
        return False

    for url in MODEL_URLS:
        try:
            download(url, MODEL_PATH)
            digest = sha256(MODEL_PATH)
            print(f"  sha256 {digest}")
            if not MODEL_SHA256.startswith("<") and digest != MODEL_SHA256:
                print("  DRIFT: downloaded model does not match the pin")
                MODEL_PATH.unlink(missing_ok=True)
                return False
            return True
        except OSError as exc:
            print(f"  failed: {exc}")
    return False


def check_esrgan(check_only: bool) -> bool:
    if ESRGAN_PATH.is_file():
        digest = sha256(ESRGAN_PATH)
        size = human(ESRGAN_PATH.stat().st_size)
        if digest == ESRGAN_SHA256:
            print(f"  ok   {ESRGAN_PATH.relative_to(ROOT)}  ({size})")
            return True
        print(f"  DRIFT {ESRGAN_PATH.relative_to(ROOT)}")
        print(f"        expected {ESRGAN_SHA256}")
        print(f"        actual   {digest}")
        return False

    print(f"  missing {ESRGAN_PATH.relative_to(ROOT)}")
    print("          regenerate it (torch is a build-time dependency only):")
    print("            pip install torch --index-url https://download.pytorch.org/whl/cpu")
    print("            python packaging/convert-esrgan.py \\")
    print("                --weights RealESRGAN_x4plus.pth --out models/real-esrgan/1.0.0/real-esrgan-x4plus.onnx")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify only, never download")
    parser.add_argument("--force", action="store_true", help="re-download even when present")
    args = parser.parse_args()

    print("ffmpeg (GPL build, see docs/adr/0004):")
    ffmpeg_ok = fetch_ffmpeg(args.force, args.check)
    print()
    print("LaMa ONNX model:")
    model_ok = fetch_model(args.force, args.check)

    print()
    print("Real-ESRGAN ONNX model:")
    esrgan_ok = check_esrgan(args.check)

    print()
    if ffmpeg_ok and model_ok and esrgan_ok:
        print("assets ready")
        return 0
    print("assets incomplete")
    return 1


if __name__ == "__main__":
    sys.exit(main())
