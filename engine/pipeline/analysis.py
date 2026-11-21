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
