"""Adapters between ported code and the new platform layer.

The ported pipeline was written against ``src/utils/ffmpeg_runtime``. Rather
than rewriting every call site, it imports these thin wrappers, which delegate
to :mod:`engine.platform.ffmpeg`.

The originals returned ``Optional[str]`` and silently returned ``None`` when
ffmpeg was missing. Here a missing tool is reported through the same ``None``
so ported code paths keep working, but the reason is logged once so a failed
run is diagnosable.
"""

from __future__ import annotations

from engine.pipeline.logging import logger
from engine.platform import ffmpeg as ffmpeg_tools

_warned = False


def _resolve(tool: str) -> str | None:
    global _warned
    resolution = ffmpeg_tools.resolve(tool)
    if resolution.available:
        return resolution.path

    if not _warned:
        searched = "\n  ".join(ffmpeg_tools.describe_search_paths())
        logger.error("%s not found. Searched:\n  %s", tool, searched)
        _warned = True
    return None


def resolve_ffmpeg_path() -> str | None:
    return _resolve(ffmpeg_tools.FFMPEG)


def resolve_ffprobe_path() -> str | None:
    return _resolve(ffmpeg_tools.FFPROBE)


def ffmpeg_version() -> str:
    resolution = ffmpeg_tools.resolve(ffmpeg_tools.FFMPEG)
    return ffmpeg_tools.version(resolution) if resolution.available else ""
