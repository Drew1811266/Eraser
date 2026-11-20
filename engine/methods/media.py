"""media.* handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer


def _probe(path_text: str) -> dict[str, Any]:
    from engine.pipeline.watermark import VideoProcessor

    path = Path(path_text)
    if not path.is_file():
        raise RpcError("INVALID_PARAMS", f"File not found: {path}", {"path": str(path)})

    try:
        info = VideoProcessor().get_video_info(str(path))
    except Exception as exc:  # noqa: BLE001 - surface as a structured error
        raise RpcError(
            "INTERNAL_ERROR",
            f"Cannot read video metadata: {exc}",
            {"path": str(path)},
        ) from exc

    return {
        "path": str(path),
        "width": int(info.width),
        "height": int(info.height),
        "fps": float(info.fps),
        "frame_count": int(info.frame_count),
        "duration_seconds": float(info.duration),
        "has_audio": bool(info.has_audio),
        "video_codec": str(getattr(info, "codec", "") or ""),
        "container": path.suffix.lstrip(".").lower(),
    }


def register(server: RpcServer) -> None:
    def handle_probe(params: dict[str, Any]) -> dict[str, Any]:
        return _probe(str(params.get("path", "")))

    server.register("media.probe", handle_probe)
