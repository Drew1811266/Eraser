"""process.* handlers: start, cancel, status.

`process.start` returns a task id immediately and the work happens on a worker
thread. Progress is pushed as notifications; `process.status` exists for the
shell to resync after a dropped event, not as the primary channel.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.platform.paths import default_output_dir, ensure_dir
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord

_live_processors: dict[str, Any] = {}
_live_lock = threading.Lock()


def build_output_path(input_path: str, output_dir: str | None) -> Path:
    source = Path(input_path)
    directory = Path(output_dir) if output_dir else default_output_dir()
    ensure_dir(directory)
    return directory / f"{source.stem}_no_watermark.mp4"


def _apply_progress(manager: TaskManager, task: TaskRecord, payload: Any) -> None:
    """The pipeline reports a few different shapes; normalise them all."""
    if isinstance(payload, dict):
        fields: dict[str, Any] = {}
        if "progress" in payload:
            fields["progress"] = float(payload["progress"])
        if "phase" in payload and payload["phase"]:
            fields["phase"] = str(payload["phase"])
        if "total" in payload:
            fields["total"] = int(payload["total"])
        step = payload.get("processed", payload.get("step"))
        if step is not None:
            fields["processed"] = int(step)
        message = payload.get("message") or payload.get("status")
        if message:
            fields["message"] = str(message)
        if fields:
            manager.update(task, **fields)
        return

    if isinstance(payload, (int, float)):
        manager.update(task, progress=float(payload))
        return

    if isinstance(payload, str):
        manager.update(task, message=payload)


def _run_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    output_path: Path,
    segments: list[dict[str, Any]],
    model_id: str,
) -> None:
    from engine.pipeline.remover import ModelNotFound, WatermarkRemover
    from engine.pipeline.watermark import VideoProcessor

    try:
        processor = VideoProcessor(remover=WatermarkRemover())
        with _live_lock:
            _live_processors[task.task_id] = processor

        manager.update(task, phase="prepare", message="Loading models")

        result = processor.process_video(
            input_path,
            str(output_path),
            segments,
            model_id,
            progress_callback=lambda payload: _apply_progress(manager, task, payload),
            status_callback=lambda message: manager.update(task, message=str(message)),
        )

        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return

        normalised = result if isinstance(result, dict) else {}
        final_path = normalised.get("output_path") or normalised.get("output") or str(output_path)
        manager.finish(
            task,
            result={
                "output_path": str(final_path),
                "requested_model_id": normalised.get("requested_model_id", model_id),
                "effective_model_id": normalised.get("effective_model_id", model_id),
                "model_warning": normalised.get("model_warning", ""),
            },
        )
    except ModelNotFound as exc:
        manager.finish(task, error=f"MODEL_MISSING: {exc}")
    except Exception as exc:  # noqa: BLE001 - boundary
        logger.error("process task failed: %s", exc)
        manager.finish(task, error=str(exc))
    finally:
        with _live_lock:
            _live_processors.pop(task.task_id, None)


def create_manager(server: RpcServer) -> TaskManager:
    return TaskManager(server.notify)


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})

        segments = params.get("segments")
        if not isinstance(segments, list) or not segments:
            raise RpcError("INVALID_PARAMS", "segments must be a non-empty array")

        model_id = str(params.get("model_id") or "lama_roi")
        output_path = build_output_path(input_path, params.get("output_dir"))

        task = manager.create("process")
        thread = threading.Thread(
            target=_run_task,
            args=(manager, task, input_path, output_path, segments, model_id),
            name=f"process-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    def handle_cancel(params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id", ""))
        task = manager.get(task_id)
        if task is None:
            raise RpcError("TASK_NOT_FOUND", f"Unknown task: {task_id}", {"task_id": task_id})

        manager.cancel(task_id)
        with _live_lock:
            processor = _live_processors.get(task_id)
        if processor is not None:
            try:
                processor.stop_processing()
            except Exception as exc:  # noqa: BLE001
                logger.warning("stop_processing failed: %s", exc)
        return {"ok": True}

    def handle_status(params: dict[str, Any]) -> dict[str, Any]:
        task_id = str(params.get("task_id", ""))
        task = manager.get(task_id)
        if task is None:
            raise RpcError("TASK_NOT_FOUND", f"Unknown task: {task_id}", {"task_id": task_id})
        snapshot = manager.snapshot(task)
        if task.result:
            snapshot["result"] = task.result
        if task.error:
            snapshot["error"] = task.error
        return snapshot

    server.register("process.start", handle_start)
    server.register("process.cancel", handle_cancel)
    server.register("process.status", handle_status)
