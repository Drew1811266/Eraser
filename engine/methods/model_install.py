"""model.install: provision a detector runtime on demand.

The engine never imports torch; this task creates a private venv, installs the
pinned dependencies and verifies the weights. `model.installStatus` is a thin
alias of the task snapshot so the declared contract has no dead surface — the
shell may poll either it or `process.status`.
"""

from __future__ import annotations

import threading
from typing import Any

from engine.pipeline.detect import runtime
from engine.pipeline.detect.weights import DetectorInstallError
from engine.pipeline.logging import logger
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord

#: Only Florence exists as a detector runtime today.
SUPPORTED = {"florence2"}


def _run_install(manager: TaskManager, task: TaskRecord) -> None:
    try:
        manager.update(task, phase="install", message="Installing the detector runtime")

        def on_progress(share: float, message: str) -> None:
            manager.update(task, progress=float(share), message=str(message)[:120])

        state = runtime.install(progress=on_progress, cancel=task.cancel)
        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        manager.finish(task, result=state)
    except DetectorInstallError as exc:
        manager.finish(task, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("detector install failed: %s", exc)
        manager.finish(task, error=str(exc))


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_install(params: dict[str, Any]) -> dict[str, Any]:
        model_id = str(params.get("model_id") or "").strip()
        if model_id not in SUPPORTED:
            raise RpcError(
                "INVALID_PARAMS",
                f"{model_id or '(empty)'} is not a detector model; use "
                "model.download.start for inpaint and upscale models",
                {"model_id": model_id},
            )
        force = bool(params.get("force"))
        task = manager.create("model_install")
        if runtime.is_ready() and not force:
            # Idempotent: the UI may offer install on every launch.
            manager.finish(task, result=runtime.state())
            return {"task_id": task.task_id}
        threading.Thread(
            target=_run_install,
            args=(manager, task),
            name=f"install-{task.task_id[:8]}",
            daemon=True,
        ).start()
        return {"task_id": task.task_id}

    def handle_status(params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id") or "")
        task = manager.get(task_id)
        if task is None:
            raise RpcError("TASK_NOT_FOUND", f"Unknown task: {task_id}", {"task_id": task_id})
        return manager.snapshot(task)

    server.register("model.install", handle_install)
    server.register("model.installStatus", handle_status)
