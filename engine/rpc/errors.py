"""Structured errors shared by every engine method.

A handler raises :class:`RpcError`; the server turns it into a JSON-RPC error
object. The numeric code is protocol-level, while ``data.code`` is the stable
application code the UI maps onto an i18n message key.
"""

from __future__ import annotations

from typing import Any

from engine.generated.contract import ERROR_CODES

FALLBACK_JSONRPC_CODE = -32603  # internal error


def jsonrpc_code(code: str) -> int:
    entry = ERROR_CODES.get(code)
    return int(entry["jsonrpc"]) if entry else FALLBACK_JSONRPC_CODE


def is_retryable(code: str) -> bool:
    entry = ERROR_CODES.get(code)
    return bool(entry["retryable"]) if entry else True


class RpcError(Exception):
    """An error that is safe to surface to the shell."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    @property
    def numeric_code(self) -> int:
        return jsonrpc_code(self.code)

    def to_error_object(self) -> dict[str, Any]:
        return {
            "code": self.numeric_code,
            "message": self.message,
            "data": {
                "code": self.code,
                "retryable": is_retryable(self.code),
                "detail": self.detail,
            },
        }


def not_implemented(method: str) -> RpcError:
    return RpcError(
        "NOT_IMPLEMENTED",
        f"Method is defined by the contract but not implemented yet: {method}",
        {"method": method},
    )


def invalid_params(message: str, detail: dict[str, Any] | None = None) -> RpcError:
    return RpcError("INVALID_PARAMS", message, detail)
