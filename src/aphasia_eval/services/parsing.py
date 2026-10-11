"""Extract and schema-validate JSON objects from untrusted model text."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator

ParseErrorCode = Literal["no_json_object", "schema_validation"]


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """A safe explanation of why model output could not be parsed."""

    code: ParseErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    """The parsed object or a structured parse failure."""

    value: dict[str, Any] | None
    error: ParseFailure | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def extract_first_json_object(raw_text: str) -> dict[str, Any] | None:
    """Return the first syntactically valid JSON object embedded in text."""

    decoder = json.JSONDecoder()
    for position, character in enumerate(raw_text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw_text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_model_output(raw_text: str, schema: dict[str, Any]) -> ParseResult:
    """Extract the first JSON object and validate it against a task schema."""

    value = extract_first_json_object(raw_text)
    if value is None:
        return ParseResult(
            value=None,
            error=ParseFailure(
                code="no_json_object",
                message="Model output did not contain a valid JSON object.",
            ),
        )

    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=_error_sort_key)
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path)
        prefix = f" at {location}" if location else ""
        return ParseResult(
            value=None,
            error=ParseFailure(
                code="schema_validation",
                message=f"Model JSON failed output schema{prefix}: {first.message}",
            ),
        )
    return ParseResult(value=value)


def _error_sort_key(error: Any) -> tuple[str, str]:
    return (".".join(str(part) for part in error.absolute_path), error.message)
