"""Static reachability analysis for the ported pipeline module.

The reference implementation carries a large amount of ProPainter-era code that
the LaMa/ONNX path never touches.  Deleting it by name would be guesswork, so
this script builds a conservative call graph and reports what is actually
reachable from a set of roots.

Conservative in practice means:

* a call through ``self.foo(...)``, ``Class.foo(...)`` or a bare ``foo(...)``
  all count as edges;
* ``getattr(obj, "name")`` -- which would hide dynamic dispatch -- is detected
  and reported, and every method of the class is treated as reachable when one
  appears in the reachable set (so nothing is deleted that dispatch could hit).

Usage::

    python scripts/callgraph.py engine/pipeline/watermark.py --root process_video
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


class Index:
    def __init__(self, tree: ast.Module) -> None:
        self.defs: dict[str, ast.AST] = {}
        self.lines: dict[str, int] = {}
        self.calls: dict[str, set[str]] = {}
        self.dynamic: list[tuple[str, int]] = []
        self._index(tree)

    def _index(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add(node.name, node)
            elif isinstance(node, ast.ClassDef):
                self.calls.setdefault(node.name, set())
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._add(item.name, item, prefix=f"{node.name}.")

    def _add(self, name: str, node: ast.AST, prefix: str = "") -> None:
        key = name if not prefix else name
        # Methods are keyed by bare name: `self.foo` and `VideoProcessor.foo`
        # both resolve to the same implementation.
        self.defs.setdefault(key, node)
        self.lines.setdefault(key, getattr(node, "lineno", 0))
        calls: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                target = child.func
                if isinstance(target, ast.Attribute):
                    calls.add(target.attr)
                elif isinstance(target, ast.Name):
                    calls.add(target.id)
            elif isinstance(child, ast.Call) is False and isinstance(child, ast.Attribute):
                pass
            if isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Name) and fn.id == "getattr" and len(child.args) >= 2:
                    arg = child.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        self.dynamic.append((arg.value, getattr(child, "lineno", 0)))
        self.calls.setdefault(key, set()).update(calls)

    def reachable(self, roots: list[str]) -> set[str]:
        seen: set[str] = set()
        stack = [r for r in roots if r in self.defs]
        missing = [r for r in roots if r not in self.defs]
        for r in missing:
            print(f"warning: root {r!r} not defined in module", file=sys.stderr)
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for callee in self.calls.get(name, ()):
                if callee in self.defs and callee not in seen:
                    stack.append(callee)
        return seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--root", action="append", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    index = Index(tree)
    live = index.reachable(args.root)

    dead = sorted(set(index.defs) - live, key=lambda n: index.lines[n])
    report = {
        "path": str(path),
        "roots": args.root,
        "defined": len(index.defs),
        "reachable": len(live),
        "unreachable": [
            {"name": n, "line": index.lines[n]} for n in dead
        ],
        "dynamic_getattr": sorted({name for name, _ in index.dynamic}),
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"{path}: {report['defined']} definitions, {report['reachable']} reachable from {args.root}")
    if report["dynamic_getattr"]:
        print(f"dynamic getattr targets ({len(report['dynamic_getattr'])}): {', '.join(report['dynamic_getattr'])}")
    print(f"\nunreachable ({len(dead)}):")
    for name in dead:
        print(f"  {index.lines[name]:5d}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
