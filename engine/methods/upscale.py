"""upscale.* handlers.

Upscaling is a task like processing: it returns a task id immediately, streams
progress, and is inspected and cancelled through `process.status` /
`process.cancel`. Reusing that lifecycle instead of inventing a parallel one is
deliberate — the shell already knows how to render a task, and a second set of
status/cancel methods would only give it a second way to be wrong.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord

SUPPORTED_SCALES = (2, 4)


def build_output_path(input_path: str, output_dir: str | None, scale: int) -> Path:
    from engine.platform.paths import default_output_dir, ensure_dir

    source = Path(input_path)
    directory = Path(output_dir) if output_dir else default_output_dir()
    ensure_dir(directory)
    return directory / f"{source.stem}_x{scale}.mp4"


def _apply_progress(manager: TaskManager, task: TaskRecord, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    fields: dict[str, Any] = {}
    if "progress" in payload:
        fields["progress"] = float(payload["progress"])
    if "phase" in payload and payload["phase"]:
        fields["phase"] = str(payload["phase"])
    if "total" in payload:
        fields["total"] = int(payload["total"])
    if "processed" in payload:
        fields["processed"] = int(payload["processed"])
    if payload.get("message"):
        fields["message"] = str(payload["message"])
    if fields:
        manager.update(task, **fields)


def _run_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    output_path: Path,
    scale: int,
) -> None:
    from engine.pipeline.upscale_job import UpscaleError, _Cancelled, upscale_video
    from engine.pipeline.upscaler import UpscaleModelNotFound

    try:
        manager.update(task, phase="prepare", message="加载超分模型")
        result = upscale_video(
            input_path,
            output_path,
            scale=scale,
            progress=lambda payload: _apply_progress(manager, task, payload),
            cancel=task.cancel,
        )
        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        manager.finish(task, result={
            "output_path": result["output_path"],
            "width": result["width"],
            "height": result["height"],
            "scale": result["scale"],
        })
    except (UpscaleModelNotFound, UpscaleError) as exc:
        manager.finish(task, error=f"UPSCALE_FAILED: {exc}")
    except _Cancelled:
        manager.finish(task, error="Cancelled")
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("upscale task failed: %s", exc)
        manager.finish(task, error=str(exc))


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})

        raw_scale = params.get("scale", 4)
        try:
            scale = int(raw_scale)
        except (TypeError, ValueError):
            scale = 0
        if scale not in SUPPORTED_SCALES:
            raise RpcError(
                "INVALID_PARAMS",
                f"scale must be one of {list(SUPPORTED_SCALES)}",
                {"scale": raw_scale},
            )

        from engine.pipeline.upscaler import is_installed

        if not is_installed():
            raise RpcError(
                "MODEL_MISSING",
                "Real-ESRGAN model is not installed",
                {"hint": "run scripts/fetch-assets.py or build it with packaging/convert-esrgan.py"},
            )

        output_path = build_output_path(input_path, params.get("output_dir"), scale)
        task = manager.create("upscale")
        thread = threading.Thread(
            target=_run_task,
            args=(manager, task, input_path, output_path, scale),
            name=f"upscale-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    server.register("upscale.start", handle_start)
