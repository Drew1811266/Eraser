"""Minimal settings shim for the ported pipeline.

The reference ``VideoProcessor`` reads a handful of fields from a global config
object. Rather than dragging the old singleton + JSON file logic along, the
pipeline receives this plain object; the RPC layer fills it from request params.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemovalSettings:
    use_gpu: bool = True
    use_fp16: bool = False
    batch_size: int = 4
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass
class OutputSettings:
    format: str = "MP4"
    model_id: str = "lama_roi"
    output_path: str = ""
    filename_suffix: str = "_no_watermark"
    overwrite: bool = False


@dataclass
class AppSettings:
    language: str = "zh"
    theme: str = "light"
    removal: RemovalSettings = field(default_factory=RemovalSettings)
    output: OutputSettings = field(default_factory=OutputSettings)


_current = AppSettings()


def get_config() -> AppSettings:
    return _current


def configure(**overrides: object) -> AppSettings:
    """Replace the process-wide settings. Used by the RPC layer per request."""
    global _current
    for key, value in overrides.items():
        if hasattr(_current, key):
            setattr(_current, key, value)
    return _current
