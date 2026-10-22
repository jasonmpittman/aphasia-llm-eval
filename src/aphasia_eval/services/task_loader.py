"""Schema validation, prompt rendering, and gold compatibility for tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from aphasia_eval.comparison import get_comparator
from aphasia_eval.comparison.derived import DerivedValueError, with_derived_values
from aphasia_eval.gold import GoldParseError, get_gold_parser

from .gold_loader import GoldDataset, GoldItem, load_task_gold

PLAIN_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RENDERER_FIELDS = {"plain": set(), "numbered_tokens": {"numbered_tokens"}}


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """The exact messages sent for one task item."""

    system: str
    user: str


@dataclass(frozen=True, slots=True)
class TaskDefinition:
    """One schema-valid task plus optional usable gold data."""

    path: Path
    config: dict[str, Any]
    task_sha256: str
    prompt_sha256: str
    gold: GoldDataset | None

    @property
    def task_id(self) -> str:
        return self.config["task_id"]


@dataclass(frozen=True, slots=True)
class TaskLoadResult:
    """A task-loading outcome that keeps errors visible instead of raising."""

    path: Path
    task: TaskDefinition | None
    errors: tuple[str, ...]

    @property
    def task_id(self) -> str:
        return self.task.task_id if self.task is not None else self.path.stem

    @property
    def enabled(self) -> bool:
        return self.task is not None and self.task.gold is not None and not self.errors


def load_tasks(
    tasks_dir: str | Path = "tasks",
    gold_dir: str | Path = "gold_tasks",
    schema_path: str | Path | None = None,
) -> tuple[TaskLoadResult, ...]:
    """Load every top-level task YAML, returning valid and invalid results."""

    directory = Path(tasks_dir)
    resolved_schema = Path(schema_path) if schema_path else directory / "schema/task.schema.json"
    try:
        schema = _load_schema(resolved_schema)
    except ValueError as error:
        paths = sorted((*directory.glob("*.yaml"), *directory.glob("*.yml")))
        if not paths:
            paths = [directory]
        return tuple(TaskLoadResult(path=path, task=None, errors=(str(error),)) for path in paths)

    paths = sorted((*directory.glob("*.yaml"), *directory.glob("*.yml")))
    return tuple(load_task_file(path, gold_dir=gold_dir, schema=schema) for path in paths)


def load_task_file(
    path: str | Path,
    *,
    gold_dir: str | Path = "gold_tasks",
    schema: Mapping[str, Any] | None = None,
    schema_path: str | Path = "tasks/schema/task.schema.json",
) -> TaskLoadResult:
    """Load one task without allowing its failure to stop other tasks."""

    task_path = Path(path)
    try:
        config, hash_bytes = _load_task_config(task_path)
    except (OSError, ValueError, yaml.YAMLError) as error:
        return TaskLoadResult(
            path=task_path,
            task=None,
            errors=(f"could not read task YAML: {error}",),
        )
    if not isinstance(config, dict):
        return TaskLoadResult(
            path=task_path,
            task=None,
            errors=("task YAML must contain an object",),
        )

    try:
        resolved_schema = dict(schema) if schema is not None else _load_schema(Path(schema_path))
    except ValueError as error:
        return TaskLoadResult(path=task_path, task=None, errors=(str(error),))
    schema_errors = sorted(
        Draft202012Validator(resolved_schema).iter_errors(config),
        key=_validation_error_sort_key,
    )
    if schema_errors:
        return TaskLoadResult(
            path=task_path,
            task=None,
            errors=tuple(_format_schema_error(error) for error in schema_errors),
        )

    task_id = config["task_id"]
    errors: list[str] = []
    if task_path.stem != task_id:
        errors.append(f"task_id {task_id!r} must match filename stem {task_path.stem!r}")
    try:
        get_gold_parser(config["gold"]["parser"])
    except GoldParseError as error:
        errors.append(str(error))
    try:
        get_comparator(config["comparison"]["type"])
    except ValueError as error:
        errors.append(str(error))
    try:
        Draft202012Validator.check_schema(config["output"]["schema"])
    except SchemaError as error:
        errors.append(f"invalid output JSON Schema: {error}")
    try:
        validate_prompt_templates(config)
    except ValueError as error:
        errors.append(str(error))

    task_hash = hashlib.sha256(hash_bytes).hexdigest()
    prompt_hash = hashlib.sha256(
        (config["prompt"]["system"] + "\0" + config["prompt"]["user_template"]).encode()
    ).hexdigest()
    gold: GoldDataset | None = None
    if not errors:
        try:
            gold = load_task_gold(config, gold_dir)
            errors.extend(_validate_gold_items(config, gold))
        except (GoldParseError, ValueError) as error:
            errors.append(str(error))
    if errors:
        gold = None

    task = TaskDefinition(
        path=task_path,
        config=config,
        task_sha256=task_hash,
        prompt_sha256=prompt_hash,
        gold=gold,
    )
    return TaskLoadResult(path=task_path, task=task, errors=tuple(errors))


def _load_task_config(path: Path, ancestors: tuple[Path, ...] = ()) -> tuple[dict[str, Any], bytes]:
    """Load a task and shallow-merge an optional sibling base task."""

    resolved = path.resolve()
    if resolved in ancestors:
        chain = " -> ".join(item.name for item in (*ancestors, resolved))
        raise ValueError(f"task inheritance cycle: {chain}")
    raw_bytes = path.read_bytes()
    config = yaml.safe_load(raw_bytes) or {}
    if not isinstance(config, dict):
        return config, raw_bytes
    base_name = config.get("extends")
    if base_name is None:
        return config, raw_bytes
    if not isinstance(base_name, str) or not PLAIN_FIELD.fullmatch(base_name):
        raise ValueError("task extends must be a sibling task ID")
    base_path = path.parent / f"{base_name}.yaml"
    base, base_hash_bytes = _load_task_config(base_path, (*ancestors, resolved))
    merged = {**base, **config}
    return merged, base_hash_bytes + b"\0" + raw_bytes


def validate_prompt_templates(task: Mapping[str, Any]) -> None:
    """Reject undeclared, nested, converted, or formatted placeholders."""

    allowed = _allowed_prompt_fields(task)
    formatter = string.Formatter()
    for prompt_name in ("system", "user_template"):
        template = task["prompt"][prompt_name]
        try:
            parts = formatter.parse(template)
            for _literal, field_name, format_spec, conversion in parts:
                if field_name is None:
                    continue
                if not PLAIN_FIELD.fullmatch(field_name):
                    raise ValueError(f"prompt {prompt_name} uses unsafe placeholder {field_name!r}")
                if field_name not in allowed:
                    raise ValueError(
                        f"prompt {prompt_name} uses undeclared placeholder {field_name!r}"
                    )
                if format_spec or conversion:
                    raise ValueError(
                        f"prompt {prompt_name} placeholder {field_name!r} "
                        "must not use conversion or formatting"
                    )
        except ValueError as error:
            if str(error).startswith("prompt "):
                raise
            raise ValueError(f"prompt {prompt_name} has invalid braces: {error}") from error


def render_prompt(task: Mapping[str, Any], item: GoldItem | Mapping[str, Any]) -> RenderedPrompt:
    """Render only declared fields through a restricted formatter."""

    validate_prompt_templates(task)
    inputs = item.inputs if isinstance(item, GoldItem) else dict(item)
    input_config = task.get("input", {})
    required = set(input_config.get("fields", ()))
    optional = set(input_config.get("optional_fields", ()))
    missing = sorted(required - inputs.keys())
    if missing:
        raise ValueError(f"missing required input field(s): {', '.join(missing)}")

    context = {field: inputs.get(field, "") for field in required | optional}
    renderer = input_config.get("renderer", "plain")
    if renderer == "numbered_tokens":
        context["numbered_tokens"] = _render_numbered_tokens(inputs.get("tokens"))
    elif renderer != "plain":
        raise ValueError(f"unknown input renderer {renderer!r}")
    if "tagset" in task:
        context["tagset"] = ", ".join(task["tagset"])

    formatter = string.Formatter()
    return RenderedPrompt(
        system=formatter.vformat(task["prompt"]["system"], (), context),
        user=formatter.vformat(task["prompt"]["user_template"], (), context),
    )


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError, SchemaError) as error:
        raise ValueError(f"could not load task schema {path}: {error}") from error
    if not isinstance(schema, dict):
        raise ValueError(f"task schema {path} must contain an object")
    return schema


def _allowed_prompt_fields(task: Mapping[str, Any]) -> set[str]:
    input_config = task.get("input", {})
    renderer = input_config.get("renderer", "plain")
    allowed = set(input_config.get("fields", ())) | set(input_config.get("optional_fields", ()))
    allowed.update(RENDERER_FIELDS.get(renderer, set()))
    if "tagset" in task:
        allowed.add("tagset")
    return allowed


def _render_numbered_tokens(value: Any) -> str:
    if not isinstance(value, list) or not value:
        raise ValueError("numbered_tokens renderer requires a non-empty tokens list")
    lines = []
    current_utterance = None
    for token in value:
        if not isinstance(token, Mapping):
            raise ValueError("each numbered token must be an object")
        index = token.get("i")
        text = token.get("token")
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            raise ValueError(f"invalid numbered token index {index!r}")
        if not isinstance(text, str) or not text:
            raise ValueError(f"invalid token text at index {index}")
        utterance = token.get("utterance")
        if utterance is not None:
            if not isinstance(utterance, int) or isinstance(utterance, bool) or utterance < 1:
                raise ValueError(f"invalid utterance number at token index {index}")
            if utterance != current_utterance:
                if lines:
                    lines.append("")
                lines.append(f"Utterance {utterance}:")
                current_utterance = utterance
        lines.append(f"{index}\t{text}")
    return "\n".join(lines)


def _validate_gold_items(task: Mapping[str, Any], dataset: GoldDataset) -> list[str]:
    errors: list[str] = []
    output_validator = Draft202012Validator(task["output"]["schema"])
    comparator = get_comparator(task["comparison"]["type"])
    for item in dataset.items:
        prefix = f"{dataset.path}: line {item.line_number}, item {item.item_id!r}: "
        errors.extend(prefix + error for error in _validate_item_inputs(task, item))
        schema_errors = sorted(
            output_validator.iter_errors(item.gold), key=_validation_error_sort_key
        )
        errors.extend(prefix + _format_output_error(error) for error in schema_errors)
        errors.extend(prefix + error for error in comparator.validate_gold(task, item.gold))
        try:
            with_derived_values(task.get("derived"), item.gold, item.inputs)
            render_prompt(task, item)
        except (DerivedValueError, ValueError) as error:
            errors.append(prefix + str(error))
    return errors


def _validate_item_inputs(task: Mapping[str, Any], item: GoldItem) -> tuple[str, ...]:
    input_config = task.get("input", {})
    required = set(input_config.get("fields", ()))
    optional = set(input_config.get("optional_fields", ()))
    renderer = input_config.get("renderer", "plain")
    renderer_inputs = {"tokens"} if renderer == "numbered_tokens" else set()
    missing = required - item.inputs.keys()
    unknown = item.inputs.keys() - required - optional - renderer_inputs
    errors = []
    if missing:
        errors.append(f"missing required input field(s): {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"unknown input field(s): {', '.join(sorted(unknown))}")
    if renderer == "numbered_tokens" and "tokens" not in item.inputs:
        errors.append("numbered_tokens renderer requires parser-supplied tokens")
    return tuple(errors)


def _validation_error_sort_key(error: Any) -> tuple[str, str]:
    return (".".join(str(part) for part in error.absolute_path), error.message)


def _format_schema_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"task schema error at {path}: {error.message}"


def _format_output_error(error: Any) -> str:
    path = ".".join(str(part) for part in error.absolute_path) or "<root>"
    return f"gold output error at {path}: {error.message}"


def main(argv: list[str] | None = None) -> int:
    """Validate task and gold files from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", action="store_true", help="validate all task files")
    parser.add_argument("--tasks-dir", default=os.getenv("TASKS_DIR", "tasks"))
    parser.add_argument("--gold-dir", default=os.getenv("GOLD_TASKS_DIR", "gold_tasks"))
    args = parser.parse_args(argv)
    if not args.validate:
        parser.error("--validate is required")

    results = load_tasks(args.tasks_dir, args.gold_dir)
    if not results:
        print(f"No task YAML files found in {args.tasks_dir}.")
        return 1
    failed = False
    for result in results:
        if result.enabled:
            assert result.task is not None and result.task.gold is not None
            print(f"OK {result.task_id}: {len(result.task.gold.items)} gold item(s)")
        else:
            failed = True
            print(f"DISABLED {result.task_id}")
            for error in result.errors:
                print(f"  - {error}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
