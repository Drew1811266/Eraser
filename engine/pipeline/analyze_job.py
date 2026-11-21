"""One analysis run, independent of who asked for it.

Mirrors `engine/runner.py`: the RPC handler and the CLI must do exactly the same
thing, so the orchestration lives here rather than being duplicated (and slowly
diverging) in two places.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from engine.pipeline.analysis import (
    Analysis,
    Detection,
    associate,
    build_shots,
    rescale_detections,
    suggest_strategy,
)
from engine.pipeline.detect.base import WatermarkDetector
from engine.pipeline.frames import MAX_SAMPLES, FrameSamplingError, sample_frames
from engine.pipeline.logging import logger
from engine.pipeline.upscale_plan import detect_scene_cuts

ProgressCallback = Callable[[dict[str, Any]], None]

DEFAULT_SAMPLE_STEP = 15
DEFAULT_MAX_TRACKS = 8
DEFAULT_MIN_CONFIDENCE = 0.35
ANALYSIS_MAX_WIDTH = 1280
#: Cut frames are extra seeks on top of the uniform grid; a generous ceiling
#: keeps a pathological cut list from turning into thousands of subprocesses.
MAX_EXTRA_FRAMES = 64


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
    try:
        info = VideoProcessor(remover=None).get_video_info(input_path)
    except Exception as exc:  # noqa: BLE001 - the decoder's errors are not ours
        raise AnalysisError(f"cannot read video: {exc}") from exc
    frame_count = int(info.frame_count)
    if frame_count <= 0:
        raise AnalysisError("the clip reports no frames")

    emit(phase="shots", progress=0.08, message="Finding shot boundaries")
    cuts = detect_scene_cuts(input_path)
    fps = float(info.fps)
    shots = build_shots(cuts, fps=fps, frame_count=frame_count)
    # A short shot can fall entirely between two grid samples; the frames a cut
    # lands on are extracted on top of the grid for exactly that reason.
    cut_frames = [int(round(cut * fps)) for cut in cuts][:MAX_EXTRA_FRAMES]
    emit(phase="shots", progress=0.15, message=f"{len(shots)} shot(s)")

    if cancel is not None and cancel.is_set():
        raise AnalysisError("cancelled")

    emit(phase="sample", progress=0.2, message="Sampling frames")
    # Raise the step instead of refusing the clip: a 4-minute video would
    # otherwise hold gigabytes of decoded frames at once.
    effective_step = max(2, int(sample_step), math.ceil(frame_count / MAX_SAMPLES))
    try:
        frames = sample_frames(
            input_path,
            step=effective_step,
            source_width=int(info.width),
            source_height=int(info.height),
            max_width=ANALYSIS_MAX_WIDTH,
            fps=fps,
            extra_frames=cut_frames,
            max_samples=MAX_SAMPLES + MAX_EXTRA_FRAMES,
        )
    except FrameSamplingError as exc:
        raise AnalysisError(f"cannot sample frames: {exc}") from exc

    detector.load()
    detections: dict[int, list[Detection]] = {}
    total = max(1, len(frames))
    for position, (frame_index, image) in enumerate(frames, start=1):
        if cancel is not None and cancel.is_set():
            raise AnalysisError("cancelled")
        raw = detector.detect(image, frame_index)
        # The detector saw the (possibly downscaled) sample, so its boxes are in
        # that image's space until they are mapped back to source pixels.
        height, width = image.shape[:2]
        scale_x = info.width / width if width else 1.0
        scale_y = info.height / height if height else 1.0
        detections[frame_index] = rescale_detections(raw, scale_x, scale_y)
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
        fps=fps,
        shots=shots,
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
