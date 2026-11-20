"""Video processing pipeline.

``watermark.py`` and ``model_registry.py`` are mechanically ported from the
reference implementation (see ``scripts/port_reference.py``). Only the import
roots were rewritten so that they resolve inside this package; no algorithmic
change was made during the port, which is what keeps the behaviour comparable.
"""

from engine.pipeline.remover import ModelNotFound, WatermarkRemover, resolve_model_path

__all__ = ["ModelNotFound", "WatermarkRemover", "resolve_model_path"]
