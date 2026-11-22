"""Measure the analysis pipeline against the fixture ground truth.

    python scripts/eval-analysis.py --cases .cache/analysis --detector stub
    python scripts/eval-analysis.py --cases .cache/analysis --detector florence2

`--detector stub` replays the ground truth through the pipeline. It should score
perfectly; when it does not, the metrics or the analysis are wrong, not the
model. That makes it the calibration run before any real detector is judged.

Metrics per case:
    hit%      probe frames whose nearest predicted keyframe has IoU >= 0.5
    cover%    share of ground-truth visible frames covered by a predicted interval
    fp%       share of predicted visible frames the ground truth says are empty
    tracks / motion / intervals   compared with `truth.json`

The environment matters: the engine writes scratch space under
`ERASER_TEMP_DIR` and the detector runtime lives under `ERASER_USER_DATA_DIR`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.pipeline.analysis import BBox, Detection, WatermarkTrack, iou  # noqa: E402
from engine.pipeline.analyze_job import ANALYSIS_MAX_WIDTH, run_analysis  # noqa: E402
from engine.pipeline.detect import ScriptedDetector, catalog  # noqa: E402


def _ranges_to_frames(ranges: list[list[int]]) -> set[int]:
    frames: set[int] = set()
    for start, end in ranges:
        frames.update(range(int(start), int(end) + 1))
    return frames


def _track_frames(track: WatermarkTrack) -> set[int]:
    return _ranges_to_frames([[item.start_frame, item.end_frame] for item in track.intervals])


def _nearest_keyframe(track: WatermarkTrack, frame: int):
    if not track.keyframes:
        return None
    return min(track.keyframes, key=lambda item: abs(item.frame - frame))


def _evaluate(truth: dict, analysis) -> dict:
    gt_present = _ranges_to_frames(truth.get("present") or [])
    expected = truth.get("expected") or {}
    rects: dict[str, dict] = truth.get("rects") or {}

    tracks = analysis.watermarks
    predicted_present: set[int] = set()
    for track in tracks:
        predicted_present |= _track_frames(track)

    hits = 0
    probes = 0
    for frame_key, gt_rect in rects.items():
        frame = int(frame_key)
        gt_box = BBox.from_dict(gt_rect)
        candidates = [track for track in tracks if frame in _track_frames(track)]
        if not candidates:
            probes += 1
            continue
        probes += 1
        best = 0.0
        for track in candidates:
            keyframe = _nearest_keyframe(track, frame)
            if keyframe is not None:
                best = max(best, iou(gt_box, keyframe.rect))
        if best >= 0.5:
            hits += 1

    covered = len(predicted_present & gt_present)
    false_frames = len(predicted_present - gt_present)
    # With nothing to cover, coverage is vacuously complete; 0/0 must not read as
    # a failure on the clean control.
    coverage = 1.0 if not gt_present else covered / len(gt_present)

    # The track that best matches the ground truth decides motion/intervals.
    matching = None
    if tracks and rects:
        probe_frame, gt_rect = next(iter(rects.items()))
        gt_box = BBox.from_dict(gt_rect)
        best_iou = 0.0
        for track in tracks:
            keyframe = _nearest_keyframe(track, int(probe_frame))
            if keyframe is None:
                continue
            score = iou(gt_box, keyframe.rect)
            if score > best_iou:
                matching, best_iou = track, score

    return {
        "case": truth.get("case"),
        "tracks": len(tracks),
        "tracks_expected": int(expected.get("tracks", 0)),
        "motion": matching.motion if matching else ("none" if not tracks else "unmatched"),
        "motion_expected": expected.get("motion", "static"),
        "intervals": len(matching.intervals) if matching else 0,
        "intervals_expected": int(expected.get("intervals", 0)),
        "hit": round(hits / probes, 3) if probes else 1.0,
        "probes": probes,
        "coverage": round(coverage, 3),
        "false": round(false_frames / max(1, len(predicted_present)), 3),
        "seconds": round(analysis.elapsed_ms / 1000.0, 2),
        "model": analysis.model_id,
    }


def _verdict(row: dict) -> str:
    ok = (
        row["tracks"] == row["tracks_expected"]
        and row["intervals"] == row["intervals_expected"]
        and (row["tracks_expected"] == 0 or row["motion"] == row["motion_expected"])
        and row["hit"] >= 0.6
        and row["coverage"] >= 0.6
        and row["false"] <= 0.2
    )
    return "PASS" if ok else "FAIL"


def _truth_detector(truth: dict) -> ScriptedDetector:
    """Replay the ground truth the way a real detector would see it.

    A detector reports boxes in the frame it was given, and the engine downscales
    wide video before detection. Handing the pipeline source-pixel boxes would
    make the replay wrong by exactly the scale factor — and only on wide clips,
    which is the case most worth checking.
    """
    width = int((truth.get("video") or {}).get("width") or 0)
    scale = min(1.0, ANALYSIS_MAX_WIDTH / width) if width else 1.0
    detections: dict[int, list[Detection]] = {}
    for frame_key, rect in (truth.get("rects") or {}).items():
        frame = int(frame_key)
        box = BBox.from_dict(rect)
        if scale != 1.0:
            box = BBox(
                int(round(box.x * scale)),
                int(round(box.y * scale)),
                max(1, int(round(box.width * scale))),
                max(1, int(round(box.height * scale))),
            )
        detections[frame] = [Detection(frame, box, 1.0)]
    return ScriptedDetector(detections)


def _pick_detector(name: str, truth: dict):
    if name == "stub":
        return _truth_detector(truth)
    for detector in catalog():
        if detector.model_id == name:
            return detector
    raise SystemExit(f"unknown detector: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval-analysis")
    parser.add_argument("--cases", default=str(ROOT / ".cache" / "analysis"))
    parser.add_argument("--detector", default="stub")
    parser.add_argument("--json", default="", help="write the rows to this file")
    parser.add_argument("--only", default="")
    args = parser.parse_args(argv)

    cases_dir = Path(args.cases)
    if not cases_dir.is_dir():
        print(f"no fixtures at {cases_dir}; run scripts/make-analysis-fixtures.py", file=sys.stderr)
        return 2

    truths = sorted(cases_dir.glob("*/truth.json"))
    if args.only:
        truths = [path for path in truths if path.parent.name == args.only]
    if not truths:
        print("no cases found", file=sys.stderr)
        return 2

    rows: list[dict] = []
    print(f"{'case':<14} {'tracks':>6} {'gt':>3} {'motion':>10} {'iv':>3} {'gt':>3} "
          f"{'probes':>6} {'hit':>6} {'cover':>6} {'false':>6} {'sec':>6}  verdict")
    for truth_path in truths:
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        video = truth_path.parent / "video.mp4"
        if not video.is_file():
            print(f"{truth.get('case'):<14} missing video", file=sys.stderr)
            continue
        detector = _pick_detector(args.detector, truth)
        try:
            analysis = run_analysis(
                str(video),
                detector,
                sample_step=int(truth.get("sample_step") or 5),
            )
        except Exception as exc:  # noqa: BLE001 - an eval reports, never dies
            print(f"{truth.get('case'):<14} ERROR {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        row = _evaluate(truth, analysis)
        row["verdict"] = _verdict(row)
        rows.append(row)
        # With no probes the hit rate is vacuous, not perfect; printing 1.00 for
        # the clean control would look like a verified detection.
        hit_text = f"{row['hit']:.2f}" if row["probes"] else "n/a"
        print(
            f"{row['case']:<14} {row['tracks']:>6} {row['tracks_expected']:>3} "
            f"{row['motion']:>10} {row['intervals']:>3} {row['intervals_expected']:>3} "
            f"{row['probes']:>6} {hit_text:>6} {row['coverage']:>6.2f} "
            f"{row['false']:>6.2f} {row['seconds']:>6.2f}  {row['verdict']}"
        )

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"detector": args.detector, "rows": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    failed = [row["case"] for row in rows if row["verdict"] != "PASS"]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} pass" + (f" — failed: {', '.join(failed)}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
