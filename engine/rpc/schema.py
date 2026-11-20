"""Minimal JSON-Schema subset validator.

Deliberately small: it covers exactly the vocabulary used by
``contract/methods.json`` (type / required / properties / items / $ref /
additionalProperties). Anything outside that vocabulary is ignored rather than
guessed at, so the contract stays the only thing that needs to be understood.
"""

from __future__ import annotations

from typing import Any


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches(value: Any, expected: str) -> bool:
    actual = _type_name(value)
    if expected == "number":
        return actual in {"number", "integer"}
    if expected == "integer":
        # JSON has no int/float distinction; accept integral floats.
        return actual == "integer" or (actual == "number" and float(value).is_integer())
    return actual == expected


def validate(
    value: Any,
    schema: dict[str, Any],
    types: dict[str, Any],
    path: str = "params",
) -> list[str]:
    """Return a list of human-readable violations (empty means valid)."""
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], types)
        return validate(value, target, types, path) if target else []

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_matches(value, str(item)) for item in allowed):
            readable = " or ".join(str(item) for item in allowed)
            return [f"{path}: expected {readable}, got {_type_name(value)}"]

    problems: list[str] = []

    if isinstance(value, dict):
        for name in schema.get("required") or []:
            if name not in value:
                problems.append(f"{path}: missing required property '{name}'")

        properties: dict[str, Any] = schema.get("properties") or {}
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    problems.append(f"{path}: unexpected property '{name}'")

        for name, prop_schema in properties.items():
            if name in value:
                problems.extend(validate(value[name], prop_schema, types, f"{path}.{name}"))

    if isinstance(value, list) and "items" in schema:
        item_schema = schema["items"]
        for index, item in enumerate(value):
            problems.extend(validate(item, item_schema, types, f"{path}[{index}]"))

    return problems


def _resolve_ref(ref: str, types: dict[str, Any]) -> dict[str, Any] | None:
    name = ref.rsplit("/", 1)[-1]
    target = types.get(name)
    return target if isinstance(target, dict) else None
