#!/usr/bin/env python3
"""Generate typed contract artifacts for both ends from the single source.

Inputs : contract/methods.json, contract/errors.json
Outputs: engine/generated/contract.py      (Python: schemas + method registry)
         desktop/electron/generated/contract.ts (TypeScript: types + method maps)

The generated files are committed. `--check` regenerates into memory and fails
if the on-disk copies differ, so CI catches drift and hand edits.

Usage:
    python contract/codegen/generate.py
    python contract/codegen/generate.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = REPO_ROOT / "contract"
PY_OUT = REPO_ROOT / "engine" / "generated" / "contract.py"
TS_OUT = REPO_ROOT / "desktop" / "electron" / "generated" / "contract.ts"

BANNER = "AUTO-GENERATED FROM contract/methods.json + contract/errors.json. DO NOT EDIT."

PRIMITIVE_TS = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "object": "Record<string, unknown>",
}


# --------------------------------------------------------------------------- #
# schema helpers
# --------------------------------------------------------------------------- #
def load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def ref_name(ref: str) -> str:
    """'#/types/Rect' -> 'Rect'"""
    return ref.rsplit("/", 1)[-1]


def pascal(value: str) -> str:
    """'system.ping' -> 'SystemPing'"""
    parts: list[str] = []
    for chunk in value.replace("-", ".").replace("_", ".").split("."):
        if chunk:
            parts.append(chunk[:1].upper() + chunk[1:])
    return "".join(parts)


def ts_type(schema: dict[str, Any]) -> str:
    """Render a JSON-schema subset as a TypeScript type expression."""
    if "$ref" in schema:
        return ref_name(schema["$ref"])

    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        return " | ".join(PRIMITIVE_TS.get(str(item), "unknown") for item in raw_type)

    if raw_type == "array":
        inner = ts_type(schema.get("items", {}))
        return f"({inner})[]" if "|" in inner else f"{inner}[]"

    if raw_type == "object":
        props: dict[str, Any] = schema.get("properties") or {}
        if not props:
            # `Record<string, never>` means "no keys allowed", which is not what
            # a free-form payload is. Task results (process/upscale/analyze) ride
            # through `TaskStatus.result` and the shell must be able to read
            # typed fields out of them.
            if schema.get("additionalProperties", True) is not False:
                return "Record<string, unknown>"
            return "Record<string, never>"
        required = set(schema.get("required") or [])
        lines: list[str] = []
        for name, prop in props.items():
            optional = "" if name in required else "?"
            lines.append(f"{name}{optional}: {ts_type(prop)};")
        return "{ " + " ".join(lines) + " }"

    return PRIMITIVE_TS.get(str(raw_type), "unknown")


def ts_interface(name: str, schema: dict[str, Any], indent: str = "") -> str:
    props: dict[str, Any] = schema.get("properties") or {}
    if not props:
        # An empty `interface` in TS accepts any object; a Record type is tighter.
        return f"{indent}export type {name} = Record<string, never>;"
    required = set(schema.get("required") or [])
    lines = [f"{indent}export interface {name} {{"]
    for prop_name, prop in props.items():
        optional = "" if prop_name in required else "?"
        lines.append(f"{indent}  {prop_name}{optional}: {ts_type(prop)};")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def ts_comment(text: str, indent: str = "") -> str:
    return f"{indent}/** {text} */" if text else ""


# --------------------------------------------------------------------------- #
# TypeScript emission
# --------------------------------------------------------------------------- #
def emit_ts(contract: dict[str, Any], errors: dict[str, Any]) -> str:
    out: list[str] = [
        f"// {BANNER}",
        "",
        f"export const CONTRACT_VERSION = {json.dumps(contract['contractVersion'])};",
        "",
        "// ---- shared types ----",
    ]

    for name, schema in contract["types"].items():
        out.append(ts_interface(name, schema))
        out.append("")

    out.append("// ---- method params / results ----")
    method_names: list[str] = []
    params_entries: list[str] = []
    result_entries: list[str] = []

    for method in contract["methods"]:
        name = method["name"]
        method_names.append(name)
        base = pascal(name)
        params_name = f"{base}Params"
        result_name = f"{base}Result"

        out.append(ts_comment(method.get("summary", "")))
        out.append(ts_interface(params_name, method["params"]))
        out.append("")

        result_schema = method["result"]
        if "$ref" in result_schema:
            out.append(f"export type {result_name} = {ref_name(result_schema['$ref'])};")
        else:
            out.append(ts_interface(result_name, result_schema))
        out.append("")

        params_entries.append(f"  {json.dumps(name)}: {params_name};")
        result_entries.append(f"  {json.dumps(name)}: {result_name};")

    out.append("// ---- method surface ----")
    out.append("export type MethodName =")
    for name in method_names:
        out.append(f"  | {json.dumps(name)}")
    out[-1] = out[-1] + ";"
    out.append("")

    out.append("export interface MethodParams {")
    out.extend(params_entries)
    out.append("}")
    out.append("")
    out.append("export interface MethodResults {")
    out.extend(result_entries)
    out.append("}")
    out.append("")

    implemented = [m["name"] for m in contract["methods"] if m.get("implemented")]
    out.append("export const IMPLEMENTED_METHODS = [")
    for name in implemented:
        out.append(f"  {json.dumps(name)},")
    out.append("] as const;")
    out.append("")

    out.append("// ---- errors ----")
    out.append("export const ERROR_CODES = {")
    for entry in errors["codes"]:
        out.append(
            f"  {json.dumps(entry['code'])}: {{ jsonrpc: {entry['jsonrpc']}, "
            f"retryable: {str(entry['retryable']).lower()}, message_key: {json.dumps(entry['message_key'])} }},"
        )
    out.append("} as const;")
    out.append("")
    out.append("export type ErrorCode = keyof typeof ERROR_CODES;")
    out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Python emission
# --------------------------------------------------------------------------- #
def emit_py(contract: dict[str, Any], errors: dict[str, Any]) -> str:
    payload = {
        "contractVersion": contract["contractVersion"],
        "types": contract["types"],
        "methods": contract["methods"],
        "error_codes": errors["codes"],
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)

    return "\n".join(
        [
            f'"""{BANNER}"""',
            "",
            "from __future__ import annotations",
            "",
            "import json as _json",
            "",
            f"CONTRACT_VERSION = {json.dumps(contract['contractVersion'])}",
            "",
            '_CONTRACT_JSON = r"""',
            body,
            '"""',
            "",
            "_CONTRACT = _json.loads(_CONTRACT_JSON)",
            "",
            "TYPES: dict = _CONTRACT['types']",
            "METHOD_LIST: list = _CONTRACT['methods']",
            "METHODS: dict = {method['name']: method for method in METHOD_LIST}",
            "ERROR_CODES: dict = {entry['code']: entry for entry in _CONTRACT['error_codes']}",
            "IMPLEMENTED_METHODS: tuple = tuple(",
            "    method['name'] for method in METHOD_LIST if method.get('implemented')",
            ")",
            "",
        ]
    )


# --------------------------------------------------------------------------- #
def write_or_check(path: Path, content: str, check: bool) -> bool:
    """Return True when the file is up to date."""
    if check:
        if not path.exists():
            print(f"missing generated file: {path}", file=sys.stderr)
            return False
        existing = path.read_text(encoding="utf-8")
        if existing != content:
            print(f"out of date: {path}", file=sys.stderr)
            return False
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate contract artifacts.")
    parser.add_argument("--check", action="store_true", help="verify outputs are current")
    args = parser.parse_args(argv)

    contract = load(CONTRACT_DIR / "methods.json")
    errors = load(CONTRACT_DIR / "errors.json")

    ok = True
    ok &= write_or_check(PY_OUT, emit_py(contract, errors), args.check)
    ok &= write_or_check(TS_OUT, emit_ts(contract, errors), args.check)

    if args.check and not ok:
        print("run: python contract/codegen/generate.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
