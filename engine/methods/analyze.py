"""analyze.* handlers: capabilities and start.

Detection is injected, and in this milestone the only registered detector is the
scripted one (`ERASER_DETECT_STUB`), so `analyze.capabilities` honestly reports
"nothing available" on a stock install rather than pretending.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engine.pipeline.detect import available_detectors
from engine.pipeline.logging import logger
from engine.rpc.errors import RpcError
from engine.rpc.server import RpcServer
from engine.tasks import TaskManager, TaskRecord


def _detector_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for detector in available_detectors():
        available = False
        reason = ""
        try:
            available = bool(detector.is_available())
            reason = detector.get_warning() if not available else ""
        except Exception as exc:  # noqa: BLE001 - a probe must not break the list
            reason = f"{type(exc).__name__}: {exc}"
        entries.append(
            {
                "model_id": detector.model_id,
                "display_name": detector.display_name,
                "installed": True,
                "available": available,
                "blocked_reason": reason,
            }
        )
    return entries


def _pick_detector(model_id: str | None) -> Any:
    detectors = available_detectors()
    if not detectors:
        raise RpcError("DETECTOR_MISSING", "No watermark detector is installed", {})
    if model_id:
        for detector in detectors:
            if detector.model_id == model_id:
                return detector
        raise RpcError(
            "DETECTOR_MISSING", f"Unknown detector: {model_id}", {"model_id": model_id}
        )
    return detectors[0]


def _run_task(
    manager: TaskManager,
    task: TaskRecord,
    input_path: str,
    detector: Any,
    sample_step: int,
    max_tracks: int,
    min_confidence: float,
) -> None:
    from engine.pipeline.analyze_job import AnalysisError, run_analysis

    try:
        manager.update(task, phase="analyze", message="Analysing video")

        def on_progress(payload: dict[str, Any]) -> None:
            fields = {
                key: value
                for key, value in payload.items()
                if key in {"phase", "progress", "message"}
            }
            if fields:
                manager.update(task, **fields)

        analysis = run_analysis(
            input_path,
            detector,
            sample_step=sample_step,
            max_tracks=max_tracks,
            min_confidence=min_confidence,
            progress=on_progress,
            cancel=task.cancel,
        )
        if task.cancel.is_set():
            manager.finish(task, error="Cancelled")
            return
        manager.finish(task, result=analysis.as_dict())
    except AnalysisError as exc:
        manager.finish(task, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - task boundary
        logger.error("analysis task failed: %s", exc)
        manager.finish(task, error=str(exc))


def register(server: RpcServer, manager: TaskManager) -> None:
    def handle_capabilities(_params: dict[str, Any]) -> dict[str, Any]:
        detectors = _detector_entries()
        return {
            "detectors": detectors,
            "available": any(item["available"] for item in detectors),
        }

    def handle_start(params: dict[str, Any]) -> dict[str, Any]:
        input_path = str(params.get("input_path", ""))
        if not Path(input_path).is_file():
            raise RpcError("INVALID_PARAMS", f"File not found: {input_path}", {"path": input_path})

        detector = _pick_detector(None)
        task = manager.create("analyze")
        thread = threading.Thread(
            target=_run_task,
            args=(
                manager,
                task,
                input_path,
                detector,
                int(params.get("sample_step") or 15),
                int(params.get("max_tracks") or 8),
                float(params.get("min_confidence") or 0.35),
            ),
            name=f"analyze-{task.task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return {"task_id": task.task_id}

    server.register("analyze.capabilities", handle_capabilities)
    server.register("analyze.start", handle_start)
