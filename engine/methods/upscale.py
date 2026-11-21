"""upscale.* handlers.

Upscaling is a task like processing: it returns a task id immediately, streams
progress, and is inspected and cancelled through `process.status` /
`process.cancel`. Reusing that lifecycle instead of inventing a parallel one is
deliberate — the shell already knows how to render a task, and a second set of
status/cancel methods would only give it a second way to be wrong.

What the task *does* comes from `engine.pipeline.upscale_plan`: this module only
validates the request, decides whether the model can run at all, and reports.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.pipeline.upscale_plan import UpscaleConfig, build_segments, detect_scene_cuts
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord

SUPPORTED_SCALES = (2, 4)

#: Everything the reference UI can ask for; `seedvr2` is reported separately
#: because its runtime is a different kind of dependency (see model.list).
SUPPORTED_ENGINES = ("realesrgan", "seedvr2")


def _apply_progress(manager: TaskManager, task: TaskRecord, payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    fields: dict[str, Any] = {}
    for key in ("progress", "processed", "total", "segment_index", "segment_total"):
        if key in payload:
            try:
                fields[key] = float(payload[key]) if key == "progress" else int(payload[key])
            except (TypeError, ValueError):
                continue
    for key in ("phase", "message"):
        if payload.get(key):
            fields[key] = str(payload[key])
    if fields:
        manager.update(task, **fields)


def _run_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    output_dir: str | None,
    config: UpscaleConfig,
    scale_override: int | None,
) -> None:
    from engine.pipeline.upscale_job import (
        UpscaleError,
        _Cancelled,
        upscale_video,
        upscale_with_config,
    )
    from engine.pipeline.upscaler import UpscaleModelNotFound

    try:
        manager.update(task, phase="prepare", message="加载超分模型")

        # The simple 2x/4x request keeps its own path: the full config always
        # plans a target size, which is not what "give me 4x" means.
        if scale_override is not None:
            output = build_output_path(input_path, output_dir, scale_override)
            result = upscale_video(
                input_path,
                output,
                scale=scale_override,
                progress=lambda payload: _apply_progress(manager, task, payload),
                cancel=task.cancel,
            )
            if task.cancel.is_set():
                manager.finish(task, error="Cancelled")
                return
            result["scene_split_mode"] = "off"
            manager.finish(task, result=result)
            return

        segments = None
        reported_split = "off"
        if config.scene_split != "off":
            cuts = detect_scene_cuts(input_path)
            from engine.pipeline.watermark import VideoProcessor
            from engine.pipeline.upscale_plan import HYBRID_MAX_SECONDS

            info = VideoProcessor(remover=None).get_video_info(input_path)
            duration = float(getattr(info, "duration", 0.0) or 0.0)
            if duration > 0:
                max_length = HYBRID_MAX_SECONDS if config.scene_split == "hybrid" else None
                segments = build_segments(duration, cuts, max_length=max_length)
                if len(segments) <= 1:
                    # One piece is not a split; say so instead of claiming the
                    # mode ran when it had nothing to do.
                    reported_split = "fallback"
                elif len(segments) > len(cuts) + 1:
                    reported_split = "hybrid"
                else:
                    reported_split = "rule"
                logger.info("scene split: %d segment(s), mode=%s", len(segments), reported_split)

        result = upscale_with_config(
            input_path,
            None,
            config,
            segments=segments,
            progress=lambda payload: _apply_progress(manager, task, payload),
            cancel=task.cancel,
            output_dir=output_dir,
        )
        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        result["scene_split_mode"] = reported_split
        manager.finish(task, result=result)
    except (UpscaleModelNotFound, UpscaleError) as exc:
        manager.finish(task, error=f"UPSCALE_FAILED: {exc}")
    except _Cancelled:
        manager.finish(task, error="Cancelled")
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("upscale task failed: %s", exc)
        manager.finish(task, error=str(exc))


def build_output_path(input_path: str, output_dir: str | None, scale: int) -> Path:
    from engine.platform.paths import default_output_dir, ensure_dir

    source = Path(input_path)
    directory = Path(output_dir) if output_dir else default_output_dir()
    ensure_dir(directory)
    return directory / f"{source.stem}_x{scale}.mp4"


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})

        engine = str(params.get("engine") or "realesrgan")
        if engine not in SUPPORTED_ENGINES:
            raise RpcError("INVALID_PARAMS", f"engine must be one of {list(SUPPORTED_ENGINES)}",
                           {"engine": engine})
        if engine == "seedvr2":
            raise RpcError(
                "NOT_IMPLEMENTED",
                "SeedVR2 runtime is not available in this build",
                {"engine": engine, "hint": "use the realesrgan engine"},
            )

        # `scale` stays supported for the simple 2x/4x path; a full config means
        # the plan decides the size instead.
        scale_override: int | None = None
        if "scale" in params and params["scale"] is not None and "mode" not in params:
            try:
                scale_override = int(params["scale"])
            except (TypeError, ValueError):
                scale_override = 0
            if scale_override not in SUPPORTED_SCALES:
                raise RpcError("INVALID_PARAMS",
                               f"scale must be one of {list(SUPPORTED_SCALES)}",
                               {"scale": params["scale"]})

        try:
            config = UpscaleConfig.from_params(params)
        except ValueError as exc:
            raise RpcError("INVALID_PARAMS", str(exc)) from exc

        from engine.pipeline.upscaler import is_installed

        if not is_installed():
            raise RpcError(
                "MODEL_MISSING",
                "Real-ESRGAN model is not installed",
                {"hint": "run scripts/fetch-assets.py or build it with packaging/convert-esrgan.py"},
            )

        output_dir = params.get("output_dir")
        task = manager.create("upscale")
        thread = threading.Thread(
            target=_run_task,
            args=(manager, task, input_path, output_dir, config, scale_override),
            name=f"upscale-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    server.register("upscale.start", handle_start)
