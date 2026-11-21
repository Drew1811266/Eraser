"""Keep an engine from outliving the process that started it.

The RPC loop already exits on stdin EOF, and that covers an orderly shutdown.
It does not cover the cases that actually happen: the app is ended from Task
Manager, crashes, or is killed by a debugger. Nobody closes the pipe then — the
handle is simply never written to again, so the engine sits on stdin forever
holding a CUDA context and the model weights in VRAM.

So the parent is polled directly. On Windows `OpenProcess` on a dead pid fails;
on POSIX the parent becomes `init` (or a reaper), which is detected by pid change.
Either way the engine exits within one interval of the parent disappearing.
"""

from __future__ import annotations

import os
import sys
import threading

DEFAULT_INTERVAL_SECONDS = 5.0


def parent_is_alive(parent_pid: int) -> bool:
    """Whether *parent_pid* still refers to a running process."""
    if parent_pid <= 0:
        # No usable parent id (reparented, or the platform hides it).
        return True

    if sys.platform == "win32":
        return _windows_alive(parent_pid)
    return _posix_alive(parent_pid)


def _windows_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_INVALID_PARAMETER = 87

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Gone, or we are not allowed to look. A permission error means something
        # is still there, so only a "no such process" answer counts as dead.
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER

    try:
        exit_code = wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


def _posix_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ParentWatchdog:
    """Exits the process when the parent disappears."""

    def __init__(self, parent_pid: int | None = None, interval: float = DEFAULT_INTERVAL_SECONDS) -> None:
        self.parent_pid = os.getppid() if parent_pid is None else parent_pid
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.parent_pid <= 1:
            # Already orphaned or started by hand; nothing to watch.
            return
        self._thread = threading.Thread(target=self._run, name="parent-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if not parent_is_alive(self.parent_pid):
                # Bypass cleanup on purpose: the shell is gone, so there is
                # nothing left to answer, and a stuck CUDA teardown must not
                # keep the process alive.
                os._exit(0)
