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


def _run_seedvr_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    output_dir: str | None,
    config: UpscaleConfig,
    model_id: str,
) -> None:
    """Run the SeedVR2 runtime and finish the task with a normal upscale result."""
    import tempfile
    from pathlib import Path as _Path

    from engine.pipeline.seedvr.runtime import SeedVRError, SeedVRRuntime
    from engine.pipeline.upscale_job import _Cancelled, _resize_and_audio
    from engine.pipeline.upscale_plan import resolve_plan
    from engine.pipeline.watermark import VideoProcessor

    try:
        info = VideoProcessor(remover=None).get_video_info(input_path)
        plan = resolve_plan(config, int(info.width), int(info.height))

        def on_progress(payload: dict[str, Any]) -> None:
            _apply_progress(manager, task, payload)

        runtime = SeedVRRuntime()
        with tempfile.TemporaryDirectory(prefix="eraser-seedvr-") as scratch_name:
            scratch = _Path(scratch_name)
            produced = runtime.upscale(
                input_path, scratch,
                model_id=model_id,
                resolution=plan.target_height,
                progress=on_progress,
                cancel=task.cancel,
            )

            base = _Path(input_path)
            directory = _Path(output_dir) if output_dir else base.parent
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{base.stem}_seedvr.mp4"
            _resize_and_audio(
                produced, base, target,
                width=plan.target_width, height=plan.target_height,
                has_audio=bool(info.has_audio), keep_audio=config.keep_audio,
            )

        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        manager.finish(task, result={
            "output_path": str(target),
            "width": plan.target_width,
            "height": plan.target_height,
            "scale": plan.scale,
            "mode": config.mode,
            "engine": "seedvr2",
            "model_id": model_id,
            "scene_split_mode": "off",
            "segments": 1,
            "frames": int(info.frame_count),
        })
    except _Cancelled:
        manager.finish(task, error="Cancelled")
    except SeedVRError as exc:
        manager.finish(task, error=f"UPSCALE_FAILED: {exc}")
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("seedvr upscale failed: %s", exc)
        manager.finish(task, error=str(exc))


def build_capabilities() -> dict[str, Any]:
    """The capability block the UI calls before offering an engine.

    Real-ESRGAN is available when its ONNX file is present; SeedVR2 answers with
    its own runtime state and the reason it cannot run, so the UI can explain
    instead of greying a control out with no explanation.
    """
    from engine.pipeline.seedvr import capability as seedvr_capability
    from engine.pipeline.seedvr import manifest as seedvr_manifest
    from engine.pipeline.upscale_plan import MODES, SAME_RES_STRENGTHS, TARGET_PRESETS
    from engine.pipeline.upscaler import is_installed

    esrgan_ready = is_installed()

    engines: list[dict[str, Any]] = [
        {
            "engine": "realesrgan",
            "display_name": "Real-ESRGAN",
            "available": esrgan_ready,
            "reason": "" if esrgan_ready else "模型未安装",
        }
    ]

    seedvr_state = seedvr_capability.describe()
    # Availability is per model: the runtime can be installed while the chosen
    # weights are not, and the UI has to say which one is missing.
    first_model = seedvr_manifest.DEFAULT_MODEL_ID
    availability = seedvr_capability.check(first_model)
    engines.append(
        {
            "engine": seedvr_manifest.ENGINE_ID,
            "display_name": seedvr_manifest.ENGINE_DISPLAY_NAME,
            "available": availability.available,
            "reason": availability.detail if not availability.available else "",
            "runtime_hint": availability.hint if not availability.available else "",
        }
    )

    models: list[dict[str, Any]] = [
        {
            "engine": "realesrgan",
            "model_id": "realesrgan_general_x4v3",
            "display_name": "Real-ESRGAN x4plus",
            "installed": esrgan_ready,
        }
    ]
    for model_id in seedvr_manifest.UPSCALE_MODELS:
        spec = seedvr_manifest.get_spec(model_id)
        models.append(
            {
                "engine": seedvr_manifest.ENGINE_ID,
                "model_id": model_id,
                "display_name": spec.display_name,
                "installed": seedvr_manifest.model_installed(model_id),
                "size_bytes": spec.size_bytes,
                "min_memory_gb": spec.min_memory_gb,
            }
        )

    return {
        "engines": engines,
        "models": models,
        "modes": list(MODES),
        "target_presets": list(TARGET_PRESETS),
        "same_res_strengths": list(SAME_RES_STRENGTHS),
        "defaults": {
            "engine": "realesrgan",
            "mode": "upscale_resolution",
            "model_id": "realesrgan_general_x4v3",
            "target_preset": "1080p",
            "same_res_strength": "x2_then_downscale",
            "denoise_strength": 0.0,
            "keep_audio": True,
        },
        "seedvr": seedvr_state,
    }


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})

        engine = str(params.get("engine") or "realesrgan")
        if engine not in SUPPORTED_ENGINES:
            raise RpcError("INVALID_PARAMS", f"engine must be one of {list(SUPPORTED_ENGINES)}",
                           {"engine": engine})
        seedvr_model = ""
        if engine == "seedvr2":
            from engine.pipeline.seedvr import capability as seedvr_capability
            from engine.pipeline.seedvr import manifest as seedvr_manifest

            seedvr_model = str(params.get("model_id") or seedvr_manifest.DEFAULT_MODEL_ID)
            availability = seedvr_capability.check(seedvr_model)
            if not availability.available:
                # The UI shows this text; "not implemented" would send the user
                # looking for a setting instead of an install button.
                raise RpcError(
                    "MODEL_MISSING",
                    (
                        f"SeedVR2 不可用：{availability.detail}"
                        if availability.detail
                        else "SeedVR2 不可用"
                    ),
                    {
                        "engine": engine,
                        "reason": availability.reason,
                        "hint": availability.hint,
                        "model_id": seedvr_model,
                    },
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
        target = _run_seedvr_task if engine == "seedvr2" else _run_task
        args = (
            (manager, task, input_path, output_dir, config, seedvr_model)
            if engine == "seedvr2"
            else (manager, task, input_path, output_dir, config, scale_override)
        )
        thread = threading.Thread(
            target=target,
            args=args,
            name=f"upscale-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    server.register("upscale.start", handle_start)
    server.register("upscale.capabilities", lambda _params: build_capabilities())
