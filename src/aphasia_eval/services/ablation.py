"""Parameter-condition helpers for opt-in ablation runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

BASELINE_CONDITION_ID = "baseline"
MAX_ABLATION_CONDITIONS = 8


@dataclass(frozen=True, slots=True)
class ExperimentCondition:
    """One immutable parameter condition within a run."""

    condition_id: str
    label: str
    parameter: str | None
    value: int | float | None
    is_baseline: bool
    settings: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-compatible condition provenance."""

        return {
            "condition_id": self.condition_id,
            "label": self.label,
            "parameter": self.parameter,
            "value": self.value,
            "is_baseline": self.is_baseline,
        }


def experiment_conditions(settings: Mapping[str, Any]) -> tuple[ExperimentCondition, ...]:
    """Expand normalized run settings into baseline and ablation conditions."""

    baseline_settings = dict(settings)
    baseline_settings.pop("ablation", None)
    ablation = settings.get("ablation")
    if not isinstance(ablation, Mapping):
        return (
            ExperimentCondition(
                BASELINE_CONDITION_ID,
                "Baseline",
                None,
                None,
                True,
                baseline_settings,
            ),
        )

    parameter = str(ablation["parameter"])
    values = ablation["values"]
    baseline_value = baseline_settings[parameter]
    conditions: list[ExperimentCondition] = []
    for value in values:
        condition_settings = {**baseline_settings, parameter: value}
        is_baseline = value == baseline_value
        condition_id = BASELINE_CONDITION_ID if is_baseline else f"{parameter}_{value}"
        conditions.append(
            ExperimentCondition(
                condition_id=condition_id,
                label=f"{parameter.replace('_', ' ')} · {value}",
                parameter=parameter,
                value=value,
                is_baseline=is_baseline,
                settings=condition_settings,
            )
        )
    return tuple(conditions)


def normalize_ablation(
    value: Any, *, baseline_max_tokens: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate the public M7 ablation request shape."""

    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, "ablation must be null or an object"
    unknown = sorted(set(value) - {"parameter", "values"})
    if unknown:
        return None, f"Unknown ablation field(s): {', '.join(unknown)}"
    if value.get("parameter") != "max_tokens":
        return None, "ablation parameter must be max_tokens"
    values = value.get("values")
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        return None, "ablation values must be a list of positive integers"
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in values):
        return None, "ablation values must be a list of positive integers"
    normalized = list(dict.fromkeys([baseline_max_tokens, *values]))
    if len(normalized) < 2:
        return None, "ablation requires at least two distinct max_tokens values"
    if len(normalized) > MAX_ABLATION_CONDITIONS:
        return None, f"ablation supports at most {MAX_ABLATION_CONDITIONS} conditions"
    return {
        "parameter": "max_tokens",
        "values": normalized,
        "baseline_value": baseline_max_tokens,
    }, None


def result_experiment(comparison: Any, repetition: int) -> dict[str, Any]:
    """Read condition provenance, including legacy baseline results."""

    if isinstance(comparison, Mapping):
        experiment = comparison.get("experiment")
        if isinstance(experiment, Mapping):
            return {
                "condition_id": str(experiment.get("condition_id", BASELINE_CONDITION_ID)),
                "label": str(experiment.get("label", "Baseline")),
                "parameter": experiment.get("parameter"),
                "value": experiment.get("value"),
                "is_baseline": bool(experiment.get("is_baseline", False)),
                "repetition": experiment.get("repetition", repetition),
                "requested_settings": experiment.get("requested_settings", {}),
                "optional_parameters_removed": bool(
                    experiment.get("optional_parameters_removed", False)
                ),
            }
    return {
        "condition_id": BASELINE_CONDITION_ID,
        "label": "Baseline",
        "parameter": None,
        "value": None,
        "is_baseline": True,
        "repetition": repetition,
        "requested_settings": {},
        "optional_parameters_removed": False,
    }
