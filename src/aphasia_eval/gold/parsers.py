"""Built-in parsers for the ``[gold]`` section of gold files."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class SourceLine:
    """One source line with its one-based file line number."""

    number: int
    text: str


@dataclass(frozen=True, slots=True)
class ParsedGold:
    """A normalized gold answer and parser-supplied prompt inputs."""

    answer: dict[str, Any]
    inputs: dict[str, Any]


class GoldParseError(ValueError):
    """A malformed gold section with optional source-line context."""

    def __init__(self, message: str, *, line_number: int | None = None) -> None:
        self.message = message
        self.line_number = line_number
        prefix = f"line {line_number}: " if line_number is not None else ""
        super().__init__(f"{prefix}{message}")


GoldParser = Callable[[Sequence[SourceLine]], ParsedGold]


def parse_kv(lines: Sequence[SourceLine]) -> ParsedGold:
    """Parse ``key: YAML-scalar`` lines into a gold answer mapping."""

    answer: dict[str, Any] = {}
    for source_line in lines:
        stripped = source_line.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise GoldParseError(
                "expected 'key: value' in [gold] section",
                line_number=source_line.number,
            )
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key:
            raise GoldParseError("gold key must not be empty", line_number=source_line.number)
        if key in answer:
            raise GoldParseError(f"duplicate gold key {key!r}", line_number=source_line.number)
        if not raw_value.strip():
            raise GoldParseError(
                f"gold value for {key!r} must not be empty",
                line_number=source_line.number,
            )
        try:
            value = yaml.safe_load(raw_value.strip())
        except yaml.YAMLError as error:
            raise GoldParseError(
                f"invalid value for {key!r}: {error}", line_number=source_line.number
            ) from error
        if isinstance(value, dict | list):
            raise GoldParseError(
                f"gold value for {key!r} must be a scalar",
                line_number=source_line.number,
            )
        answer[key] = value

    if not answer:
        line_number = lines[0].number if lines else None
        raise GoldParseError("[gold] section is empty", line_number=line_number)
    return ParsedGold(answer=answer, inputs={})


def parse_token_table(lines: Sequence[SourceLine]) -> ParsedGold:
    """Parse tab-separated ``index, token, tag`` rows."""

    tags: list[dict[str, Any]] = []
    tokens: list[dict[str, Any]] = []
    seen_indices: set[int] = set()
    for source_line in lines:
        stripped = source_line.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        columns = source_line.text.split("\t")
        if len(columns) != 3:
            raise GoldParseError(
                "expected three tab-separated columns: index, token, tag",
                line_number=source_line.number,
            )
        raw_index, raw_token, raw_tag = (column.strip() for column in columns)
        try:
            index = int(raw_index)
        except ValueError as error:
            raise GoldParseError(
                f"token index must be an integer, got {raw_index!r}",
                line_number=source_line.number,
            ) from error
        if index < 1:
            raise GoldParseError("token index must be at least 1", line_number=source_line.number)
        if index in seen_indices:
            raise GoldParseError(f"duplicate token index {index}", line_number=source_line.number)
        if not raw_token:
            raise GoldParseError("token must not be empty", line_number=source_line.number)
        if not raw_tag:
            raise GoldParseError("tag must not be empty", line_number=source_line.number)
        seen_indices.add(index)
        tokens.append({"i": index, "token": raw_token})
        tags.append({"i": index, "pos": raw_tag})

    if not tags:
        line_number = lines[0].number if lines else None
        raise GoldParseError("[gold] token table is empty", line_number=line_number)
    return ParsedGold(answer={"tags": tags}, inputs={"tokens": tokens})
