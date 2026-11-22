"""Wire format between the engine and the Florence worker.

Torch-free and importable from the engine on purpose: the protocol is the part
that can be wrong without anyone noticing, so it must be unit-testable without a
444 MB model or a GPU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from engine.pipeline.analysis import BBox

DEFAULT_PROMPT = "watermark"
#: Florence-2 task token. `OPEN_VOCABULARY_DETECTION` takes a free phrase and
#: measured the same boxes as CAPTION_TO_PHRASE_GROUNDING in the spike.
DEFAULT_TASK = "<OPEN_VOCABULARY_DETECTION>"
DEFAULT_BATCH = 8


def encode_job(
    weights_dir: str | Path,
    images: Iterable[tuple[int, str | Path]],
    *,
    prompt: str = DEFAULT_PROMPT,
    task: str = DEFAULT_TASK,
    batch: int = DEFAULT_BATCH,
) -> dict[str, Any]:
    size = int(batch)
    return {
        "weights": str(weights_dir),
        "prompt": prompt,
        "task": task,
        "batch": size if size > 0 else DEFAULT_BATCH,
        "images": [{"frame": int(frame), "path": str(path)} for frame, path in images],
    }


def parse_line(line: str) -> dict[str, Any]:
    """Parse one NDJSON line; anything unusable becomes an error payload.

    A worker that dies mid-line must not crash the engine, so this never raises.
    """
    text = (line or "").strip()
    if not text:
        return {"type": "error", "message": "empty line"}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"type": "error", "message": f"bad json: {exc}"}
    if not isinstance(payload, dict) or "type" not in payload:
        return {"type": "error", "message": "not a protocol object"}
    return payload


def _to_box(raw: Any) -> BBox | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return BBox(int(round(x1)), int(round(y1)), int(round(x2 - x1)), int(round(y2 - y1)))


def collect_frames(lines: Iterable[str], *, total: int) -> dict[int, list[BBox]]:
    """Fold the worker's stream into ``{frame: [box]}``.

    `total` is advisory — it documents how many frames were asked for; frames the
    worker never reported stay absent rather than silently becoming "no
    watermark", which is how a crashed worker would otherwise look like success.
    """
    del total
    frames: dict[int, list[BBox]] = {}
    for line in lines:
        payload = parse_line(line)
        if payload.get("type") != "frame":
            continue
        try:
            frame = int(payload.get("frame", -1))
        except (TypeError, ValueError):
            continue
        boxes = [
            box
            for box in (_to_box(raw) for raw in (payload.get("boxes") or []))
            if box is not None
        ]
        frames[frame] = boxes
    return frames
