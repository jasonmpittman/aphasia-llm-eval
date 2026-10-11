from __future__ import annotations

from aphasia_eval.services.parsing import extract_first_json_object, parse_model_output

SCHEMA = {
    "type": "object",
    "required": ["count"],
    "properties": {"count": {"type": "integer", "minimum": 0}},
    "additionalProperties": False,
}


def test_extracts_json_from_fence_and_surrounding_prose():
    result = parse_model_output('Here is the result:\n```json\n{"count": 3}\n```\nDone.', SCHEMA)
    assert result.ok
    assert result.value == {"count": 3}


def test_skips_malformed_brace_before_first_valid_object():
    assert extract_first_json_object('noise {broken then {"count": 4} trailing') == {"count": 4}


def test_returns_first_syntactically_valid_object():
    result = parse_model_output('{"wrong": 1} and later {"count": 2}', SCHEMA)
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "schema_validation"


def test_missing_or_malformed_json_is_parse_failure():
    for text in ("nothing structured", "```json\n{broken}\n```"):
        result = parse_model_output(text, SCHEMA)
        assert not result.ok
        assert result.value is None
        assert result.error is not None
        assert result.error.code == "no_json_object"


def test_schema_failure_includes_nested_location():
    schema = {
        "type": "object",
        "required": ["nested"],
        "properties": {
            "nested": {
                "type": "object",
                "required": ["count"],
                "properties": {"count": {"type": "integer"}},
            }
        },
    }
    result = parse_model_output('{"nested": {"count": "three"}}', schema)
    assert result.error is not None
    assert result.error.code == "schema_validation"
    assert "nested.count" in result.error.message
