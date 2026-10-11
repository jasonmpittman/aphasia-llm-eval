"""Load the proposed line-oriented gold-file format with provenance hashes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from aphasia_eval.gold import GoldParseError, SourceLine, get_gold_parser

ITEM_HEADER = re.compile(r"^===\s*item:\s*(.+?)\s*===$")
SECTION_HEADER = re.compile(r"^\[([a-zA-Z0-9_.]+)]$")
ITEM_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class GoldItem:
    """One normalized reference item."""

    item_id: str
    inputs: dict[str, Any]
    gold: dict[str, Any]
    line_number: int


@dataclass(frozen=True, slots=True)
class GoldDataset:
    """Validated gold-file contents plus immutable provenance metadata."""

    path: Path
    sha256: str
    items: tuple[GoldItem, ...]


def resolve_gold_path(task_id: str, gold_dir: str | Path, file_override: str | None = None) -> Path:
    """Resolve a task's gold filename inside the configured gold directory."""

    filename = file_override or f"gold_{task_id}.txt"
    if Path(filename).name != filename:
        raise GoldParseError("gold filename must not contain directory components")
    return Path(gold_dir) / filename


def load_gold_file(path: str | Path, parser_name: str) -> GoldDataset:
    """Read and parse a gold file, enforcing unique item identifiers."""

    gold_path = Path(path)
    try:
        raw_bytes = gold_path.read_bytes()
    except FileNotFoundError as error:
        raise GoldParseError(f"gold file not found: {gold_path}") from error
    except OSError as error:
        raise GoldParseError(f"could not read gold file {gold_path}: {error}") from error
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GoldParseError(f"gold file is not valid UTF-8: {gold_path}") from error

    parser = get_gold_parser(parser_name)
    items: list[GoldItem] = []
    seen_ids: dict[str, int] = {}
    current_id: str | None = None
    current_line = 0
    current_section: str | None = None
    sections: dict[str, list[SourceLine]] = {}

    def finish_item() -> None:
        nonlocal current_id, current_line, sections
        if current_id is None:
            return
        if "gold" not in sections:
            raise GoldParseError(
                f"item {current_id!r} is missing a [gold] section",
                line_number=current_line,
            )
        inputs = _parse_input_sections(sections)
        parsed = parser(sections["gold"])
        overlap = inputs.keys() & parsed.inputs.keys()
        if overlap:
            names = ", ".join(sorted(overlap))
            raise GoldParseError(
                f"item {current_id!r} defines parser-supplied input(s) twice: {names}",
                line_number=current_line,
            )
        inputs.update(parsed.inputs)
        items.append(
            GoldItem(
                item_id=current_id,
                inputs=inputs,
                gold=parsed.answer,
                line_number=current_line,
            )
        )

    for line_number, line in enumerate(text.splitlines(), start=1):
        item_match = ITEM_HEADER.fullmatch(line.strip())
        if item_match:
            finish_item()
            item_id = item_match.group(1).strip()
            if not ITEM_ID.fullmatch(item_id):
                raise GoldParseError(f"invalid item ID {item_id!r}", line_number=line_number)
            if item_id in seen_ids:
                raise GoldParseError(
                    f"duplicate item ID {item_id!r}; first declared on line {seen_ids[item_id]}",
                    line_number=line_number,
                )
            seen_ids[item_id] = line_number
            current_id = item_id
            current_line = line_number
            current_section = None
            sections = {}
            continue

        section_match = SECTION_HEADER.fullmatch(line.strip())
        if section_match:
            if current_id is None:
                raise GoldParseError("section appears before first item", line_number=line_number)
            section = section_match.group(1)
            if section in sections:
                raise GoldParseError(f"duplicate [{section}] section", line_number=line_number)
            if section != "input" and section != "gold" and not section.startswith("input."):
                raise GoldParseError(f"unknown section [{section}]", line_number=line_number)
            current_section = section
            sections[section] = []
            continue

        if current_id is None:
            if line.strip() and not line.lstrip().startswith("#"):
                raise GoldParseError("content appears before first item", line_number=line_number)
            continue
        if current_section is None:
            if line.strip() and not line.lstrip().startswith("#"):
                raise GoldParseError(
                    f"item {current_id!r} has content outside a section",
                    line_number=line_number,
                )
            continue
        sections[current_section].append(SourceLine(line_number, line))

    finish_item()
    if not items:
        raise GoldParseError("gold file contains no items")
    return GoldDataset(
        path=gold_path,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        items=tuple(items),
    )


def load_task_gold(task: dict[str, Any], gold_dir: str | Path) -> GoldDataset:
    """Resolve and load the gold file declared by a task configuration."""

    gold_config = task["gold"]
    path = resolve_gold_path(task["task_id"], gold_dir, gold_config.get("file"))
    return load_gold_file(path, gold_config["parser"])


def _parse_input_sections(sections: dict[str, list[SourceLine]]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for source_line in sections.get("input", []):
        stripped = source_line.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise GoldParseError(
                "expected 'key: value' in [input] section",
                line_number=source_line.number,
            )
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        if not key or not raw_value.strip():
            raise GoldParseError(
                "input key and value must not be empty", line_number=source_line.number
            )
        if key in inputs:
            raise GoldParseError(f"duplicate input field {key!r}", line_number=source_line.number)
        try:
            value = yaml.safe_load(raw_value.strip())
        except yaml.YAMLError as error:
            raise GoldParseError(
                f"invalid input value for {key!r}: {error}",
                line_number=source_line.number,
            ) from error
        if isinstance(value, dict | list):
            raise GoldParseError(
                f"input value for {key!r} must be a scalar",
                line_number=source_line.number,
            )
        inputs[key] = value

    for section, lines in sections.items():
        if not section.startswith("input."):
            continue
        field = section.removeprefix("input.")
        if not field:
            first_line = lines[0].number if lines else None
            raise GoldParseError("text input field must have a name", line_number=first_line)
        if field in inputs:
            first_line = lines[0].number if lines else None
            raise GoldParseError(f"duplicate input field {field!r}", line_number=first_line)
        inputs[field] = "\n".join(line.text for line in lines).strip("\n")
    return inputs
