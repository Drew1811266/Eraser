"""assets.* and model.* handlers."""

from __future__ import annotations

from typing import Any

from engine.platform import ffmpeg as ffmpeg_tools
from engine.rpc.server import RpcServer


def _model_entries() -> list[dict[str, Any]]:
    from engine.pipeline import upscaler
    from engine.pipeline.remover import ModelNotFound, resolve_model_path

    try:
        path = resolve_model_path()
        installed = path.is_file()
        detail = str(path)
    except ModelNotFound:
        installed = False
        detail = ""

    upscale_installed = upscaler.is_installed()
    upscale_detail = ""
    if not upscale_installed:
        upscale_detail = f"Real-ESRGAN ONNX weights not found ({upscaler.default_model_path()})"

    return [
        {
            "model_id": "lama_roi",
            "display_name": "LaMa (ONNX)",
            "engine": "lama",
            "installed": installed,
            "implemented": True,
            "blocked_reason": "" if installed else f"LaMa ONNX weights not found ({detail})",
        },
        {
            "model_id": "realesrgan_x4",
            "display_name": "Real-ESRGAN x4plus (ONNX)",
            "engine": "realesrgan",
            "installed": upscale_installed,
            "implemented": True,
            "blocked_reason": upscale_detail,
        },
    ]


def _verify_assets() -> dict[str, Any]:
    missing: list[str] = []

    for tool in (ffmpeg_tools.FFMPEG, ffmpeg_tools.FFPROBE):
        if not ffmpeg_tools.resolve(tool).available:
            missing.append(tool)

    for entry in _model_entries():
        if not entry["installed"]:
            missing.append(f"model:{entry['model_id']}")

    return {"ok": not missing, "missing": missing}


def register(server: RpcServer) -> None:
    def handle_list(_params: dict[str, Any]) -> dict[str, Any]:
        return {"models": _model_entries()}

    def handle_verify(_params: dict[str, Any]) -> dict[str, Any]:
        return _verify_assets()

    server.register("model.list", handle_list)
    server.register("assets.verify", handle_verify)
