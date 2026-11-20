"""The JSON-RPC server: reads frames from stdin, writes frames to stdout.

Hard rule: stdout carries protocol frames only. Everything else (including
tracebacks) must go to stderr, otherwise a single stray print corrupts the
stream and the shell sees a "protocol broken" failure it cannot diagnose.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from typing import Any, Callable, TextIO

from engine.generated.contract import METHODS, TYPES
from engine.rpc import protocol
from engine.rpc.errors import RpcError
from engine.rpc.schema import validate

Handler = Callable[[dict[str, Any]], Any]


class RpcServer:
    def __init__(
        self,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        trace: TextIO | None = None,
    ) -> None:
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._trace = trace
        self._handlers: dict[str, Handler] = {}
        self._write_lock = threading.Lock()
        self._stopped = threading.Event()
        self._started = time.monotonic()

    # -- registration ------------------------------------------------------
    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ValueError(f"handler already registered: {method}")
        if method not in METHODS:
            raise ValueError(f"method is not in the contract: {method}")
        self._handlers[method] = handler

    def registered(self) -> list[str]:
        return sorted(self._handlers)

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stopped.set()

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def uptime_ms(self) -> float:
        return round((time.monotonic() - self._started) * 1000, 3)

    # -- outbound ----------------------------------------------------------
    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a server-initiated notification (progress, ready, ...)."""
        self._send(protocol.make_notification(method, params))

    def _send(self, payload: dict[str, Any]) -> None:
        line = protocol.dumps(payload)
        with self._write_lock:
            self._stdout.write(line + "\n")
            self._stdout.flush()
        self._trace_write("send", line)

    def _trace_write(self, direction: str, line: str) -> None:
        if self._trace is None:
            return
        try:
            self._trace.write(f"{direction}\t{line}\n")
            self._trace.flush()
        except OSError:
            # Tracing must never take the protocol down.
            self._trace = None

    # -- main loop ---------------------------------------------------------
    def serve(self) -> None:
        while not self._stopped.is_set():
            line = self._stdin.readline()
            if line == "":
                break  # EOF: the parent went away.
            stripped = line.strip()
            if not stripped:
                continue
            self._dispatch(stripped)

    def _dispatch(self, line: str) -> None:
        self._trace_write("recv", line)

        try:
            payload = protocol.loads(line)
        except ValueError as exc:
            self._send(
                protocol.make_error(
                    None, RpcError("PARSE_ERROR", f"Malformed JSON frame: {exc}").to_error_object()
                )
            )
            return

        version = payload.get("jsonrpc")
        is_request = "id" in payload
        request_id = payload.get("id")

        if version is not None and version != protocol.JSONRPC_VERSION:
            if is_request:
                self._reject(request_id, RpcError("INVALID_REQUEST", f"Unsupported jsonrpc version: {version}"))
            return

        method = payload.get("method")
        if not isinstance(method, str) or not method:
            if is_request:
                self._reject(request_id, RpcError("INVALID_REQUEST", "Missing method"))
            return

        params = payload.get("params", {})
        if not isinstance(params, dict):
            if is_request:
                self._reject(request_id, RpcError("INVALID_PARAMS", "params must be an object"))
            return

        handler = self._handlers.get(method)
        if handler is None:
            if is_request:
                self._reject(
                    request_id,
                    RpcError("METHOD_NOT_FOUND", f"Unknown method: {method}", {"method": method}),
                )
            return

        spec = METHODS.get(method, {})
        problems = validate(params, spec.get("params", {}), TYPES, "params")
        if problems:
            if is_request:
                self._reject(
                    request_id,
                    RpcError(
                        "INVALID_PARAMS",
                        "Invalid params: " + "; ".join(problems),
                        {"problems": problems},
                    ),
                )
            return

        try:
            result = handler(params)
        except RpcError as exc:
            if is_request:
                self._send(protocol.make_error(request_id, exc.to_error_object()))
            return
        except Exception as exc:  # noqa: BLE001 - boundary must not leak
            traceback.print_exc(file=sys.stderr)
            if is_request:
                self._send(
                    protocol.make_error(
                        request_id,
                        RpcError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}").to_error_object(),
                    )
                )
            return

        if is_request:
            self._send(protocol.make_result(request_id, result))

    def _reject(self, request_id: Any, error: RpcError) -> None:
        self._send(protocol.make_error(request_id, error.to_error_object()))
