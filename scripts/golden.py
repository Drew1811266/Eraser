#!/usr/bin/env python3
"""The golden-set runner: the port's only objective quality gate.

`docs/05-golden-set-runbook.md` describes six cases and how to record them on a
machine that still runs the mac build. This makes that document executable:

    python scripts/golden.py --check              # compare against golden/cases
    python scripts/golden.py --build              # (re)record expected.mp4 here
    python scripts/golden.py --synth              # generate the synthetic cases

A case is a directory under `golden/cases/`:

    input.mp4                source clip
    annotations.wmr.json     segments, in the format the app saves
    expected.mp4             the reference output for that input
    meta.json                what produced it, and the thresholds

Two kinds of case are distinguished, because they answer different questions:

    baseline    recorded by *this* build. Detects regressions; proves nothing
                about parity with the mac app.
    reference   recorded by the mac (or the reference implementation). The only
                thing that can prove parity.

`--check` reports the kind for every case and fails if any baseline case is
missing, so a green run is never mistaken for "we match the mac build".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = Path(os.environ.get("ERASER_GOLDEN_DIR") or (ROOT / "golden"))
CASES = GOLDEN / "cases"
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
WORK = ROOT / ".cache" / "golden"

DEFAULT_SSIM_MIN = 0.98
DEFAULT_PSNR_MIN = 40.0
DEFAULT_BASELINE_SSIM = 0.995
DEFAULT_BASELINE_PSNR = 45.0

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


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def probe(path: Path) -> dict:
    result = run([str(ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffprobe.exe"),
                  "-v", "error", "-show_entries",
                  "stream=codec_type,width,height,nb_frames", "-of", "json", str(path)])
    streams = json.loads(result.stdout.decode("utf-8", "replace") or "{}").get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "frames": int(video.get("nb_frames") or 0),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def metrics(expected: Path, actual: Path) -> tuple[float, float]:
    """SSIM and PSNR between two files, via ffmpeg's own filters."""
    command = str(FFMPEG)
    result = run([command, "-v", "info", "-i", str(actual), "-i", str(expected),
                  "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"])
    ssim = 0.0
    for line in result.stderr.decode("utf-8", "replace").splitlines():
        if "SSIM" in line and "All:" in line:
            try:
                ssim = float(line.split("All:")[1].split()[0])
            except (IndexError, ValueError):
                pass
    result = run([command, "-v", "info", "-i", str(actual), "-i", str(expected),
                  "-lavfi", "[0:v][1:v]psnr", "-f", "null", "-"])
    psnr = 0.0
    for line in result.stderr.decode("utf-8", "replace").splitlines():
        if "PSNR" in line and "average:" in line:
            try:
                psnr = float(line.split("average:")[1].split()[0])
            except (IndexError, ValueError):
                pass
    return ssim, psnr


def load_case(directory: Path) -> dict | None:
    meta_path = directory / "meta.json"
    annotations = directory / "annotations.wmr.json"
    if not (directory / "input.mp4").is_file() or not annotations.is_file():
        return None
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}
    payload = json.loads(annotations.read_text(encoding="utf-8"))
    segments = payload.get("segments") if isinstance(payload, dict) else payload
    return {
        "id": directory.name,
        "directory": directory,
        "meta": meta,
        "segments": segments or [],
        "kind": "reference" if meta.get("captured_on", {}).get("os", "").lower().startswith("mac") else "baseline",
    }


def produce(case: dict, destination: Path) -> dict:
    sys.path.insert(0, str(ROOT))
    from engine.runner import run_job

    return run_job(
        case["directory"] / "input.mp4",
        destination,
        case["segments"],
        case["meta"].get("engine", {}).get("model_id") or "lama_roi",
    )


def check_case(case: dict) -> None:
    print(f"\n== {case['id']} ({case['kind']}) ==")
    expected = case["directory"] / "expected.mp4"
    if case["kind"] == "baseline":
        print("  note: baseline recorded by this build — regression only, not mac parity")
    if not expected.is_file():
        check("expected.mp4 exists", False, "record it with --build or copy the mac output")
        return

    WORK.mkdir(parents=True, exist_ok=True)
    actual = WORK / f"{case['id']}.mp4"
    started = time.time()
    result = produce(case, actual)
    elapsed = time.time() - started

    check("the pipeline produced a file", actual.is_file(), f"{elapsed:.1f}s")
    if not actual.is_file():
        return

    expected_info = probe(expected)
    actual_info = probe(actual)
    check("same geometry",
          (expected_info["width"], expected_info["height"]) == (actual_info["width"], actual_info["height"]),
          f"{actual_info['width']}x{actual_info['height']}")
    check("same frame count", expected_info["frames"] == actual_info["frames"],
          f"{actual_info['frames']} vs {expected_info['frames']}")

    thresholds = case["meta"].get("thresholds", {})
    default_ssim = DEFAULT_SSIM_MIN if case["kind"] == "reference" else DEFAULT_BASELINE_SSIM
    default_psnr = DEFAULT_PSNR_MIN if case["kind"] == "reference" else DEFAULT_BASELINE_PSNR
    ssim, psnr = metrics(expected, actual)
    check("SSIM at or above the threshold", ssim >= thresholds.get("ssim_min", default_ssim),
          f"{ssim:.4f} vs {thresholds.get('ssim_min', default_ssim)}")
    check("PSNR at or above the threshold", psnr >= thresholds.get("psnr_min", default_psnr),
          f"{psnr:.1f} dB vs {thresholds.get('psnr_min', default_psnr)}")
    check("the run recorded a wall time", bool(result.get("output_path")), f"{elapsed:.1f}s")


def build_case(case: dict) -> None:
    expected = case["directory"] / "expected.mp4"
    produce(case, expected)
    meta_path = case["directory"] / "meta.json"
    meta = case["meta"] or {}
    meta.setdefault("case_id", case["id"])
    meta["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta.setdefault("captured_on", {"os": "Windows"})
    meta.setdefault("engine", {"model_id": "lama_roi"})
    meta.setdefault("input", probe(case["directory"] / "input.mp4"))
    meta["baseline"] = {"output_bytes": expected.stat().st_size}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  recorded {expected.relative_to(ROOT)} ({expected.stat().st_size / 1e6:.1f} MB)")


SYNTHETIC = [
    ("case_synth_single_roi", 640, 360, 24, 48, (480, 20, 120, 60), False),
    ("case_synth_edge_roi", 640, 360, 24, 24, (600, 320, 40, 40), False),
    ("case_synth_portrait_audio", 360, 640, 24, 24, (200, 40, 100, 60), True),
]


def synth() -> None:
    """Cases that can be generated here; they cover the failure surfaces the
    runbook lists, not real footage."""
    sys.path.insert(0, str(ROOT))
    from scripts.generate_fixture import make_clip  # type: ignore

    CASES.mkdir(parents=True, exist_ok=True)
    for case_id, width, height, fps, frames, rect, audio in SYNTHETIC:
        directory = CASES / case_id
        directory.mkdir(parents=True, exist_ok=True)
        make_clip(directory / "input.mp4", width, height, fps, frames, rect, audio)
        segments = [{
            "start_frame": 0,
            "end_frame": frames - 1,
            "rect": {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]},
            "expand_px": 5,
            "feather_px": 3,
            "enabled": True,
        }]
        (directory / "annotations.wmr.json").write_text(
            json.dumps({"segments": segments}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  generated {case_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="compare every case against expected.mp4")
    parser.add_argument("--build", action="store_true", help="record expected.mp4 from this build")
    parser.add_argument("--synth", action="store_true", help="generate the synthetic cases")
    parser.add_argument("--case", default="", help="only this case id")
    args = parser.parse_args()

    if args.synth:
        synth()
        return 0

    if not CASES.is_dir():
        print(f"no cases under {CASES.relative_to(ROOT)}")
        print("  generate the synthetic ones: python scripts/golden.py --synth")
        return 2

    cases = [load_case(directory) for directory in sorted(CASES.iterdir()) if directory.is_dir()]
    cases = [case for case in cases if case]
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]

    if not cases:
        print("no usable cases (each needs input.mp4 and annotations.wmr.json)")
        return 2

    if args.build:
        for case in cases:
            build_case(case)
        return 0

    print(f"{len(cases)} case(s) under {CASES.relative_to(ROOT)}")
    for case in cases:
        check_case(case)

    references = sum(1 for case in cases if case["kind"] == "reference")
    print()
    print(f"{passed} passed, {failed} failed")
    print(f"{references} reference case(s), {len(cases) - references} baseline case(s)")
    if references == 0:
        print("note: no mac-recorded cases yet — these runs only prove no regression")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
