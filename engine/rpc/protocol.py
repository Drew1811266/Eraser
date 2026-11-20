"""JSON-RPC 2.0 framing over a newline-delimited stream."""

from __future__ import annotations

import json
from typing import Any

JSONRPC_VERSION = "2.0"


def dumps(payload: dict[str, Any]) -> str:
    """Serialise one protocol frame. No trailing newline; the writer adds it."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def loads(line: str) -> dict[str, Any]:
    """Parse one frame. Raises ValueError on anything that is not a JSON object."""
    parsed = json.loads(line)
    if not isinstance(parsed, dict):
        raise ValueError("frame is not a JSON object")
    return parsed


def make_request(request_id: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


def make_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error(request_id: Any, error: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        payload["params"] = params
    return payload
