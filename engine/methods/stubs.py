"""Register every contract method that has no implementation yet.

The contract deliberately declares the full v1 surface up front (see
docs/02-target-architecture.md). Declaring a method and returning a structured
NOT_IMPLEMENTED is honest and testable; silently omitting it would make the
shell guess which capabilities exist.
"""

from __future__ import annotations

from typing import Any

from engine.generated.contract import METHODS
from engine.rpc.errors import not_implemented
from engine.rpc.server import RpcServer


def register_stubs(server: RpcServer) -> list[str]:
    stubbed: list[str] = []
    already = set(server.registered())

    for name, spec in METHODS.items():
        if name in already:
            continue
        if spec.get("implemented"):
            # A method claims to be implemented but has no handler: that is a
            # wiring bug, and it must be loud rather than becoming a stub.
            raise RuntimeError(
                f"contract marks {name} as implemented but no handler was registered"
            )

        def handler(_params: dict[str, Any], _name: str = name) -> Any:
            raise not_implemented(_name)

        server.register(name, handler)
        stubbed.append(name)

    return stubbed
