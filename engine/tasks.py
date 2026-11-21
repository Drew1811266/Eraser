"""Background task registry for long-running engine work.

The RPC contract requires `process.start` / `model.install` to return a task id
immediately and stream progress as notifications, so every long operation is a
task here rather than a blocking call.

Cancellation is cooperative: a task owns a ``threading.Event`` and the worker
checks it. Nothing here sends OS signals, because Windows has no SIGTERM and
process.kill() would leave ffmpeg grandchildren running.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

TERMINAL_STATES = frozenset({"success", "failed", "cancelled"})


@dataclass
class TaskRecord:
    task_id: str
    kind: str
    state: str = "running"
    phase: str = "prepare"
    progress: float = 0.0
    processed: int = 0
    total: int = 0
    eta_seconds: float | None = None
    message: str = ""
    error: str = ""
    result: dict[str, Any] | None = None
    started_at: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_STATES


class TaskManager:
    def __init__(self, emit: Callable[[str, dict[str, Any]], None], *, max_finished: int = 20) -> None:
        self._emit = emit
        self._max_finished = max_finished
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def create(self, kind: str) -> TaskRecord:
        task = TaskRecord(task_id=uuid.uuid4().hex, kind=kind)
        with self._lock:
            self._tasks[task.task_id] = task
            self._prune()
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        task = self.get(task_id)
        if task is None or task.finished:
            return False
        task.cancel.set()
        self.update(task, phase=task.phase, message="Cancelling...")
        return True

    # -- updates -----------------------------------------------------------
    def update(self, task: TaskRecord, **fields: Any) -> None:
        """Apply fields and emit a progress notification.

        `progress` is forced monotonic: the UI treats a decreasing value as a
        restart, and several pipeline phases report their own 0..1 curve.
        """
        with self._lock:
            if "progress" in fields and fields["progress"] is not None:
                candidate = float(fields["progress"])
                fields["progress"] = max(task.progress, min(1.0, candidate))
            for key, value in fields.items():
                if hasattr(task, key):
                    setattr(task, key, value)

        self._emit("progress", self.snapshot(task))

    def finish(self, task: TaskRecord, *, result: dict[str, Any] | None = None, error: str = "") -> None:
        with self._lock:
            task.state = "cancelled" if task.cancel.is_set() and error else ("failed" if error else "success")
            task.error = error
            task.result = result
            task.progress = 1.0 if not error else task.progress
            task.phase = "finalize"
            task.message = error or "Done"
            task.eta_seconds = 0.0
        self._emit("progress", self.snapshot(task))

    def snapshot(self, task: TaskRecord) -> dict[str, Any]:
        eta = task.eta_seconds
        if eta is None and task.total > 0 and task.processed > 0 and not task.finished:
            elapsed = time.monotonic() - task.started_at
            per_item = elapsed / task.processed
            eta = round(max(0.0, per_item * (task.total - task.processed)), 1)

        snapshot: dict[str, Any] = {
            "task_id": task.task_id,
            "state": task.state,
            "phase": task.phase,
            "progress": round(task.progress, 4),
            "processed": task.processed,
            "total": task.total,
            "eta_seconds": eta,
            "message": task.message,
        }
        # `finish()` emits its own notification, so the result has to travel with
        # it: a shell that only learns "success" but never the path has to keep
        # polling a task that is already over.
        if task.result:
            snapshot["result"] = task.result
        if task.error:
            snapshot["error"] = task.error
        return snapshot

    # -- housekeeping ------------------------------------------------------
    def _prune(self) -> None:
        if len(self._tasks) <= self._max_finished:
            return
        finished = [t for t in self._tasks.values() if t.finished]
        finished.sort(key=lambda item: item.started_at)
        for task in finished[: max(0, len(self._tasks) - self._max_finished)]:
            self._tasks.pop(task.task_id, None)
