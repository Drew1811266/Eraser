#!/usr/bin/env python3
"""Mechanically port reference modules into ``engine/pipeline/``.

This script exists instead of hand edits so the transformation is auditable and
re-runnable: every change it makes is an import-root rewrite, nothing else. Any
behavioural change must be made deliberately afterwards, where it is visible in
a diff.

    python scripts/port_reference.py           # port
    python scripts/port_reference.py --check   # verify ported copies are current

Source of truth: the reference checkout mirrored under ``.reference/up/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / ".reference" / "up" / "src" / "core"
TARGET_DIR = REPO_ROOT / "engine" / "pipeline"

BANNER = (
    '"""PORTED FROM Mac-Remover src/core/{source}.\n\n'
    "Do not edit the porting mechanics here by hand: rerun scripts/port_reference.py.\n"
    '"""\n'
)

# (source file, target file, [(needle, replacement), ...])
MODULES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "video_processor.py",
        "watermark.py",
        [
            ("from ..config import get_config", "from engine.pipeline.settings import get_config"),
            (
                "from ..utils.ffmpeg_runtime import resolve_ffmpeg_path, resolve_ffprobe_path",
                "from engine.pipeline.compat import resolve_ffmpeg_path, resolve_ffprobe_path",
            ),
            ("from ..utils.logger import logger", "from engine.pipeline.logging import logger"),
            (
                "from .model_registry import ModelRegistry, SUPPORTED_MODEL_IDS",
                "from engine.pipeline.model_registry import ModelRegistry, SUPPORTED_MODEL_IDS",
            ),
            ("from .remover import WatermarkRemover", "from engine.pipeline.remover import WatermarkRemover"),
        ],
    ),
    (
        "model_registry.py",
        "model_registry.py",
        [
            ("from ..utils.logger import logger", "from engine.pipeline.logging import logger"),
            ("from .remover import WatermarkRemover", "from engine.pipeline.remover import WatermarkRemover"),
        ],
    ),
]


def transform(source_name: str, text: str, rewrites: list[tuple[str, str]]) -> str:
    if text.startswith('"""'):
        end = text.find('"""', 3)
        if end != -1:
            text = text[end + 3 :].lstrip("\n")

    for needle, replacement in rewrites:
        text = text.replace(needle, replacement)

    return BANNER.format(source=source_name) + "\n" + text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Port reference modules.")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if not SOURCE_DIR.is_dir():
        print(f"reference checkout missing: {SOURCE_DIR}", file=sys.stderr)
        return 2

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0

    for source_name, target_name, rewrites in MODULES:
        source_path = SOURCE_DIR / source_name
        target_path = TARGET_DIR / target_name
        if not source_path.is_file():
            print(f"missing reference module: {source_path}", file=sys.stderr)
            failures += 1
            continue

        original = source_path.read_text(encoding="utf-8")
        ported = transform(source_name, original, rewrites)

        if args.check:
            existing = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
            if existing != ported:
                print(f"out of date: {target_path}", file=sys.stderr)
                failures += 1
            continue

        target_path.write_text(ported, encoding="utf-8")
        line_count = ported.count("\n") + 1
        print(f"ported {source_name} -> {target_path.relative_to(REPO_ROOT)} ({line_count} lines)")

    if args.check and failures:
        print("run: python scripts/port_reference.py", file=sys.stderr)
        return 1
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
