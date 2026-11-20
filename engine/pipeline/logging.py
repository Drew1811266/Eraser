"""Engine logging. Everything goes to stderr; stdout is reserved for protocol frames."""

from __future__ import annotations

import sys
import threading
from datetime import datetime


class _Logger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._min_level = "info"

    def set_level(self, level: str) -> None:
        self._min_level = level.lower()

    def _emit(self, level: str, message: str) -> None:
        with self._lock:
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            print(f"[{stamp}] [{level}] {message}", file=sys.stderr)
        sys.stderr.flush()

    def debug(self, message: str, *args: object) -> None:
        self._emit("debug", message % args if args else message)

    def info(self, message: str, *args: object) -> None:
        self._emit("info", message % args if args else message)

    def warning(self, message: str, *args: object) -> None:
        self._emit("warn", message % args if args else message)

    def error(self, message: str, *args: object) -> None:
        self._emit("error", message % args if args else message)

    def exception(self, message: str, *args: object) -> None:
        self._emit("error", message % args if args else message)


logger = _Logger()
