"""annotation.* storage handlers.

The reference kept a JSON sidecar next to the video so annotations travel with
the clip and survive an app reinstall. Same decision here; what changes is that
the file is written atomically and read defensively — a truncated sidecar from a
power cut must not stop the app from opening the video.

`annotation.normalize` (in `engine/methods/annotation.py`) is the one place that
decides what a valid segment looks like; this module only stores what that
function produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.methods.annotation import normalize_segments
from engine.pipeline.logging import logger
from engine.platform.files import atomic_write
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer

SIDECAR_SUFFIX = ".annotations.json"
SIDECAR_VERSION = 1


def sidecar_path(video_path: str | Path) -> Path:
    source = Path(video_path)
    return source.with_name(source.stem + SIDECAR_SUFFIX)


def _video_info(path: Path) -> dict[str, Any] | None:
    """Geometry for clamping; a moved or missing video is not an error here."""
    if not path.is_file():
        return None
    try:
        from engine.pipeline.watermark import VideoProcessor

        info = VideoProcessor(remover=None).get_video_info(str(path))
    except Exception as exc:  # noqa: BLE001 - storage must not depend on codecs
        logger.warning("cannot read %s for clamping: %s", path, exc)
        return None
    return {"width": info.width, "height": info.height, "frame_count": info.frame_count}


def _load(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(params.get("video_path", "")))
    target = sidecar_path(path)
    if not target.is_file():
        return {"exists": False, "sidecar_path": str(target), "segments": []}

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ignoring unreadable annotation sidecar %s: %s", target, exc)
        return {"exists": False, "sidecar_path": str(target), "segments": []}

    raw = payload.get("segments") if isinstance(payload, dict) else payload
    # Normalising on read means an older sidecar (or a hand-edited one) cannot
    # put impossible frame ranges into the UI.
    segments = normalize_segments(raw if isinstance(raw, list) else [], _video_info(path))
    return {"exists": True, "sidecar_path": str(target), "segments": segments}


def _save(params: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(params.get("video_path", "")))
    segments = params.get("segments")
    if not isinstance(segments, list):
        raise RpcError("INVALID_PARAMS", "segments must be an array")
    if not path.name:
        raise RpcError("INVALID_PARAMS", "video_path is required")

    normalised = normalize_segments(segments, _video_info(path))
    target = sidecar_path(path)
    payload = {
        "version": SIDECAR_VERSION,
        "video": path.name,
        "segments": normalised,
    }
    atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info("saved %d annotation segment(s) to %s", len(normalised), target)
    return {"sidecar_path": str(target), "segments": normalised}


def _delete(params: dict[str, Any]) -> dict[str, Any]:
    target = sidecar_path(str(params.get("video_path", "")))
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:  # noqa: BLE001 - a locked file is not fatal here
        logger.warning("cannot delete %s: %s", target, exc)
        return {"ok": False, "deleted": False}
    return {"ok": True, "deleted": True}


def register(server: RpcServer) -> None:
    server.register("annotation.load", _load)
    server.register("annotation.save", _save)
    server.register("annotation.delete", _delete)
