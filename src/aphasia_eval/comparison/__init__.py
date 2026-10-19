"""Registry and public interface for deterministic comparison plugins."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .derived import with_derived_values


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    """JSON-serializable per-item comparison output."""

    details: dict[str, Any]
    matched: bool | None
    statistics: dict[str, Any]
    view_descriptor: dict[str, Any]
    model_derived: dict[str, Any]
    gold_derived: dict[str, Any]


class Comparator(Protocol):
    """Small plugin contract used by task loading and run comparison."""

    name: str

    def compare(
        self,
        task: Mapping[str, Any],
        model_answer: Mapping[str, Any],
        gold_answer: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool | None, dict[str, Any]]: ...

    def validate_gold(
        self, task: Mapping[str, Any], gold_answer: Mapping[str, Any]
    ) -> tuple[str, ...]: ...

    def view_descriptor(self, task: Mapping[str, Any]) -> dict[str, Any]: ...


COMPARATORS: dict[str, Comparator] = {}


def register_comparator(comparator: Comparator) -> None:
    """Register one comparator under its stable task-facing name."""

    if comparator.name in COMPARATORS:
        raise ValueError(f"comparator {comparator.name!r} is already registered")
    COMPARATORS[comparator.name] = comparator


def get_comparator(name: str) -> Comparator:
    """Resolve a comparator name from the fixed plugin registry."""

    try:
        return COMPARATORS[name]
    except KeyError as error:
        available = ", ".join(sorted(COMPARATORS))
        raise ValueError(
            f"unknown comparator {name!r}; available comparators: {available}"
        ) from error


def compare_answers(
    task: Mapping[str, Any],
    model_answer: Mapping[str, Any],
    gold_answer: Mapping[str, Any],
    inputs: Mapping[str, Any] | None = None,
) -> ComparisonOutcome:
    """Derive values identically for model and gold, then invoke the plugin."""

    declarations = task.get("derived")
    model_with_derived = with_derived_values(declarations, model_answer, inputs)
    gold_with_derived = with_derived_values(declarations, gold_answer, inputs)
    comparator = get_comparator(task["comparison"]["type"])
    details, matched, statistics = comparator.compare(task, model_with_derived, gold_with_derived)
    model_derived = {
        declaration["name"]: model_with_derived.get(declaration["name"])
        for declaration in declarations or ()
    }
    gold_derived = {
        declaration["name"]: gold_with_derived.get(declaration["name"])
        for declaration in declarations or ()
    }
    return ComparisonOutcome(
        details=details,
        matched=matched,
        statistics=statistics,
        view_descriptor=comparator.view_descriptor(task),
        model_derived=model_derived,
        gold_derived=gold_derived,
    )


from .ciu_token_labels import CIUTokenLabelsComparator  # noqa: E402
from .numeric_fields import NumericFieldsComparator  # noqa: E402
from .token_tags import TokenTagsComparator  # noqa: E402

register_comparator(NumericFieldsComparator())
register_comparator(TokenTagsComparator())
register_comparator(CIUTokenLabelsComparator())

__all__ = [
    "COMPARATORS",
    "Comparator",
    "ComparisonOutcome",
    "compare_answers",
    "get_comparator",
    "register_comparator",
]
