"""system.* handlers: liveness, capabilities, shutdown."""

from __future__ import annotations

import sys
from typing import Any

from engine import __version__
from engine.generated.contract import IMPLEMENTED_METHODS
from engine.platform import current_platform_key
from engine.platform.device import detect_devices
from engine.platform import ort_runtime
from engine.rpc.server import RpcServer


def build_capabilities(server: RpcServer) -> dict[str, Any]:
    return {
        "engine_version": __version__,
        "python_version": sys.version.split()[0],
        "platform": current_platform_key(),
        "methods": list(IMPLEMENTED_METHODS),
        "devices": detect_devices(),
        "runtime": ort_runtime.summary(),
    }


def register(server: RpcServer) -> None:
    def ping(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "pong": True,
            "uptime_ms": server.uptime_ms(),
            "engine_version": __version__,
        }

    def capabilities(_params: dict[str, Any]) -> dict[str, Any]:
        return build_capabilities(server)

    def shutdown(_params: dict[str, Any]) -> dict[str, Any]:
        server.stop()
        return {"ok": True}

    server.register("system.ping", ping)
    server.register("system.capabilities", capabilities)
    server.register("system.shutdown", shutdown)
