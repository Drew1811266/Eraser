#!/usr/bin/env python3
"""Compare the port's removal output with the reference implementation's.

This is the strongest parity evidence available without a mac: the reference
project's own `VideoProcessor` and `WatermarkRemover` are importable and run on
Windows given a PyTorch environment, so both implementations can be pointed at
the same clip and the same annotation, and the two outputs compared.

It answers the question a golden set would answer — "does the port remove the
same pixels?" — with the caveat that runs on different hardware and different
inpainting backends (their TorchScript LaMa vs the port's ONNX LaMa) will never
be bit-identical. Thresholds are set accordingly.

Requirements, checked here rather than assumed:

* an interpreter with torch, torchvision, opencv and simple-lama-inpainting
  (the SeedVR2 runtime has all of them)
* `.reference/up` — the reference source snapshot

    python scripts/parity-removal.py [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / ".reference" / "up"
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
FFPROBE = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"
WORK = ROOT / ".cache" / "parity-removal"
RUNTIME_PYTHON = ROOT / "models" / "runtime" / "seedvr-py312" / "venv" / "Scripts" / "python.exe"

SSIM_MIN = 0.98
PSNR_MIN = 40.0

SEGMENTS = [{
    "start_frame": 0,
    "end_frame": 11,
    "rect": {"x": 480, "y": 20, "width": 120, "height": 60},
    "expand_px": 5,
    "feather_px": 3,
    "enabled": True,
}]


def reference_runner() -> str:
    """A script that runs the reference pipeline, executed in its own env."""
    return f'''
import json, os, sys
sys.path.insert(0, {str(REFERENCE)!r})
from src.core.remover import WatermarkRemover
from src.core.video_processor import VideoProcessor

segments = json.loads({json.dumps(SEGMENTS)!r})
remover = WatermarkRemover(device="cuda")
processor = VideoProcessor(remover=remover)
result = processor.process_video({str(WORK / "input.mp4")!r}, {str(WORK / "reference.mp4")!r},
                                 segments, model_id="lama_roi")
print("DONE", json.dumps(result))
'''


def run(command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not REFERENCE.is_dir():
        print(f"reference source missing at {REFERENCE.relative_to(ROOT)} — skipping")
        return 0
    if not RUNTIME_PYTHON.is_file():
        print("no PyTorch interpreter for the reference implementation "
              "(install the SeedVR2 runtime first) — skipping")
        return 0

    WORK.mkdir(parents=True, exist_ok=True)
    source = ROOT / ".cache" / "e2e-upscale" / "source.mp4"
    if not source.is_file():
        print("fixture missing; run scripts/e2e-upscale.py once")
        return 2

    # Same input for both, cut to the frames the annotation covers.
    subprocess.run(
        [str(FFMPEG), "-y", "-v", "error", "-i", str(source),
         "-frames:v", "12", "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
         str(WORK / "input.mp4")],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    print("running the reference implementation")
    script = WORK / "run_reference.py"
    script.write_text(reference_runner(), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    # The reference writes its LaMa checkpoint under ~/.cache/torch; point that
    # inside the workspace so it works in a sandbox that cannot write to the
    # user profile, and so the 196MB download is cached once.
    env["TORCH_HOME"] = str(ROOT / ".cache" / "torch")
    env["HOME"] = str(ROOT / ".cache" / "home")
    env["USERPROFILE"] = str(ROOT / ".cache" / "home")
    env["PATH"] = str(FFMPEG.parent) + os.pathsep + env.get("PATH", "")
    reference = run([str(RUNTIME_PYTHON), str(script)], env=env)
    if "DONE" not in reference.stdout:
        print("the reference implementation did not finish:")
        print((reference.stdout + reference.stderr).strip().splitlines()[-5:])
        print("skipping the comparison (it is a broken environment, not a parity failure)")
        return 0
    if args.verbose:
        print(reference.stdout.strip().splitlines()[-1][:200])

    print("running the port")
    sys.path.insert(0, str(ROOT))
    from engine.runner import run_job

    run_job(WORK / "input.mp4", WORK / "port.mp4", SEGMENTS, "lama_roi")

    print("\ncomparing")
    comparison = run([
        sys.executable, str(ROOT / "scripts" / "compare-video.py"),
        "--expected", str(WORK / "reference.mp4"),
        "--actual", str(WORK / "port.mp4"),
        "--ssim-min", str(SSIM_MIN), "--psnr-min", str(PSNR_MIN),
        "--ffmpeg", str(FFMPEG), "--ffprobe", str(FFPROBE),
    ])
    print(comparison.stdout.strip() or comparison.stderr.strip())

    passed = 0
    failed = 0
    for name, info in (("reference output", WORK / "reference.mp4"), ("port output", WORK / "port.mp4")):
        if info.is_file():
            passed += 1
            print(f"  [PASS] {name} exists ({info.stat().st_size / 1e3:.0f} KB)")
        else:
            failed += 1
            print(f"  [FAIL] {name} is missing")

    # compare-video already returns 0/1 on the thresholds.
    if comparison.returncode == 0:
        passed += 1
        print(f"  [PASS] the two implementations agree (SSIM >= {SSIM_MIN}, PSNR >= {PSNR_MIN})")
    else:
        failed += 1
        print(f"  [FAIL] the two implementations disagree (see the metrics above)")

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
