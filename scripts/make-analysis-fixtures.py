"""Generate the analysis evaluation fixtures and their ground truth.

    python scripts/make-analysis-fixtures.py --out .cache/analysis [--force]

Each case is rendered twice — once clean, once with the watermark — and the
ground-truth rectangle is the bounding box of the pixels that differ. Hand-written
coordinates would drift with every font and ffmpeg build; the diff cannot.

Output per case:
    <out>/<case>/video.mp4
    <out>/<case>/truth.json   {video, expected, present[], rects{frame: rect}}
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.platform.ffmpeg import resolve  # noqa: E402

WIDTH, HEIGHT, FPS, FRAMES = 640, 360, 10, 100
STEP = 5

BACKGROUND = "gradients=s=640x360:c0=0x203040:c1=0x806040:d=1"


def _background_image(ffmpeg: str, out_dir: Path) -> Path:
    """One still frame, looped for both renders.

    A timed `gradients` source is animated: rendering it twice gives two different
    backgrounds and the diff then reports the whole frame as the watermark. One
    static image is the only way the diff means what it claims.
    """
    target = out_dir / "background.png"
    if not target.is_file():
        _run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", BACKGROUND,
              "-frames:v", "1", str(target)])
    return target


def _text(text: str, *, size: int, x: str, y: str, alpha: float = 0.8, enable: str | None = None) -> str:
    parts = [
        f"drawtext=text='{text}'",
        f"fontsize={size}",
        f"fontcolor=white@{alpha}",
        f"x={x}",
        f"y={y}",
        "box=1",
        "boxcolor=black@0.25",
        "boxborderw=4",
    ]
    if enable:
        parts.append(f"enable='{enable}'")
    return ":".join(parts)


CASES: dict[str, dict] = {
    "corner_static": {
        "draw": _text("© StudioName 2026", size=28, x="w-tw-24", y="h-th-20"),
        "present": [[0, FRAMES - 1]],
        "expected": {"tracks": 1, "motion": "static", "intervals": 1},
    },
    "center_logo": {
        "draw": _text("DEMO", size=72, x="(w-tw)/2", y="(h-th)/2", alpha=0.6, enable=None),
        "present": [[0, FRAMES - 1]],
        "expected": {"tracks": 1, "motion": "static", "intervals": 1},
    },
    "small_mark": {
        "draw": _text("wm", size=22, x="24", y="20", alpha=0.55),
        "present": [[0, FRAMES - 1]],
        "expected": {"tracks": 1, "motion": "static", "intervals": 1},
    },
    "moving_mark": {
        # 40 px per second = 4 px per frame, well inside the association window.
        "draw": _text("follow me", size=30, x="60+40*t", y="40", alpha=0.75),
        "present": [[0, FRAMES - 1]],
        "expected": {"tracks": 1, "motion": "moving", "intervals": 1},
    },
    "blink": {
        "draw": _text(
            "© Brand", size=30, x="w-tw-30", y="h-th-24",
            enable="lt(t,2)+gte(t,5)*lt(t,8)",
        ),
        "present": [[0, 19], [50, 79]],
        "expected": {"tracks": 1, "motion": "static", "intervals": 2},
    },
    "multi_shot": {
        # Three shots joined by hard cuts; the same mark sits in the corner.
        # The colours are far apart on purpose: a cut between two dark solids
        # scores ~0.06 with ffmpeg's scene metric and is not a detectable cut.
        "shots": [
            {"background": "color=c=0xE8DCC8:s=640x360", "draw": _text("© Brand", size=30, x="w-tw-30", y="h-th-24")},
            {"background": "color=c=0x1A2A44:s=640x360", "draw": _text("© Brand", size=30, x="w-tw-30", y="h-th-24")},
            {"background": "color=c=0xC04020:s=640x360", "draw": _text("© Brand", size=30, x="w-tw-30", y="h-th-24")},
        ],
        "present": [[0, FRAMES - 1]],
        "expected": {"tracks": 1, "motion": "static", "intervals": 3},
    },
    "clean": {
        "draw": None,
        "present": [],
        "expected": {"tracks": 0, "motion": "static", "intervals": 0},
    },
}


def _run(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {completed.stderr.decode('utf-8', 'replace')[-400:]}"
        )


def _render(ffmpeg: str, target: Path, *, draw: str | None, background: Path) -> None:
    filters = draw or "null"
    _run(
        [
            ffmpeg, "-y", "-v", "error",
            "-loop", "1", "-i", str(background),
            "-vf", filters,
            "-r", str(FPS),
            "-frames:v", str(FRAMES),
            "-pix_fmt", "yuv420p",
            str(target),
        ]
    )


def _render_multi_shot(ffmpeg: str, target: Path, shots: list[dict]) -> None:
    scratch = target.parent / "shots"
    scratch.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    per_shot = FRAMES // len(shots)
    for index, shot in enumerate(shots):
        count = per_shot if index < len(shots) - 1 else FRAMES - per_shot * (len(shots) - 1)
        part = scratch / f"{index}.mp4"
        filters = shot["background"]
        if shot.get("draw"):
            filters = f"{filters},{shot['draw']}"
        _run(
            [
                ffmpeg, "-y", "-v", "error",
                "-f", "lavfi", "-i", filters,
                "-r", str(FPS), "-frames:v", str(count),
                "-pix_fmt", "yuv420p", str(part),
            ]
        )
        parts.append(part)
    listing = scratch / "list.txt"
    # ffmpeg resolves relative paths in a concat list against the list's own
    # directory, so the entries must be absolute.
    listing.write_text(
        "".join(f"file '{part.resolve().as_posix()}'\n" for part in parts),
        encoding="utf-8",
    )
    _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(target)])


def _grab(ffmpeg: str, video: Path, frame: int) -> np.ndarray:
    target = video.parent / f"grab-{video.stem}-{frame}.png"
    _run([ffmpeg, "-y", "-v", "error", "-ss", f"{frame / FPS:.4f}", "-i", str(video),
          "-frames:v", "1", str(target)])
    image = cv2.imread(str(target))
    target.unlink(missing_ok=True)
    if image is None:
        raise RuntimeError(f"cannot read frame {frame} of {video}")
    return image


def _diff_box(clean: np.ndarray, marked: np.ndarray) -> dict | None:
    delta = cv2.absdiff(clean, marked).max(axis=2)
    mask = (delta > 12).astype(np.uint8) * 255
    if not mask.any():
        return None
    x, y, w, h = cv2.boundingRect(mask)
    if w < 2 or h < 2:
        return None
    return {"x": int(x), "y": int(y), "width": int(w), "height": int(h)}


def build_case(ffmpeg: str, name: str, spec: dict, out_dir: Path) -> dict:
    case_dir = out_dir / name
    case_dir.mkdir(parents=True, exist_ok=True)
    marked = case_dir / "video.mp4"
    clean = case_dir / "clean.mp4"

    if "shots" in spec:
        _render_multi_shot(ffmpeg, marked, spec["shots"])
        # The clean render must use the *same* backgrounds, otherwise the diff is
        # the whole frame rather than the watermark.
        _render_multi_shot(ffmpeg, clean, [{**shot, "draw": None} for shot in spec["shots"]])
    else:
        background = _background_image(ffmpeg, out_dir)
        _render(ffmpeg, marked, draw=spec["draw"], background=background)
        _render(ffmpeg, clean, draw=None, background=background)

    rects: dict[str, dict] = {}
    if spec.get("draw") is not None or "shots" in spec:
        for frame in range(0, FRAMES, STEP):
            box = _diff_box(_grab(ffmpeg, clean, frame), _grab(ffmpeg, marked, frame))
            if box is not None:
                rects[str(frame)] = box

    truth = {
        "case": name,
        "video": {"width": WIDTH, "height": HEIGHT, "fps": FPS, "frames": FRAMES},
        "sample_step": STEP,
        "present": spec["present"],
        "expected": spec["expected"],
        "rects": rects,
    }
    (case_dir / "truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="make-analysis-fixtures")
    parser.add_argument("--out", default=str(ROOT / ".cache" / "analysis"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", default="", help="build one case")
    args = parser.parse_args(argv)

    ffmpeg = resolve("ffmpeg").path
    if not ffmpeg:
        print("ffmpeg not found", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else list(CASES)
    for name in names:
        case_dir = out_dir / name
        if (case_dir / "truth.json").is_file() and not args.force:
            print(f"{name}: cached")
            continue
        if case_dir.exists() and args.force:
            shutil.rmtree(case_dir, ignore_errors=True)
        truth = build_case(ffmpeg, name, CASES[name], out_dir)
        print(f"{name}: {len(truth['rects'])} probe frame(s), present={truth['present']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
