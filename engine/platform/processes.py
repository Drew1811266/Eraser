"""Child-process handling, including the parts Windows does differently.

Two behaviours the reference implementation got wrong:

- It cancelled work with ``SIGTERM``. Windows has no such signal; ``kill()``
  there is TerminateProcess on the direct child only, so ffmpeg's children
  survived and half-written output files were left behind.
- It let child consoles flash. Every spawn here is hidden.

Cancellation is cooperative: callers pass a ``threading.Event`` and we escalate
to a process-tree kill, then wait for the process to actually be gone.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from threading import Event
from typing import Mapping, Sequence

POLL_INTERVAL_SECONDS = 0.2
KILL_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    def tail(self, lines: int = 6) -> str:
        text = (self.stderr or self.stdout or "").strip()
        return "\n".join(text.splitlines()[-lines:])


def no_window_kwargs() -> dict[str, int]:
    """Keep GUI apps from spawning a console window for each child."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def run(
    argv: Sequence[str],
    *,
    timeout: float | None = None,
    cancel: Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run a command to completion, honouring timeout and cancellation.

    Never raises for a non-zero exit: callers decide what a failure means.
    Raises only when the executable itself cannot be started.
    """
    popen_kwargs: dict[str, object] = {
        **no_window_kwargs(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
    }
    if os.name != "nt":
        # Own process group so a cancellation can take the whole tree down.
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(list(argv), **popen_kwargs)  # noqa: S603 - argv is explicit
    deadline = time.monotonic() + timeout if timeout else None

    while True:
        try:
            stdout, stderr = process.communicate(timeout=POLL_INTERVAL_SECONDS)
            return ProcessResult(process.returncode, stdout or "", stderr or "")
        except subprocess.TimeoutExpired:
            pass

        if cancel is not None and cancel.is_set():
            kill_tree(process)
            stdout, stderr = process.communicate()
            return ProcessResult(process.returncode, stdout or "", stderr or "", cancelled=True)

        if deadline is not None and time.monotonic() >= deadline:
            kill_tree(process)
            stdout, stderr = process.communicate()
            return ProcessResult(process.returncode, stdout or "", stderr or "", timed_out=True)


def kill_tree(process: subprocess.Popen[str]) -> None:
    """Terminate *process* and everything it spawned, then reap it."""
    if process.poll() is not None:
        return

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                **no_window_kwargs(),
                check=False,
                timeout=KILL_GRACE_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except OSError:
                pass

    try:
        process.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
