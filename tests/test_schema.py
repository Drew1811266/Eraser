"""The schema validator must reject exactly what the contract forbids."""

from __future__ import annotations

import unittest

from engine.rpc.schema import validate

TYPES = {
    "Rect": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
        },
        "required": ["x", "y"],
    }
}


class TypeTests(unittest.TestCase):
    def test_accepts_matching_primitive(self) -> None:
        self.assertEqual(validate("a", {"type": "string"}, TYPES), [])
        self.assertEqual(validate(True, {"type": "boolean"}, TYPES), [])
        self.assertEqual(validate(None, {"type": "null"}, TYPES), [])

    def test_integer_accepts_integral_float(self) -> None:
        self.assertEqual(validate(1920.0, {"type": "integer"}, TYPES), [])

    def test_integer_rejects_fractional_float(self) -> None:
        self.assertTrue(validate(1.5, {"type": "integer"}, TYPES))

    def test_boolean_is_not_an_integer(self) -> None:
        self.assertTrue(validate(True, {"type": "integer"}, TYPES))

    def test_number_accepts_integer(self) -> None:
        self.assertEqual(validate(3, {"type": "number"}, TYPES), [])

    def test_union_type_reports_both_choices(self) -> None:
        problems = validate("x", {"type": ["number", "null"]}, TYPES)
        self.assertEqual(len(problems), 1)
        self.assertIn("number", problems[0])
        self.assertIn("null", problems[0])

    def test_null_allowed_by_union(self) -> None:
        self.assertEqual(validate(None, {"type": ["number", "null"]}, TYPES), [])


class ObjectTests(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    }

    def test_valid_object_passes(self) -> None:
        self.assertEqual(validate({"a": "x"}, self.SCHEMA, TYPES), [])

    def test_missing_required_is_reported_with_path(self) -> None:
        problems = validate({}, self.SCHEMA, TYPES)
        self.assertEqual(len(problems), 1)
        self.assertIn("'a'", problems[0])

    def test_unexpected_property_is_reported(self) -> None:
        problems = validate({"a": "x", "z": 1}, self.SCHEMA, TYPES)
        self.assertTrue(any("'z'" in problem for problem in problems))

    def test_nested_property_path_is_precise(self) -> None:
        problems = validate({"a": 7}, self.SCHEMA, TYPES)
        self.assertIn("params.a", problems[0])


class ArrayTests(unittest.TestCase):
    SCHEMA = {"type": "array", "items": {"type": "string"}}

    def test_valid_array_passes(self) -> None:
        self.assertEqual(validate(["a", "b"], self.SCHEMA, TYPES), [])

    def test_bad_item_reports_index(self) -> None:
        problems = validate(["a", 2], self.SCHEMA, TYPES)
        self.assertEqual(len(problems), 1)
        self.assertIn("[1]", problems[0])

    def test_non_array_is_rejected(self) -> None:
        self.assertTrue(validate("a", self.SCHEMA, TYPES))


class RefTests(unittest.TestCase):
    def test_ref_resolves_named_type(self) -> None:
        self.assertEqual(validate({"x": 1, "y": 2}, {"$ref": "#/types/Rect"}, TYPES), [])

    def test_ref_reports_inner_violations(self) -> None:
        problems = validate({"x": 1}, {"$ref": "#/types/Rect"}, TYPES)
        self.assertTrue(any("'y'" in problem for problem in problems))

    def test_unknown_ref_is_ignored_rather_than_crashing(self) -> None:
        self.assertEqual(validate({"anything": 1}, {"$ref": "#/types/Missing"}, TYPES), [])


if __name__ == "__main__":
    unittest.main()
