"""annotation.* handlers.

``normalize`` is a faithful port of ``normalize_segments`` from the reference
implementation (src/core/annotations.py). Behaviour that must not drift:

- a non-dict segment or non-dict rect is dropped, not repaired;
- start/end are clamped to [0, frame_count - 1] and swapped if inverted;
- rect is clamped into the frame, width/height forced to at least 1, and
  truncated (never scaled) when it overflows the right/bottom edge;
- expand_px defaults to 5, feather_px to 3, enabled to true;
- created_at is preserved when present, updated_at is always refreshed.

Keeping this exact matters: existing .wmr.json sidecars were written by the
Python build, and the parity test vector pins these rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from engine.rpc.errors import invalid_params
from engine.rpc.server import RpcServer

Clock = Callable[[], str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_rect(
    rect: dict[str, Any],
    width: int,
    height: int,
    clamp: bool = True,
) -> dict[str, int]:
    x = _coerce_int(rect.get("x", 0), 0)
    y = _coerce_int(rect.get("y", 0), 0)
    w = _coerce_int(rect.get("width", 0), 0)
    h = _coerce_int(rect.get("height", 0), 0)

    w = max(1, w)
    h = max(1, h)
    if not clamp:
        # Without the media there is nothing to clamp against; truncating to a
        # 0-pixel canvas would silently rewrite every stored rectangle.
        return {"x": max(0, x), "y": max(0, y), "width": w, "height": h}

    x = max(0, min(x, max(0, width - 1)))
    y = max(0, min(y, max(0, height - 1)))
    if x + w > width:
        w = max(1, width - x)
    if y + h > height:
        h = max(1, height - y)

    return {"x": x, "y": y, "width": w, "height": h}


def normalize_segments(
    segments: list[Any],
    video: dict[str, Any] | None = None,
    now: Clock = _now_iso,
) -> list[dict[str, Any]]:
    """Validate segments, clamping them to the frame range when it is known.

    `video` is optional because storage does not always have the media at hand
    (reading a sidecar for a file that was moved, for instance). Clamping without
    it would silently collapse every range to frame 0, which is worse than
    keeping what was stored, so it is skipped when the geometry is unknown.
    """
    info = video or {}
    width = _coerce_int(info.get("width", 0), 0)
    height = _coerce_int(info.get("height", 0), 0)
    frame_count = _coerce_int(info.get("frame_count", 0), 0)
    max_frame = max(0, frame_count - 1)
    # No geometry at all means "unknown", not "zero-length": clamping would
    # destroy stored ranges. A caller that passes a video dict is telling us the
    # truth, so its numbers are honoured even when they are degenerate.
    clamp_frames = video is not None
    timestamp = now()

    normalized: list[dict[str, Any]] = []
    for item in segments:
        if not isinstance(item, dict):
            continue
        rect = item.get("rect", {})
        if not isinstance(rect, dict):
            continue

        start_frame = _coerce_int(item.get("start_frame", 0), 0)
        end_frame = _coerce_int(item.get("end_frame", start_frame), start_frame)
        if clamp_frames:
            start_frame = max(0, min(start_frame, max_frame))
            end_frame = max(0, min(end_frame, max_frame))
        else:
            start_frame = max(0, start_frame)
            end_frame = max(0, end_frame)
        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame

        normalized.append(
            {
                "id": str(item.get("id") or uuid4().hex),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "rect": _normalize_rect(rect, width, height, clamp=video is not None),
                "expand_px": max(0, _coerce_int(item.get("expand_px", 5), 5)),
                "feather_px": max(0, _coerce_int(item.get("feather_px", 3), 3)),
                "enabled": bool(item.get("enabled", True)),
                "created_at": str(item.get("created_at") or timestamp),
                "updated_at": timestamp,
            }
        )

    return normalized


def register(server: RpcServer, clock: Clock = _now_iso) -> None:
    def handle_normalize(params: dict[str, Any]) -> dict[str, Any]:
        video = params.get("video")
        if not isinstance(video, dict):
            raise invalid_params("video must be an object")

        segments = params.get("segments")
        if not isinstance(segments, list):
            raise invalid_params("segments must be an array")

        return {"segments": normalize_segments(segments, video, now=clock)}

    server.register("annotation.normalize", handle_normalize)

    def handle_compile(params: dict[str, Any]) -> dict[str, Any]:
        """Tracks -> segments.

        Lives on the engine side on purpose: the compile rules have to agree with
        `normalize_segments`, and two copies of "clamp to the frame" would drift.
        """
        from engine.pipeline.analysis import (
            BBox,
            Interval,
            Keyframe,
            WatermarkTrack,
            compile_tracks,
        )

        raw_tracks = params.get("watermarks")
        raw_shots = params.get("shots")
        video = params.get("video")
        if not isinstance(raw_tracks, list) or not isinstance(raw_shots, list):
            raise invalid_params("watermarks and shots must be arrays")
        if not isinstance(video, dict):
            raise invalid_params("video must be an object")

        tracks: list[WatermarkTrack] = []
        for raw in raw_tracks:
            data = raw if isinstance(raw, dict) else {}
            intervals = [
                Interval(
                    _coerce_int(item.get("start_frame", 0), 0),
                    _coerce_int(item.get("end_frame", 0), 0),
                )
                for item in (data.get("intervals") or [])
                if isinstance(item, dict)
            ]
            keyframes = [
                Keyframe(
                    _coerce_int(item.get("frame", 0), 0),
                    BBox.from_dict(item.get("rect")),
                )
                for item in (data.get("keyframes") or [])
                if isinstance(item, dict)
            ]
            tracks.append(
                WatermarkTrack(
                    id=str(data.get("id") or uuid4().hex),
                    intervals=intervals,
                    keyframes=keyframes,
                    motion=str(data.get("motion") or "static"),
                    confidence=float(data.get("confidence") or 0.0),
                    source=str(data.get("source") or "manual"),
                    notes=[str(item) for item in (data.get("notes") or [])],
                )
            )

        shots = [
            Interval(
                _coerce_int(item.get("start_frame", 0), 0),
                _coerce_int(item.get("end_frame", 0), 0),
            )
            for item in raw_shots
            if isinstance(item, dict)
        ]
        segments = compile_tracks(
            tracks,
            shots=shots,
            frame_width=_coerce_int(video.get("width", 0), 0),
            frame_height=_coerce_int(video.get("height", 0), 0),
        )
        return {"segments": segments}

    server.register("annotation.compile", handle_compile)
