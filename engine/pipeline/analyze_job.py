"""One analysis run, independent of who asked for it.

Mirrors `engine/runner.py`: the RPC handler and the CLI must do exactly the same
thing, so the orchestration lives here rather than being duplicated (and slowly
diverging) in two places.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from engine.pipeline.analysis import (
    Analysis,
    Detection,
    associate,
    build_shots,
    suggest_strategy,
)
from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.frames import FrameSamplingError, sample_frames
from engine.pipeline.logging import logger
from engine.pipeline.upscale_plan import detect_scene_cuts

ProgressCallback = Callable[[dict[str, Any]], None]

DEFAULT_SAMPLE_STEP = 15
DEFAULT_MAX_TRACKS = 8
DEFAULT_MIN_CONFIDENCE = 0.35
ANALYSIS_MAX_WIDTH = 1280


class AnalysisError(RuntimeError):
    """The clip could not be analysed."""


def run_analysis(
    input_path: str,
    detector: WatermarkDetector | None,
    *,
    sample_step: int = DEFAULT_SAMPLE_STEP,
    max_tracks: int = DEFAULT_MAX_TRACKS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    progress: ProgressCallback | None = None,
    cancel: Any = None,
    engines: list[str] | None = None,
) -> Analysis:
    from engine.pipeline.watermark import VideoProcessor

    if detector is None:
        raise AnalysisError("no watermark detector is available")

    started = time.monotonic()

    def emit(**payload: Any) -> None:
        if progress is not None:
            try:
                progress(payload)
            except Exception as exc:  # noqa: BLE001 - progress must never break a run
                logger.debug("dropping analysis progress event: %s", exc)

    emit(phase="probe", progress=0.02, message="Reading video")
    info = VideoProcessor(remover=None).get_video_info(input_path)
    frame_count = int(info.frame_count)
    if frame_count <= 0:
        raise AnalysisError("the clip reports no frames")

    emit(phase="shots", progress=0.08, message="Finding shot boundaries")
    cuts = detect_scene_cuts(input_path)
    shots = build_shots(cuts, fps=float(info.fps), frame_count=frame_count)
    emit(phase="shots", progress=0.15, message=f"{len(shots)} shot(s)")

    if cancel is not None and cancel.is_set():
        raise AnalysisError("cancelled")

    emit(phase="sample", progress=0.2, message="Sampling frames")
    try:
        frames = sample_frames(
            input_path,
            step=max(2, int(sample_step)),
            source_width=int(info.width),
            max_width=ANALYSIS_MAX_WIDTH,
        )
    except FrameSamplingError as exc:
        raise AnalysisError(f"cannot sample frames: {exc}") from exc

    detector.load()
    detections: dict[int, list[Detection]] = {}
    total = max(1, len(frames))
    for position, (frame_index, image) in enumerate(frames, start=1):
        if cancel is not None and cancel.is_set():
            raise AnalysisError("cancelled")
        detections[frame_index] = detector.detect(image, frame_index)
        if position == total or position % 5 == 0:
            emit(
                phase="detect",
                progress=0.2 + 0.6 * (position / total),
                message=f"Detecting {position}/{total}",
            )

    emit(phase="associate", progress=0.85, message="Grouping detections")
    tracks = associate(
        detections,
        frame_count=frame_count,
        frame_width=int(info.width),
        frame_height=int(info.height),
        fps=float(info.fps),
        max_tracks=max(1, int(max_tracks)),
        min_confidence=float(min_confidence),
    )

    suggested = suggest_strategy(shots, tracks, engines=engines)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "analysis: %d frame(s) sampled, %d shot(s), %d track(s) in %d ms",
        len(frames),
        len(shots),
        len(tracks),
        elapsed_ms,
    )
    emit(phase="finalize", progress=0.99, message="Analysis complete")
    return Analysis(
        video={
            "width": int(info.width),
            "height": int(info.height),
            "fps": float(info.fps),
            "frame_count": frame_count,
            "duration_seconds": float(info.duration),
        },
        shots=shots,
        watermarks=tracks,
        suggested=suggested,
        model_id=detector.model_id,
        elapsed_ms=elapsed_ms,
    )
