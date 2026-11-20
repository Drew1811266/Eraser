"""Method registration entry point."""

from __future__ import annotations

from engine.methods import annotation, stubs, system
from engine.rpc.server import RpcServer


def register_all(server: RpcServer) -> list[str]:
    """Register real handlers first, then fill the rest of the contract with stubs."""
    system.register(server)
    annotation.register(server)
    return stubs.register_stubs(server)
