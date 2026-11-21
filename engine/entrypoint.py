"""Frozen-executable entry point.

PyInstaller needs a real script to analyse; `python -m engine.main` is not one.
Keeping the launcher this thin means the frozen build exercises exactly the same
`main()` as the development run, so packaging cannot drift from the tested path.

The frozen engine is also the app's CLI: with no arguments it speaks JSON-RPC on
stdio, and with `process`/`probe`/`info` it behaves like `engine.cli`.
"""

from __future__ import annotations

import sys


def run() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] in {"info", "probe", "process", "upscale", "analyze"}:
        from engine.cli import main as cli_main

        return cli_main(argv)

    from engine.main import main as rpc_main

    return rpc_main(argv)


if __name__ == "__main__":
    sys.exit(run())
