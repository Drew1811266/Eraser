#!/usr/bin/env python3
"""Compare the port's annotation handling against the reference implementation.

Same idea as `parity-scene-split.py`: the reference's `src/core/annotations.py`
imports nothing outside the standard library, so the port's normalisation can be
compared with the original on the same inputs — including the inputs that only
happen at 3am (negative coordinates, a rectangle wider than the frame, a frame
range that ends before it starts).

Geometry errors here are silent: a rectangle clamped the wrong way still renders
and still produces a video, it just removes the wrong pixels.

    python scripts/parity-annotations.py [--verbose]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / ".reference" / "up" / "src" / "core"

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


RECT_CASES = [
    ("inside", {"x": 100, "y": 50, "width": 200, "height": 80}, 1920, 1080),
    ("negative origin", {"x": -20, "y": -10, "width": 100, "height": 50}, 1920, 1080),
    ("wider than the frame", {"x": 1900, "y": 10, "width": 400, "height": 60}, 1920, 1080),
    ("taller than the frame", {"x": 10, "y": 1070, "width": 60, "height": 400}, 1920, 1080),
    ("zero size", {"x": 10, "y": 10, "width": 0, "height": 0}, 1920, 1080),
    ("past the right edge", {"x": 5000, "y": 10, "width": 50, "height": 50}, 1920, 1080),
    ("past the bottom edge", {"x": 10, "y": 5000, "width": 50, "height": 50}, 1080, 1920),
    ("fractional", {"x": 10.9, "y": 20.4, "width": 30.6, "height": 40.1}, 640, 360),
    ("string numbers", {"x": "12", "y": "34", "width": "56", "height": "78"}, 640, 360),
    ("garbage", {"x": "left", "y": None, "width": "wide", "height": ""}, 640, 360),
    ("tiny frame", {"x": 5, "y": 5, "width": 10, "height": 10}, 2, 2),
    ("exact fit", {"x": 0, "y": 0, "width": 640, "height": 360}, 640, 360),
]


def reference_helpers():
    sys.path.insert(0, str(REFERENCE))
    import annotations  # type: ignore

    return annotations


def port_helpers():
    sys.path.insert(0, str(ROOT))
    from engine.methods import annotation  # type: ignore

    return annotation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("== rectangle normalisation ==")
    original = reference_helpers()
    port = port_helpers()

    mismatches = 0
    for name, rect, width, height in RECT_CASES:
        expected = original._normalize_rect(dict(rect), width, height)
        actual = port._normalize_rect(dict(rect), width, height)
        same = expected == actual
        if not same:
            mismatches += 1
        if args.verbose or not same:
            print(f"  {name}: reference {expected} | port {actual}")
    check("every rectangle normalises identically", mismatches == 0,
          f"{len(RECT_CASES) - mismatches}/{len(RECT_CASES)}")

    print("\n== segment normalisation ==")
    video = {"width": 640, "height": 360, "frame_count": 100, "fps": 24.0}
    segments = [
        {"start_frame": 0, "end_frame": 10, "rect": {"x": 10, "y": 10, "width": 20, "height": 20}},
        {"start_frame": -5, "end_frame": 500, "rect": {"x": -10, "y": 10, "width": 20, "height": 20}},
        {"start_frame": 50, "end_frame": 10, "rect": {"x": 10, "y": 10, "width": 20, "height": 20}},
        {"rect": {"x": 10, "y": 10, "width": 20, "height": 20}},
    ]

    expected = original.normalize_segments([dict(item) for item in segments], video)
    actual = port.normalize_segments([dict(item) for item in segments], video)
    check("the same number of segments survives", len(expected) == len(actual),
          f"{len(actual)} vs {len(expected)}")

    if len(expected) == len(actual):
        frame_diffs = 0
        rect_diffs = 0
        for left, right in zip(expected, actual):
            if (left.get("start_frame"), left.get("end_frame")) != (right.get("start_frame"), right.get("end_frame")):
                frame_diffs += 1
                if args.verbose:
                    print(f"  frames: reference {left.get('start_frame')}-{left.get('end_frame')} "
                          f"| port {right.get('start_frame')}-{right.get('end_frame')}")
            if left.get("rect") != right.get("rect"):
                rect_diffs += 1
                if args.verbose:
                    print(f"  rect: reference {left.get('rect')} | port {right.get('rect')}")
        check("frame ranges match", frame_diffs == 0, f"{frame_diffs} differ")
        check("rectangles match", rect_diffs == 0, f"{rect_diffs} differ")

        # The two implementations are allowed to differ in how they describe a
        # segment, not in what they mean.
        check("both keep segment ids", all("id" in item for item in actual))
        check("both clamp to the last frame",
              all(item.get("end_frame", 0) < video["frame_count"] for item in actual),
              f"frame_count {video['frame_count']}")

    print()
    print(f"{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
