#!/usr/bin/env python3
"""Compare my scene splitting against the reference implementation, function by function.

Golden *videos* recorded by the mac app are the real acceptance gate, but they
need a mac and a day of recording. This answers a narrower question that can be
answered right now: when the original Python and this port look at the same
clip, do they find the same shots at the same times?

The two implementations are not identical by design — the port is one pass of
ffmpeg's scene score, the reference adds PySceneDetect and TransNetV2 review —
so the comparison is deliberately about *agreement*, not equality:

    cuts within 0.2s of each other count as the same cut
    segment counts may differ by one (the reference merges short shots)

Run:

    python scripts/parity-scene-split.py [--verbose]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / ".reference" / "up2" / "src" / "core"
FFMPEG = ROOT / "vendor" / "ffmpeg" / "win32-x86_64" / "ffmpeg.exe"
WORK = ROOT / ".cache" / "parity"

CUT_TOLERANCE = 0.20
COUNT_TOLERANCE = 1

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}" + (f" ({detail})" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" ({detail})" if detail else ""))


def run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def make_clip(destination: Path, shots: int, duration: float = 2.0) -> None:
    """A clip of `shots` equal pieces, each a different colour: every boundary is
    a hard cut and the expected cut list is known in advance."""
    colours = ["red", "blue", "green", "yellow", "white", "black"]
    piece = duration / shots
    inputs: list[str] = []
    for index in range(shots):
        inputs += ["-f", "lavfi", "-i", f"color=c={colours[index % len(colours)]}:s=320x180:d={piece:.3f}:r=24"]
    filters = "".join(f"[{index}:v]" for index in range(shots)) + f"concat=n={shots}:v=1:a=0[out]"
    compilation = run([str(FFMPEG), "-y", "-v", "error", *inputs,
                       "-filter_complex", filters, "-map", "[out]",
                       "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(destination)])
    if compilation.returncode != 0:
        raise SystemExit(f"clip generation failed: {compilation.stderr.decode('utf-8', 'replace')[-400:]}")


def reference_split(clip: Path, duration: float, fps: float) -> tuple[list[float], list[tuple[float, float]], str]:
    sys.path.insert(0, str(REFERENCE))
    import scene_splitter  # type: ignore

    splitter = scene_splitter.SceneSplitter(ffmpeg_bin=str(FFMPEG))
    result = splitter.split(input_path=str(clip), duration_sec=duration, fps=fps)
    segments = [(round(segment.start, 2), round(segment.end, 2)) for segment in result.segments]
    return list(result.cuts), segments, result.split_mode


def port_cuts(clip: Path) -> list[float]:
    sys.path.insert(0, str(ROOT))
    from engine.pipeline.upscale_plan import detect_scene_cuts

    return detect_scene_cuts(str(clip))


def match(expected: list[float], actual: list[float]) -> tuple[int, list[float]]:
    """How many expected cuts have a counterpart within the tolerance."""
    unmatched: list[float] = []
    matched = 0
    for cut in expected:
        if any(abs(cut - other) <= CUT_TOLERANCE for other in actual):
            matched += 1
        else:
            unmatched.append(cut)
    return matched, unmatched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)

    scenarios = [
        ("single-shot", 1, []),
        ("two-shots", 2, [1.0]),
        ("four-shots", 4, [0.5, 1.0, 1.5]),
    ]

    for name, shots, expected in scenarios:
        print(f"\n== {name}: {shots} shot(s), expected cuts {expected} ==")
        clip = WORK / f"{name}.mp4"
        make_clip(clip, shots, duration=2.0)

        reference, reference_segments, mode = reference_split(clip, 2.0, 24.0)
        mine = port_cuts(clip)

        if args.verbose:
            print(f"  reference [{mode}]: {[round(cut, 2) for cut in reference]}")
            print(f"  port              : {[round(cut, 2) for cut in mine]}")

        # Whatever the reference found defines the truth for this comparison.
        matched, unmatched = match(reference, mine)
        check("the port finds every cut the reference found",
              matched == len(reference), f"{matched}/{len(reference)}" + (f", missing {unmatched}" if unmatched else ""))

        extra = [cut for cut in mine if not any(abs(cut - other) <= CUT_TOLERANCE for other in reference)]
        check("the port does not invent cuts", len(extra) == 0, str(extra[:3]) if extra else "")

        from engine.pipeline.upscale_plan import build_segments

        my_segments = build_segments(2.0, mine, merge_short=True)
        check("segment counts agree within one",
              abs(len(reference_segments) - len(my_segments)) <= COUNT_TOLERANCE,
              f"reference {len(reference_segments)}, port {len(my_segments)}")
        if args.verbose:
            print(f"  reference segments: {reference_segments}")
            print(f"  port segments     : {my_segments}")

    print()
    print(f"{passed} passed, {failed} failed")
    if failed:
        print("note: a disagreement here is a porting bug unless the reference's extra passes explain it")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
