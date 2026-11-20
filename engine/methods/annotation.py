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


def _normalize_rect(rect: dict[str, Any], width: int, height: int) -> dict[str, int]:
    x = _coerce_int(rect.get("x", 0), 0)
    y = _coerce_int(rect.get("y", 0), 0)
    w = _coerce_int(rect.get("width", 0), 0)
    h = _coerce_int(rect.get("height", 0), 0)

    x = max(0, min(x, max(0, width - 1)))
    y = max(0, min(y, max(0, height - 1)))
    w = max(1, w)
    h = max(1, h)

    if x + w > width:
        w = max(1, width - x)
    if y + h > height:
        h = max(1, height - y)

    return {"x": x, "y": y, "width": w, "height": h}


def normalize_segments(
    segments: list[Any],
    video: dict[str, Any],
    now: Clock = _now_iso,
) -> list[dict[str, Any]]:
    width = _coerce_int(video.get("width", 0), 0)
    height = _coerce_int(video.get("height", 0), 0)
    frame_count = _coerce_int(video.get("frame_count", 0), 0)
    max_frame = max(0, frame_count - 1)
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
        start_frame = max(0, min(start_frame, max_frame))
        end_frame = max(0, min(end_frame, max_frame))
        if end_frame < start_frame:
            start_frame, end_frame = end_frame, start_frame

        normalized.append(
            {
                "id": str(item.get("id") or uuid4().hex),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "rect": _normalize_rect(rect, width, height),
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
