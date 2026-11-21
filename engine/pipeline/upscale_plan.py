"""Upscale planning: what to run, how big, and in how many pieces.

The reference app exposes more than "2x or 4x": a target resolution, a
same-resolution enhancement mode, a denoise strength, audio retention and scene
splitting. This module turns that configuration into a concrete plan — which
scale to run, what the output resolution should be, and where the scene cuts
are — so the job itself stays a loop over a list of segments.

Keeping it separate matters for testing: the arithmetic (what does "1080p" mean
for a 360p source, when is a 1.5x request rounded up) is the part that silently
goes wrong, and it can be checked without touching a single frame.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.pipeline.logging import logger
from engine.platform.ffmpeg import resolve

MODES = ("upscale_resolution", "enhance_same_resolution")
TARGET_PRESETS = {"1080p": 1080, "1440p": 1440, "4k": 2160}
SAME_RES_STRENGTHS = ("x2_then_downscale",)
SCENE_SPLIT_MODES = ("off", "rule", "hybrid")

# The model is natively 4x. Anything else is produced by resampling its output,
# which is both cheaper and better than asking it for a fake scale.
NATIVE_SCALE = 4


class ConfigError(ValueError):
    """The caller asked for something the engine cannot do."""


@dataclass(frozen=True)
class UpscaleConfig:
    mode: str = "upscale_resolution"
    engine: str = "realesrgan"
    model_id: str = "realesrgan_general_x4v3"
    target_preset: str = "1080p"
    same_res_strength: str = "x2_then_downscale"
    denoise_strength: float = 0.0
    keep_audio: bool = True
    scene_split: str = "rule"

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "UpscaleConfig":
        mode = str(params.get("mode") or "upscale_resolution")
        if mode not in MODES:
            raise ConfigError(f"mode must be one of {list(MODES)}")

        preset = str(params.get("target_preset") or "1080p").lower()
        if preset not in TARGET_PRESETS:
            raise ConfigError(f"target_preset must be one of {sorted(TARGET_PRESETS)}")

        strength = str(params.get("same_res_strength") or "x2_then_downscale")
        if strength not in SAME_RES_STRENGTHS:
            raise ConfigError(f"same_res_strength must be one of {list(SAME_RES_STRENGTHS)}")

        scene_split = str(params.get("scene_split") or "rule").lower()
        if scene_split not in SCENE_SPLIT_MODES:
            raise ConfigError(f"scene_split must be one of {list(SCENE_SPLIT_MODES)}")

        raw_denoise = params.get("denoise_strength", 0.0)
        try:
            denoise = float(raw_denoise)
        except (TypeError, ValueError) as exc:
            raise ConfigError("denoise_strength must be a number") from exc
        if not 0.0 <= denoise <= 1.0:
            raise ConfigError("denoise_strength must be between 0 and 1")

        return cls(
            mode=mode,
            engine=str(params.get("engine") or "realesrgan"),
            model_id=str(params.get("model_id") or "realesrgan_general_x4v3"),
            target_preset=preset,
            same_res_strength=strength,
            # A denoise that does nothing is the same as not asking for it.
            denoise_strength=0.0 if denoise < 0.01 else denoise,
            keep_audio=params.get("keep_audio", True) is not False,
            scene_split=scene_split,
        )


@dataclass(frozen=True)
class UpscalePlan:
    """The resolved plan for one video."""

    config: UpscaleConfig
    source_width: int
    source_height: int
    scale: int
    target_width: int
    target_height: int
    #: True when the result is resampled after upscaling (2x, or a preset).
    downscale: bool
    #: True when the output keeps the source resolution by design.
    same_resolution: bool

    def describe(self) -> str:
        if self.same_resolution:
            return f"{self.scale}× 后回缩到 {self.target_width}×{self.target_height}"
        return f"放大到 {self.target_width}×{self.target_height}"


def resolve_plan(config: UpscaleConfig, width: int, height: int) -> UpscalePlan:
    """Decide the model scale and the final resolution."""
    if width <= 0 or height <= 0:
        raise ConfigError("cannot plan an upscale for an empty frame")

    if config.mode == "enhance_same_resolution":
        # Upscale, then come back down: the reference calls this
        # `x2_then_downscale`, and keeping the ratio explicit means the value is
        # meaningful even though the model runs at 4x.
        return UpscalePlan(
            config=config,
            source_width=width,
            source_height=height,
            scale=NATIVE_SCALE,
            target_width=width,
            target_height=height,
            downscale=True,
            same_resolution=True,
        )

    target_height = TARGET_PRESETS[config.target_preset]
    if height >= target_height:
        # Already at or above the preset: enhance without changing the size
        # rather than producing something the user could not have wanted.
        return UpscalePlan(
            config=config,
            source_width=width,
            source_height=height,
            scale=NATIVE_SCALE,
            target_width=width,
            target_height=height,
            downscale=True,
            same_resolution=True,
        )

    width_out = _scaled(width, target_height, height)
    return UpscalePlan(
        config=config,
        source_width=width,
        source_height=height,
        scale=NATIVE_SCALE,
        target_width=width_out,
        target_height=target_height,
        downscale=True,
        same_resolution=False,
    )


def _scaled(width: int, target_height: int, source_height: int) -> int:
    """Width that keeps the aspect ratio at *target_height*, rounded to even."""
    exact = width * (target_height / source_height)
    return max(2, int(round(exact / 2.0)) * 2)


def detect_scene_cuts(path: str | Path, threshold: float = 0.32) -> list[float]:
    """Timestamps of hard cuts, in seconds.

    ffmpeg's `scene` score is a per-frame difference; the threshold is the usual
    starting point for "a new shot begins here". A failed detection is not an
    error: the caller falls back to a single segment.
    """
    found = resolve("ffmpeg")
    if not found.path:
        return []

    command = [
        found.path, "-v", "error", "-i", str(path),
        "-vf", f"select='gt(scene,{threshold})',metadata=print:file=-",
        "-an", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=300,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("scene detection failed: %s", exc)
        return []

    cuts: list[float] = []
    for line in completed.stdout.decode("utf-8", "replace").splitlines():
        if "pts_time:" not in line:
            continue
        try:
            value = float(line.split("pts_time:")[1].split()[0])
        except (IndexError, ValueError):
            continue
        if value > 0.05:
            cuts.append(round(value, 3))
    return sorted(set(cuts))


# These three come from the reference implementation's scene splitter, not from
# taste. Matching them matters: segment length drives peak memory and how much
# work a cancelled job loses, and a port that splits a 30s shot into one piece
# where the original makes eight is not the same product.
MIN_SCENE_SECONDS = 1.2
SECONDARY_MERGE_SECONDS = 2.0
MAX_SCENE_SECONDS = 4.0
#: The reference only bothers splitting shots on clips long enough to have any.
SPLIT_LONG_MIN_DURATION = 8.0

#: Longest single segment allowed in hybrid mode.
HYBRID_MAX_SECONDS = MAX_SCENE_SECONDS


def _merge_short(segments: list[tuple[float, float]], minimum: float) -> list[tuple[float, float]]:
    """Fold segments shorter than `minimum` into their shorter neighbour.

    Straight port of the reference's `_merge_short_segments`: a flash frame
    between two shots becomes part of whichever side is shorter, because a
    4-frame segment is not a shot and the model cannot see enough of it to do
    anything sensible anyway.
    """
    merged = [list(item) for item in segments]
    index = 0
    while index < len(merged):
        start, end = merged[index]
        if (end - start) >= minimum or len(merged) == 1:
            index += 1
            continue

        if index == 0:
            merged[1][0] = merged[0][0]
            del merged[0]
            continue
        if index == len(merged) - 1:
            merged[index - 1][1] = merged[index][1]
            del merged[index]
            index = max(0, index - 1)
            continue

        left = merged[index - 1][1] - merged[index - 1][0]
        right = merged[index + 1][1] - merged[index + 1][0]
        if left <= right:
            merged[index - 1][1] = merged[index][1]
        else:
            merged[index + 1][0] = merged[index][0]
        del merged[index]
        index = max(0, index - 1)
    return [(round(start, 3), round(end, 3)) for start, end in merged]


def _split_long(segments: list[tuple[float, float]], maximum: float) -> list[tuple[float, float]]:
    """Cut any segment longer than `maximum` into equal pieces."""
    pieces: list[tuple[float, float]] = []
    for start, end in segments:
        span = end - start
        if span <= maximum:
            pieces.append((start, end))
            continue
        count = int(max(2, -(-span // maximum)))
        step = span / count
        for index in range(count):
            piece_start = start + index * step
            piece_end = end if index == count - 1 else piece_start + step
            pieces.append((round(piece_start, 3), round(piece_end, 3)))
    return pieces


def build_segments(
    duration: float,
    cuts: list[float],
    *,
    max_length: float | None = None,
    merge_short: bool = True,
) -> list[tuple[float, float]]:
    """Turn cut timestamps into `(start, length)` pairs covering the whole video.

    The order matters and follows the reference: cut, drop degenerate pieces,
    merge what is too short to be a shot, and only then split what is too long.
    Doing the split first would produce pieces that the merge then recombines in
    a different place, which is how two implementations end up with the same
    cut list and different segments.
    """
    if duration <= 0:
        raise ConfigError("cannot split a video with no duration")

    boundaries = [0.0]
    for cut in cuts:
        if 0.0 < cut < duration and cut - boundaries[-1] > 0.2:
            boundaries.append(cut)
    boundaries.append(duration)

    segments = [
        (round(boundaries[index], 3), round(boundaries[index + 1], 3))
        for index in range(len(boundaries) - 1)
        if boundaries[index + 1] - boundaries[index] > 0
    ]

    if merge_short:
        # Two passes, like the reference: 1.2s first (flash frames), then 2.0s
        # (shots too short to be worth their own inference pass).
        segments = _merge_short(segments, MIN_SCENE_SECONDS)
        segments = _merge_short(segments, SECONDARY_MERGE_SECONDS)

    if max_length and max_length > 0:
        segments = _split_long(segments, max_length)

    # Public shape is (start, length): it is what the job passes to ffmpeg's
    # -ss/-t, and a caller that mistakes it for an end time cuts the wrong window.
    return [(start, round(end - start, 3)) for start, end in segments]


def plan_output_name(source: Path, plan: UpscalePlan) -> str:
    """A name that says what the file is, not just that it was upscaled."""
    if plan.same_resolution:
        return f"{source.stem}_enhanced.mp4"
    if plan.config.mode == "upscale_resolution":
        preset = plan.config.target_preset
        return f"{source.stem}_{preset}.mp4"
    return f"{source.stem}_x{plan.scale}.mp4"
