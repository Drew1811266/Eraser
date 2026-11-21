"""Engine entry point: speaks JSON-RPC on stdin/stdout until EOF or shutdown.

Run with ``python -m engine.main`` from the repository root, or as the frozen
``eraser-engine.exe`` from the packaged app.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Sequence, TextIO

from engine import __version__
from engine.methods import register_all
from engine.methods.system import build_capabilities
from engine.platform.watchdog import ParentWatchdog
from engine.rpc.server import RpcServer

NOISE_ENV = "ERASER_STDERR_NOISE"


def _configure_streams() -> None:
    """Force UTF-8 + LF on both pipes.

    On Windows the default console encoding is not UTF-8, and the default stdout
    newline translation would corrupt the frame delimiter.
    """
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", newline="\n")
        except (ValueError, OSError):
            pass


def _emit_noise(count: int) -> None:
    """Write junk to stderr on purpose.

    Used by the contract test that proves logging cannot corrupt stdout.
    """
    for index in range(count):
        print(f"[noise] {index} {os.urandom(8).hex()}", file=sys.stderr)
    sys.stderr.flush()


def _open_trace(path: str | None) -> TextIO | None:
    if not path:
        return None
    try:
        return open(path, "w", encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"cannot open trace file {path}: {exc}", file=sys.stderr)
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eraser-engine", description="Eraser video engine")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--rpc-trace", metavar="FILE", default=None, help="record every frame")
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    _configure_streams()
    trace = _open_trace(args.rpc_trace)

    server = RpcServer(trace=trace)
    register_all(server)

    noise = int(os.environ.get(NOISE_ENV, "0") or 0)
    if noise > 0:
        _emit_noise(noise)

    # The shell waits for this before enabling the UI, so it must be the very
    # first frame after registration succeeds.
    server.notify("ready", build_capabilities(server))

    # Stdin EOF handles an orderly shutdown; this handles the shell being killed,
    # which is the case that leaves a CUDA context pinned to a dead window.
    watchdog = ParentWatchdog()
    watchdog.start()

    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    finally:
        watchdog.stop()
        if trace is not None:
            trace.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
