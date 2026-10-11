"""Comparator for numeric answer fields and derived numeric values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class NumericFieldsComparator:
    """Compare declared numeric fields with field-specific tolerances."""

    name = "numeric_fields"

    def compare(
        self,
        task: Mapping[str, Any],
        model_answer: Mapping[str, Any],
        gold_answer: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        comparison = task["comparison"]
        tolerances = comparison.get("tolerance", {})
        field_details: dict[str, Any] = {}
        field_statistics: dict[str, Any] = {}
        matches: list[bool] = []

        for field in comparison["fields"]:
            model_value = model_answer.get(field)
            gold_value = gold_answer.get(field)
            tolerance = tolerances.get(field, 0)
            valid = _is_number(model_value) and _is_number(gold_value)
            difference = model_value - gold_value if valid else None
            absolute_difference = abs(difference) if difference is not None else None
            within_tolerance = bool(
                valid and absolute_difference is not None and absolute_difference <= tolerance
            )
            exact_match = bool(valid and difference == 0)
            matches.append(within_tolerance)
            field_details[field] = {
                "model": model_value,
                "gold": gold_value,
                "difference": difference,
                "absolute_difference": absolute_difference,
                "tolerance": tolerance,
                "within_tolerance": within_tolerance,
            }
            field_statistics[field] = {
                "absolute_difference": absolute_difference,
                "signed_difference": difference,
                "exact_match": exact_match,
            }

        matched = all(matches)
        return (
            {"fields": field_details},
            matched,
            {"item_match": matched, "fields": field_statistics},
        )

    def validate_gold(
        self, task: Mapping[str, Any], gold_answer: Mapping[str, Any]
    ) -> tuple[str, ...]:
        errors = []
        derived_names = {declaration["name"] for declaration in task.get("derived", ())}
        for field in task["comparison"]["fields"]:
            if field not in gold_answer and field not in derived_names:
                errors.append(f"comparison field {field!r} is absent from gold and derived values")
            elif field in gold_answer and not _is_number(gold_answer[field]):
                errors.append(f"comparison field {field!r} must be numeric")
        return tuple(errors)

    def view_descriptor(self, task: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "type": self.name,
            "table": {"fields": list(task["comparison"]["fields"])},
            "chart": {
                "type": "model_vs_gold_scatter",
                "field": task["headline"].get("field"),
                "identity_line": True,
            },
        }


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float)
