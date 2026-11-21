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
#: Association window as a fraction of the frame width. Governs continuity and
#: short re-opens; across a cut, or after a longer absence, position is only a
#: sanity bound (see `REOPEN_TRAVEL`).
ASSOCIATION_WINDOW = 0.08
#: How far a watermark may plausibly have travelled while it was hidden for more
#: than the merge window. Wider than the association window because it is a
#: *return*, not a continuation.
REOPEN_TRAVEL = 0.5


def rescale_detections(
    detections: list[Detection], scale_x: float, scale_y: float
) -> list[Detection]:
    """Map detections from a sampled frame's space back to source pixels.

    `frames.sample_frames` downscales wide videos before detection runs, so a
    detector reports boxes in the *sampled* image's coordinate system. Skipping
    this mapping is invisible on a 640x360 clip and silently corrupt on 1080p.
    """
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("scale must be positive")
    if scale_x == 1.0 and scale_y == 1.0:
        return list(detections)
    scaled: list[Detection] = []
    for item in detections:
        rect = item.rect
        scaled.append(
            Detection(
                frame=item.frame,
                rect=BBox(
                    x=int(round(rect.x * scale_x)),
                    y=int(round(rect.y * scale_y)),
                    width=max(1, int(round(rect.width * scale_x))),
                    height=max(1, int(round(rect.height * scale_y))),
                ),
                confidence=item.confidence,
            )
        )
    return scaled


@dataclass
class _MutableTrack:
    id: str
    intervals: list[Interval]
    keyframes: list[Keyframe]
    confidences: list[float]
    last_frame: int
    last_rect: BBox
    last_shot: int = -1


def _shot_index(shots: list[Interval], frame: int) -> int:
    for index, shot in enumerate(shots):
        if shot.start_frame <= frame <= shot.end_frame:
            return index
    return -1


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


def _assign_matches(
    open_tracks: list[_MutableTrack],
    candidates: list[Detection],
    frame_width: int,
) -> dict[int, int]:
    """Assign candidates to open tracks, ranked globally.

    Per-track matching lets a track whose own box disappeared take the nearest
    box belonging to a neighbouring track — the detection counts would look
    right while the trajectory and motion flag belong to the wrong watermark.
    Ranking every (track, candidate) pair together makes the pair that wants
    each other most win first.

    Returns ``{track_index: candidate_index}``.
    """
    window = ASSOCIATION_WINDOW * frame_width
    pairs: list[tuple[tuple[int, float, float], int, int]] = []
    for track_index, track in enumerate(open_tracks):
        for candidate_index, candidate in enumerate(candidates):
            score = iou(track.last_rect, candidate.rect)
            distance = center_distance(track.last_rect, candidate.rect)
            if score >= IOU_MATCH:
                rank = (0, -score, distance)
            elif distance <= window:
                # Rank 1 loses to every overlapping pair, so this only decides
                # anything when nothing overlaps.
                rank = (1, distance, 0.0)
            else:
                continue
            pairs.append((rank, track_index, candidate_index))
    pairs.sort(key=lambda item: item[0])

    assignment: dict[int, int] = {}
    taken_tracks: set[int] = set()
    taken_candidates: set[int] = set()
    for _rank, track_index, candidate_index in pairs:
        if track_index in taken_tracks or candidate_index in taken_candidates:
            continue
        assignment[track_index] = candidate_index
        taken_tracks.add(track_index)
        taken_candidates.add(candidate_index)
    return assignment


def associate(
    detections: dict[int, list[Detection]],
    *,
    frame_count: int,
    frame_width: int,
    frame_height: int,
    fps: float,
    shots: list[Interval] | None = None,
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

    Shot boundaries come from ``shots``: a cut always starts a new interval (a
    trajectory must never be interpolated across a scene change) while the track
    identity is kept, because a corner logo can survive a cut.
    """
    del frame_count  # symmetry with the callers; ranges are validated by them
    frame_area = max(1, frame_width * frame_height)
    usable_gap = max(0.0, float(max_gap_seconds))
    timeline = list(shots or [])

    open_tracks: list[_MutableTrack] = []
    idle_tracks: list[_MutableTrack] = []
    next_id = 1

    for frame in sorted(detections):
        shot = _shot_index(timeline, frame)
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

        # Rank every pair at once: creation order must not decide who gets which
        # box when two watermarks are close together.
        assignment = _assign_matches(open_tracks, candidates, frame_width)
        for track_index in sorted(assignment):
            candidate_index = assignment[track_index]
            _extend(open_tracks[track_index], frame, candidates[candidate_index], shot)
            used.add(candidate_index)

        # Anything not matched in this sample becomes idle *before* creation runs,
        # so a watermark that jumps at a cut can be re-identified in the same
        # frame instead of being duplicated as a new track.
        still_open = [track for track in open_tracks if track.last_frame == frame]
        idle_tracks.extend(track for track in open_tracks if track.last_frame != frame)
        open_tracks = still_open

        for index, detection in enumerate(candidates):
            if index in used:
                continue
            track = _reopenable(
                idle_tracks,
                detection,
                frame,
                fps,
                usable_gap,
                shots=timeline,
                frame_width=frame_width,
            )
            if track is not None:
                idle_tracks.remove(track)
                gap = (frame - track.last_frame) / fps if fps > 0 else 0.0
                if shot == track.last_shot and gap <= MERGE_GAP_SECONDS:
                    track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
                else:
                    track.intervals.append(Interval(frame, frame))
                track.keyframes.append(Keyframe(frame, detection.rect))
                track.confidences.append(detection.confidence)
                track.last_frame, track.last_rect = frame, detection.rect
                track.last_shot = shot
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
                last_shot=shot,
            )
            next_id += 1
            open_tracks.append(created)
            used.add(index)

    idle_tracks.extend(open_tracks)
    tracks = [_finalise(item, frame_width, frame_height) for item in idle_tracks]
    tracks.sort(key=lambda item: (item.intervals[0].start_frame, item.id))
    cap = max(0, int(max_tracks))
    if len(tracks) > cap:
        # Keep the most confident when there are too many, but preserve
        # chronological order in the result: the timeline reads left to right.
        keep = {id(item) for item in sorted(tracks, key=lambda item: item.confidence, reverse=True)[:cap]}
        tracks = [item for item in tracks if id(item) in keep]
    return tracks


def _extend(track: _MutableTrack, frame: int, detection: Detection, shot: int) -> None:
    if shot != track.last_shot:
        track.intervals.append(Interval(frame, frame))
    else:
        track.intervals[-1] = Interval(track.intervals[-1].start_frame, frame)
    track.keyframes.append(Keyframe(frame, detection.rect))
    track.confidences.append(detection.confidence)
    track.last_frame, track.last_rect = frame, detection.rect
    track.last_shot = shot


def _reopenable(
    idle: list[_MutableTrack],
    detection: Detection,
    frame: int,
    fps: float,
    max_gap_seconds: float,
    *,
    shots: list[Interval],
    frame_width: int,
) -> _MutableTrack | None:
    """Pick the idle track this detection most plausibly belongs to.

    Within a shot, a *continuation* (gap inside the merge window) must sit inside
    the association window; a *return* after a longer absence only has to be
    plausible, because a moving watermark may have travelled while hidden. Across
    a cut the camera moved, so position is a weak tie-breaker rather than a gate.
    """
    shot = _shot_index(shots, frame)
    window = ASSOCIATION_WINDOW * frame_width
    best: _MutableTrack | None = None
    best_score = float("inf")
    for track in idle:
        gap = (frame - track.last_frame) / fps if fps > 0 else float("inf")
        if gap < 0 or gap > max_gap_seconds:
            continue
        if not _similar_size(track.last_rect, detection.rect):
            continue
        distance = center_distance(track.last_rect, detection.rect)
        same_shot = shot == track.last_shot
        if same_shot:
            # Continuity gets the association window; a longer absence is a
            # *return* (spec §4.3 rule 3), and the watermark may have moved while
            # it was hidden, so only a generous sanity bound applies there.
            limit = (
                window
                if gap <= MERGE_GAP_SECONDS
                else REOPEN_TRAVEL * frame_width
            )
            if distance > limit:
                continue
        distance_weight = 1.0 if same_shot else 0.1
        score = gap / max(1e-6, max_gap_seconds) + distance_weight * distance / max(
            1, frame_width
        )
        if score < best_score:
            best, best_score = track, score
    return best


def _group_moves(keyframes: list[Keyframe], frame_width: int, frame_height: int) -> bool:
    if len(keyframes) < 2:
        return False
    centers = [item.rect.center for item in keyframes]
    xs = [point[0] for point in centers]
    ys = [point[1] for point in centers]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    widths = [max(1, item.rect.width) for item in keyframes]
    heights = [max(1, item.rect.height) for item in keyframes]
    grows = max(max(widths) / min(widths), max(heights) / min(heights))
    return dx > 0.05 * frame_width or dy > 0.05 * frame_height or grows > 1.15


def _finalise(track: _MutableTrack, frame_width: int, frame_height: int) -> WatermarkTrack:
    confidence = sum(track.confidences) / max(1, len(track.confidences))
    result = WatermarkTrack(
        id=track.id,
        intervals=list(track.intervals),
        keyframes=list(track.keyframes),
        confidence=confidence,
    )
    result.motion = classify_motion(result, frame_width=frame_width, frame_height=frame_height)
    if result.motion == "static" and len(result.keyframes) > 1:
        # A static watermark has exactly one rectangle; repeating it per sample
        # bloats the payload and gives the future per-frame mask nothing extra.
        result.keyframes = result.keyframes[:1]
    return result


def classify_motion(track: WatermarkTrack, *, frame_width: int, frame_height: int) -> str:
    """`moving` when the box travels or changes size appreciably.

    Measured per interval: a logo that survives a cut at a different screen
    position was not "moving", the camera moved.
    """
    if len(track.keyframes) < 2:
        return "static"
    groups: list[list[Keyframe]] = []
    for interval in track.intervals or [None]:  # type: ignore[list-item]
        inside = [
            item
            for item in track.keyframes
            if interval is None
            or interval.start_frame <= item.frame <= interval.end_frame
        ]
        if inside:
            groups.append(inside)
    if not groups:
        groups = [track.keyframes]
    if any(_group_moves(group, frame_width, frame_height) for group in groups):
        return "moving"
    return "static"


ENGINE_FAST = "lama_roi"
ENGINE_QUALITY = "propainter_roi"
MIN_SHOT_SECONDS = 1.2
SECOND_MERGE_SECONDS = 2.0


def _merge_short_shots(shots: list[Interval], minimum_seconds: float, fps: float) -> list[Interval]:
    """Fold a flash cut into a neighbour, but never collapse a montage.

    A shot shorter than the threshold is merged into a neighbour *that is long
    enough to absorb it*. When every shot is short (a montage, a title sequence)
    the cut list is real information and collapsing it into one shot would give
    the wrong answer to "how many shots does this have".
    """
    if fps <= 0 or len(shots) < 2:
        return shots

    merged = list(shots)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for index, shot in enumerate(merged):
            length = (shot.end_frame - shot.start_frame + 1) / fps
            if length >= minimum_seconds:
                continue
            prev_shot = merged[index - 1] if index > 0 else None
            next_shot = merged[index + 1] if index + 1 < len(merged) else None

            def long_enough(candidate: Interval | None) -> bool:
                if candidate is None:
                    return False
                return (candidate.end_frame - candidate.start_frame + 1) / fps >= minimum_seconds

            if long_enough(prev_shot):
                merged[index - 1] = Interval(prev_shot.start_frame, shot.end_frame)  # type: ignore[union-attr]
            elif long_enough(next_shot):
                merged[index + 1] = Interval(shot.start_frame, next_shot.end_frame)  # type: ignore[union-attr]
            else:
                continue
            merged.pop(index)
            changed = True
            break
    return merged


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
    shots = _merge_short_shots(shots, MIN_SHOT_SECONDS, fps)
    shots = _merge_short_shots(shots, SECOND_MERGE_SECONDS, fps)
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
    multiple_marks = len(watermarks) > 1
    if moves:
        warnings.append("moving_watermark")
    if multiple_marks:
        warnings.append("multiple_watermarks")
    if any(item.confidence < 0.5 for item in watermarks):
        warnings.append("low_confidence")

    wants_quality = moves or multi_shot or multiple_marks
    engine = ENGINE_FAST
    if wants_quality:
        if ENGINE_QUALITY in available:
            engine = ENGINE_QUALITY
        else:
            warnings.append("quality_engine_unavailable")

    return {"engine_id": engine, "split_by_shot": multi_shot, "warnings": warnings}


def compile_tracks(
    tracks: list[WatermarkTrack],
    *,
    shots: list[Interval],
    frame_width: int,
    frame_height: int,
    frame_count: int | None = None,
    expand_px: int = 5,
    feather_px: int = 3,
) -> list[dict[str, Any]]:
    """Flatten tracks into the Segment list the existing pipeline understands.

    A track that spans a cut becomes one segment per shot: a temporal model must
    not interpolate a trajectory across a scene change, and the pipeline already
    treats segments as independent runs.

    The ROI is the union of the keyframes inside the range, so a moving watermark
    keeps a *stable* crop while its per-frame mask moves — stable crops are what
    the flow-propagation engines want.

    `frame_count` is honoured when given: a hand-edited track can name frames the
    video does not have, and `process.start` does not normalise segments again.
    """
    last_frame = None if frame_count is None else max(0, int(frame_count) - 1)

    if not shots:
        last_end = max(
            (item.intervals[-1].end_frame for item in tracks if item.intervals),
            default=0,
        )
        if last_frame is not None:
            last_end = min(last_end, last_frame)
        shots = [Interval(0, max(0, last_end))]

    segments: list[dict[str, Any]] = []
    for track in tracks:
        for interval in track.intervals:
            for shot in shots:
                start = max(interval.start_frame, shot.start_frame)
                end = min(interval.end_frame, shot.end_frame)
                if last_frame is not None:
                    start = min(start, last_frame)
                    end = min(end, last_frame)
                if end < start:
                    continue

                inside = [item for item in track.keyframes if start <= item.frame <= end]
                if not inside:
                    before = [item for item in track.keyframes if item.frame <= end]
                    chosen = before[-1] if before else (
                        track.keyframes[0] if track.keyframes else None
                    )
                    if chosen is None:
                        continue
                    # The fallback rectangle belongs to the range it is used in:
                    # re-labelling its frame is what keeps the compiled segment
                    # self-consistent for the per-frame mask.
                    inside = [Keyframe(min(max(chosen.frame, start), end), chosen.rect)]

                rect = BBox.union([item.rect for item in inside]).clamp(
                    frame_width, frame_height
                )
                segments.append(
                    {
                        "id": f"{track.id}@{start}-{end}",
                        "start_frame": int(start),
                        "end_frame": int(end),
                        "rect": rect.as_dict(),
                        "expand_px": int(expand_px),
                        "feather_px": int(feather_px),
                        "enabled": True,
                        "keyframes": [
                            {
                                "frame": int(item.frame),
                                "rect": item.rect.clamp(frame_width, frame_height).as_dict(),
                            }
                            for item in inside
                        ],
                    }
                )

    segments.sort(key=lambda item: (item["start_frame"], item["end_frame"], item["id"]))
    return segments
