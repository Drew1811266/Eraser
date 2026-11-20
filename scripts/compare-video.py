#!/usr/bin/env python3
"""Compare two videos with FFmpeg's SSIM/PSNR filters and emit a verdict.

Used as the acceptance gate for the Eraser Windows port: the mac build's
output is the reference ("expected"), the new build's output is "actual".

Exit codes:
    0  metrics at or above thresholds
    1  metrics below thresholds
    2  tooling/usage error

Requires ffmpeg + ffprobe on PATH, or pass --ffmpeg / --ffprobe explicitly.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SSIM_RE = re.compile(r"All:([0-9.]+)")
PSNR_RE = re.compile(r"average:([0-9.]+)")


def resolve_tool(explicit: str | None, name: str) -> str | None:
    """Resolve an executable: explicit path first, then PATH."""
    if explicit:
        candidate = Path(explicit)
        return str(candidate) if candidate.is_file() else None
    return shutil.which(name)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def probe_duration(ffprobe: str, path: Path) -> float:
    result = run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    if result.returncode != 0:
        return 0.0
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def measure(ffmpeg: str, expected: Path, actual: Path) -> dict[str, float]:
    """Run ssim and psnr filters; each needs its own pass because both
    write to stderr and reading two filters in one command complicates
    parsing more than the extra decode costs."""
    metrics: dict[str, float] = {}

    ssim_run = run(
        [
            ffmpeg, "-hide_banner", "-nostats",
            "-i", str(expected), "-i", str(actual),
            "-lavfi", "[0:v][1:v]ssim",
            "-f", "null", "-",
        ]
    )
    # FFmpeg prints the summary on stderr.
    match = SSIM_RE.search(ssim_run.stderr)
    if match:
        metrics["ssim"] = float(match.group(1))

    psnr_run = run(
        [
            ffmpeg, "-hide_banner", "-nostats",
            "-i", str(expected), "-i", str(actual),
            "-lavfi", "[0:v][1:v]psnr",
            "-f", "null", "-",
        ]
    )
    match = PSNR_RE.search(psnr_run.stderr)
    if match:
        metrics["psnr"] = float(match.group(1))

    return metrics


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Compare two videos (SSIM/PSNR).")
    parser.add_argument("--expected", required=True, help="reference video")
    parser.add_argument("--actual", required=True, help="video to check")
    parser.add_argument("--ssim-min", type=float, default=0.98)
    parser.add_argument("--psnr-min", type=float, default=40.0)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    ffmpeg = resolve_tool(args.ffmpeg, "ffmpeg")
    ffprobe = resolve_tool(args.ffprobe, "ffprobe")
    if not ffmpeg or not ffprobe:
        print("error: ffmpeg/ffprobe not found. Pass --ffmpeg/--ffprobe.", file=sys.stderr)
        return 2

    expected = Path(args.expected)
    actual = Path(args.actual)
    for path in (expected, actual):
        if not path.is_file():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2

    durations = {
        "expected_seconds": round(probe_duration(ffprobe, expected), 3),
        "actual_seconds": round(probe_duration(ffprobe, actual), 3),
    }

    metrics = measure(ffmpeg, expected, actual)
    if "ssim" not in metrics:
        print("error: could not parse SSIM from ffmpeg output", file=sys.stderr)
        return 2

    ssim = metrics["ssim"]
    psnr = metrics.get("psnr", 0.0)
    passed = ssim >= args.ssim_min and psnr >= args.psnr_min

    report = {
        "passed": passed,
        "expected": str(expected),
        "actual": str(actual),
        "metrics": {"ssim": ssim, "psnr": psnr, **durations},
        "thresholds": {"ssim_min": args.ssim_min, "psnr_min": args.psnr_min},
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] SSIM={ssim:.4f} (min {args.ssim_min})  "
              f"PSNR={psnr:.2f}dB (min {args.psnr_min})")
        if durations["expected_seconds"] and durations["actual_seconds"]:
            delta = abs(durations["expected_seconds"] - durations["actual_seconds"])
            if delta > 0.5:
                print(f"  warning: duration differs by {delta:.2f}s "
                      f"({durations['expected_seconds']}s vs {durations['actual_seconds']}s)")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
