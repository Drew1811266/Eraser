"""Analysis value objects and geometry.

Everything here is pure: no ffmpeg, no model, no file IO. That is deliberate —
the hard part of the feature (associating detections across frames) is checkable
with synthetic inputs, so it must not sit behind a video decoder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    width: int
    height: int

    @property
    def x2(self) -> int:
        return self.x + self.width

    @property
    def y2(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: Any) -> "BBox":
        data = raw if isinstance(raw, dict) else {}
        return cls(
            x=int(data.get("x", 0) or 0),
            y=int(data.get("y", 0) or 0),
            width=int(data.get("width", 0) or 0),
            height=int(data.get("height", 0) or 0),
        )

    def clamp(self, frame_width: int, frame_height: int) -> "BBox":
        """Fit inside the frame without ever collapsing to zero size."""
        if frame_width <= 0 or frame_height <= 0:
            return BBox(0, 0, 1, 1)
        x = min(max(0, self.x), frame_width - 1)
        y = min(max(0, self.y), frame_height - 1)
        width = max(1, min(max(1, self.width), frame_width - x))
        height = max(1, min(max(1, self.height), frame_height - y))
        return BBox(x, y, width, height)

    @staticmethod
    def union(boxes: Iterable["BBox"]) -> "BBox":
        items = list(boxes)
        if not items:
            return BBox(0, 0, 1, 1)
        x1 = min(item.x for item in items)
        y1 = min(item.y for item in items)
        x2 = max(item.x2 for item in items)
        y2 = max(item.y2 for item in items)
        return BBox(x1, y1, max(1, x2 - x1), max(1, y2 - y1))


@dataclass(frozen=True)
class Detection:
    frame: int
    rect: BBox
    confidence: float = 1.0


@dataclass(frozen=True)
class Interval:
    start_frame: int
    end_frame: int

    @property
    def length(self) -> int:
        return max(0, self.end_frame - self.start_frame + 1)

    def as_dict(self) -> dict[str, int]:
        return {"start_frame": self.start_frame, "end_frame": self.end_frame}


@dataclass(frozen=True)
class Keyframe:
    frame: int
    rect: BBox

    def as_dict(self) -> dict[str, Any]:
        return {"frame": self.frame, "rect": self.rect.as_dict()}


@dataclass
class WatermarkTrack:
    id: str
    intervals: list[Interval]
    keyframes: list[Keyframe]
    motion: str = "static"
    confidence: float = 0.0
    source: str = "auto"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "intervals": [item.as_dict() for item in self.intervals],
            "keyframes": [item.as_dict() for item in self.keyframes],
            "motion": self.motion,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "notes": list(self.notes),
        }


@dataclass
class Analysis:
    video: dict[str, Any]
    shots: list[Interval]
    watermarks: list[WatermarkTrack]
    suggested: dict[str, Any]
    model_id: str
    elapsed_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "video": dict(self.video),
            "shots": [item.as_dict() for item in self.shots],
            "watermarks": [item.as_dict() for item in self.watermarks],
            "suggested": dict(self.suggested),
            "model_id": self.model_id,
            "elapsed_ms": int(self.elapsed_ms),
        }


def iou(a: BBox, b: BBox) -> float:
    """Intersection over union; 0.0 when either box has no area."""
    ix1 = max(a.x, b.x)
    iy1 = max(a.y, b.y)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    intersection = (ix2 - ix1) * (iy2 - iy1)
    union = a.area + b.area - intersection
    if union <= 0:
        return 0.0
    return intersection / float(union)


def center_distance(a: BBox, b: BBox) -> float:
    ax, ay = a.center
    bx, by = b.center
    return math.hypot(ax - bx, ay - by)


IOU_MATCH = 0.5
#: A re-appearance closer than this (in seconds) is a missed detection, not a
#: second appearance — the timeline must not shatter over one dropped sample.
MERGE_GAP_SECONDS = 1.5
#: Beyond this the watermark is considered to have come back as a new interval.
REOPEN_GAP_SECONDS = 5.0
SIZE_SIMILARITY = 0.25


@dataclass
class _MutableTrack:
    id: str
    intervals: list[Interval]
    keyframes: list[Keyframe]
    confidences: list[float]
    last_frame: int
    last_rect: BBox


def _similar_size(a: BBox, b: BBox) -> bool:
    if min(a.width, a.height, b.width, b.height) <= 0:
        return False

    def ratio(x: float, y: float) -> float:
        return abs(x - y) / max(1e-6, max(x, y))

    return (
        ratio(a.width, b.width) <= SIZE_SIMILARITY
        and ratio(a.height, b.height) <= SIZE_SIMILARITY
        and ratio(a.width / a.height, b.width / b.height) <= SIZE_SIMILARITY
    )


def associate(
    detections: dict[int, list[Detection]],
    *,
    frame_count: int,
    frame_width: int,
    frame_height: int,
    fps: float,
    min_confidence: float = 0.35,
    max_area_ratio: float = 0.25,
    max_gap_seconds: float = REOPEN_GAP_SECONDS,
    max_tracks: int = 8,
) -> list[WatermarkTrack]:
    """Group per-frame detections into watermark tracks.

    Greedy and deterministic on purpose: a wrong grouping is visible in the UI
    and fixable by splitting/merging, whereas a non-deterministic grouping would
    make the same clip analyse differently on every run.

    Two windows govern identity:
    - gaps up to ``MERGE_GAP_SECONDS`` are missed detections (same interval);
    - gaps up to ``max_gap_seconds`` are the same watermark returning (new interval);
    - anything longer is a different watermark.
    """
    del frame_count  # symmetry with the callers; ranges are validated by them
    frame_area = max(1, frame_width * frame_height)
    usable_gap = max(0.0, float(max_gap_seconds))

    open_tracks: list[_MutableTrack] = []
    idle_tracks: list[_MutableTrack] = []
    next_id = 1

    for frame in sorted(detections):
        # A track unseen for longer than the merge window is no longer a
        # *continuation* candidate: it either reopens as a second interval of the
        # same watermark (inside the reopen window) or becomes a new track.
        expired = [
            track
            for track in open_tracks
            if fps > 0 and (frame - track.last_frame) / fps > MERGE_GAP_SECONDS
        ]
        for track in expired:
            open_tracks.remove(track)
            idle_tracks.append(track)

        candidates = [
            item
            for item in detections[frame]
            if item.confidence >= min_confidence
            and 0 < item.rect.area <= frame_area * max_area_ratio
        ]
        used: set[int] = set()

        for track in sorted(open_tracks, key=lambda item: item.last_frame, reverse=True):
            index, score = _best_match(track.last_rect, candidates, used)
            if index is None:
                continue
            distance = center_distance(track.last_rect, candidates[index].rect)
            if score < IOU_MATCH and distance > 0.08 * frame_width:
                continue
            _extend(track, frame, candidates[index])
            used.add(index)

        for index, detection in enumerate(candidates):
            if index in used:
                continue
            track = _reopenable(idle_tracks, detection, frame, fps, usable_gap)
            if track is not None:
                idle_tracks.remove(track)
                gap = (frame - track.last_frame) / fps if fps > 0 else 0.0
                if gap <= MERGE_GAP_SECONDS:
                    track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
                else:
                    track.intervals.append(Interval(frame, frame))
                track.keyframes.append(Keyframe(frame, detection.rect))
                track.confidences.append(detection.confidence)
                track.last_frame, track.last_rect = frame, detection.rect
                open_tracks.append(track)
                used.add(index)
                continue

            created = _MutableTrack(
                id=f"wm-{next_id}",
                intervals=[Interval(frame, frame)],
                keyframes=[Keyframe(frame, detection.rect)],
                confidences=[detection.confidence],
                last_frame=frame,
                last_rect=detection.rect,
            )
            next_id += 1
            open_tracks.append(created)
            used.add(index)

        still_open: list[_MutableTrack] = []
        for track in open_tracks:
            if track.last_frame == frame:
                still_open.append(track)
            else:
                idle_tracks.append(track)
        open_tracks = still_open

    idle_tracks.extend(open_tracks)
    tracks = [_finalise(item, frame_width, frame_height) for item in idle_tracks]
    tracks.sort(key=lambda item: (item.intervals[0].start_frame, item.id))
    return tracks[: max(0, int(max_tracks))]


def _best_match(
    reference: BBox,
    candidates: list[Detection],
    used: set[int],
) -> tuple[int | None, float]:
    best_index: int | None = None
    best_score = 0.0
    for index, item in enumerate(candidates):
        if index in used:
            continue
        score = iou(reference, item.rect)
        if score > best_score:
            best_index, best_score = index, score
    return best_index, best_score


def _extend(track: _MutableTrack, frame: int, detection: Detection) -> None:
    track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
    track.keyframes.append(Keyframe(frame, detection.rect))
    track.confidences.append(detection.confidence)
    track.last_frame, track.last_rect = frame, detection.rect


def _reopenable(
    idle: list[_MutableTrack],
    detection: Detection,
    frame: int,
    fps: float,
    max_gap_seconds: float,
) -> _MutableTrack | None:
    best: _MutableTrack | None = None
    best_gap = float("inf")
    for track in idle:
        gap = (frame - track.last_frame) / fps if fps > 0 else float("inf")
        if gap < 0 or gap > max_gap_seconds:
            continue
        if not _similar_size(track.last_rect, detection.rect):
            continue
        if gap < best_gap:
            best, best_gap = track, gap
    return best


def _finalise(track: _MutableTrack, frame_width: int, frame_height: int) -> WatermarkTrack:
    confidence = sum(track.confidences) / max(1, len(track.confidences))
    result = WatermarkTrack(
        id=track.id,
        intervals=list(track.intervals),
        keyframes=list(track.keyframes),
        confidence=confidence,
    )
    result.motion = classify_motion(result, frame_width=frame_width, frame_height=frame_height)
    return result


def classify_motion(track: WatermarkTrack, *, frame_width: int, frame_height: int) -> str:
    """`moving` when the box travels or changes size appreciably."""
    if len(track.keyframes) < 2:
        return "static"
    centers = [item.rect.center for item in track.keyframes]
    xs = [point[0] for point in centers]
    ys = [point[1] for point in centers]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    widths = [max(1, item.rect.width) for item in track.keyframes]
    heights = [max(1, item.rect.height) for item in track.keyframes]
    grows = max(max(widths) / min(widths), max(heights) / min(heights))
    if dx > 0.05 * frame_width or dy > 0.05 * frame_height:
        return "moving"
    if grows > 1.15:
        return "moving"
    return "static"


ENGINE_FAST = "lama_roi"
ENGINE_QUALITY = "propainter_roi"


def build_shots(cuts: list[float], *, fps: float, frame_count: int) -> list[Interval]:
    """Turn cut timestamps into frame intervals covering the whole clip.

    Degenerate input (no frames, cuts outside the clip) must still yield at
    least one shot: the analysis report has to show something for a 1-frame
    video rather than an empty timeline.
    """
    total = max(1, int(frame_count))
    last = total - 1
    if last <= 0:
        return [Interval(0, 0)]

    boundaries = [0]
    for cut in sorted(float(item) for item in cuts):
        if fps <= 0:
            continue
        frame = int(round(cut * fps))
        if 0 < frame <= last and frame - boundaries[-1] > 0:
            boundaries.append(frame)
    # Exclusive upper bound: the last shot must *include* the final frame, so a
    # 100-frame clip is (0, 99) and not (0, 98).
    if boundaries[-1] != last + 1:
        boundaries.append(last + 1)

    shots: list[Interval] = []
    for index in range(len(boundaries) - 1):
        start = boundaries[index]
        end = boundaries[index + 1] - 1
        if end >= start:
            shots.append(Interval(start, end))
    return shots or [Interval(0, 0)]


def suggest_strategy(
    shots: list[Interval],
    watermarks: list[WatermarkTrack],
    *,
    engines: list[str] | None = None,
) -> dict[str, Any]:
    """Advice for the confirmation screen. Never a decision on the user's behalf."""
    available = list(engines) if engines is not None else [ENGINE_FAST]
    warnings: list[str] = []

    if not watermarks:
        warnings.append("no_watermark")
        return {"engine_id": ENGINE_FAST, "split_by_shot": False, "warnings": warnings}

    moves = any(item.motion == "moving" for item in watermarks)
    multi_shot = len(shots) > 1
    if moves:
        warnings.append("moving_watermark")

    wants_quality = moves or multi_shot
    engine = ENGINE_FAST
    if wants_quality:
        if ENGINE_QUALITY in available:
            engine = ENGINE_QUALITY
        else:
            warnings.append("quality_engine_unavailable")

    return {"engine_id": engine, "split_by_shot": multi_shot, "warnings": warnings}
