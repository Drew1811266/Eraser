"""One processing job, independent of who asked for it.

The RPC handler and the CLI both need to do exactly the same thing — build a
processor, run it, normalise the result — so that logic lives here instead of
being duplicated (and slowly diverging) in two places.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from engine.pipeline.logging import logger

ProgressCallback = Callable[[Any], None]
StatusCallback = Callable[[str], None]
ProcessorCallback = Callable[[Any], None]

DEFAULT_MODEL_ID = "lama_roi"


def _progress_payload(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Normalise whatever the pipeline passes into one dict.

    `VideoProcessor._emit_progress` probes callback signatures by trying six
    positional arguments first — `(progress, message, processed, total, eta,
    extra)` — and falls back to `(progress, message)`.  Accepting `*args` means
    the six-argument form always wins, so the richer payload is available; the
    old two-argument form still works for anyone calling `process_video`
    directly.
    """
    if not args:
        return dict(kwargs)

    payload: dict[str, Any] = {}
    head = args[0]
    if isinstance(head, dict):
        payload.update(head)
    elif isinstance(head, (int, float)) and not isinstance(head, bool):
        payload["progress"] = float(head)

    if len(args) > 1 and isinstance(args[1], str) and args[1].strip():
        payload["message"] = args[1].strip()

    for index, key in ((2, "processed"), (3, "total")):
        if len(args) > index and args[index] is not None:
            try:
                payload[key] = int(args[index])
            except (TypeError, ValueError):
                pass

    if len(args) > 5 and isinstance(args[5], dict):
        for key, value in args[5].items():
            payload.setdefault(key, value)

    return payload


def output_path_for(input_path: str | Path, output_dir: str | Path | None) -> Path:
    """<stem>_no_watermark.mp4 inside `output_dir` (or the default one)."""
    from engine.platform.paths import default_output_dir, ensure_dir

    source = Path(input_path)
    directory = Path(output_dir) if output_dir else default_output_dir()
    ensure_dir(directory)
    return directory / f"{source.stem}_no_watermark.mp4"


def run_job(
    input_path: str | Path,
    output_path: str | Path,
    segments: list[dict[str, Any]],
    model_id: str | None = None,
    *,
    progress: ProgressCallback | None = None,
    status: StatusCallback | None = None,
    on_processor: ProcessorCallback | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Run the pipeline to completion and return a normalised result.

    `on_processor` receives the live `VideoProcessor`, which is the only handle
    that can stop a run mid-flight: the Windows port has no signals to interrupt
    the worker thread with, so cancellation is cooperative.
    """
    from engine.pipeline.remover import WatermarkRemover
    from engine.pipeline.watermark import VideoProcessor

    chosen_model = str(model_id or DEFAULT_MODEL_ID)
    processor = VideoProcessor(remover=WatermarkRemover())
    if on_processor is not None:
        on_processor(processor)

    if status is not None:
        status("Loading models")

    progress_callback = None
    if progress is not None:

        def progress_callback(*args: Any, **kwargs: Any) -> None:
            try:
                payload = _progress_payload(args, kwargs)
            except Exception as exc:  # noqa: BLE001 - never break a run over a progress event
                logger.debug("dropping malformed progress event: %s", exc)
                return
            if payload:
                progress(payload)

    raw = processor.process_video(
        str(input_path),
        str(output_path),
        segments,
        chosen_model,
        progress_callback=progress_callback,
        status_callback=(lambda message: status(str(message))) if status else None,
    )

    if cancel is not None and cancel.is_set():
        processor.stop_processing()

    payload = raw if isinstance(raw, dict) else {}
    result = {
        "output_path": str(payload.get("output_path") or payload.get("output") or output_path),
        "requested_model_id": payload.get("requested_model_id", chosen_model),
        "effective_model_id": payload.get("effective_model_id", chosen_model),
        "model_warning": payload.get("model_warning", ""),
    }
    logger.info("job finished: %s", result["output_path"])
    return result
