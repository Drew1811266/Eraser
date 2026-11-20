"""Delete a named set of never-called definitions from a Python module.

Line-range based: for each requested name the decorators, signature and body
are removed together, from the bottom of the file upwards so earlier line
numbers stay valid.  A trailing blank-line collapse keeps the diff tidy.

Usage::

    python scripts/prune_dead.py engine/pipeline/watermark.py \
        _resolve_active_annotation_segment extract_frames ...
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def spans(tree: ast.Module) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        candidates: list[ast.AST] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.append(node)
        elif isinstance(node, ast.ClassDef):
            candidates.extend(
                item
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
        for node_ in candidates:
            body = getattr(node_, "decorator_list", [])
            start = body[0].lineno if body else node_.lineno
            end = getattr(node_, "end_lineno", None)
            if end is None:
                raise SystemExit(f"no end_lineno for {node_.name}")
            out.setdefault(node_.name, (start, end))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("names", nargs="+")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    index = spans(ast.parse(text, filename=str(path)))

    missing = [n for n in args.names if n not in index]
    if missing:
        print(f"not found: {missing}")
        return 2

    targets = sorted((index[n] for n in args.names), reverse=True)
    removed = 0
    for start, end in targets:
        print(f"removing {start}-{end} ({end - start + 1} lines)")
        removed += end - start + 1
        # also eat one blank separator line after the block
        stop = end
        while stop < len(lines) and lines[stop].strip() == "":
            stop += 1
            if stop - end >= 2:
                break
        del lines[start - 1 : stop]

    result = "".join(lines)
    ast.parse(result)  # fail fast if the cut broke syntax
    print(f"removed {removed} lines -> {len(result.splitlines())} lines remain")
    if args.write:
        path.write_text(result, encoding="utf-8")
        print(f"wrote {path}")
    else:
        print("(dry run, pass --write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
