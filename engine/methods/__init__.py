"""Method registration entry point."""

from __future__ import annotations

from engine.methods import annotation, assets, media, process, stubs, system
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager


def register_all(server: RpcServer) -> list[str]:
    """Register real handlers first, then fill the rest of the contract with stubs."""
    system.register(server)
    annotation.register(server)

    manager: TaskManager = process.create_manager(server)
    media.register(server)
    process.register(server, manager)
    assets.register(server)

    server.task_manager = manager  # type: ignore[attr-defined]
    return stubs.register_stubs(server)
