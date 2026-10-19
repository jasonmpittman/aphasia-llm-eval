from __future__ import annotations

from aphasia_eval.services.ablation import (
    experiment_conditions,
    normalize_ablation,
    result_experiment,
)


def test_normalize_ablation_includes_baseline_and_rejects_bad_shapes():
    normalized, error = normalize_ablation(
        {"parameter": "max_tokens", "values": [256, 512, 256]},
        baseline_max_tokens=1024,
    )
    assert error is None
    assert normalized == {
        "parameter": "max_tokens",
        "values": [1024, 256, 512],
        "baseline_value": 1024,
    }
    assert normalize_ablation(None, baseline_max_tokens=10) == (None, None)
    assert normalize_ablation([], baseline_max_tokens=10)[1] == (
        "ablation must be null or an object"
    )
    assert (
        "must be max_tokens"
        in normalize_ablation(
            {"parameter": "temperature", "values": [1, 2]}, baseline_max_tokens=10
        )[1]
    )
    assert (
        "at least two"
        in normalize_ablation({"parameter": "max_tokens", "values": [10]}, baseline_max_tokens=10)[
            1
        ]
    )


def test_conditions_and_legacy_result_metadata():
    settings = {
        "temperature": 0.0,
        "max_tokens": 512,
        "repetitions": 1,
        "max_items_per_task": None,
        "ablation": {
            "parameter": "max_tokens",
            "values": [512, 128],
            "baseline_value": 512,
        },
    }
    conditions = experiment_conditions(settings)
    assert [condition.condition_id for condition in conditions] == [
        "baseline",
        "max_tokens_128",
    ]
    assert conditions[0].settings["max_tokens"] == 512
    assert conditions[1].settings["max_tokens"] == 128
    assert result_experiment(None, 3) == {
        "condition_id": "baseline",
        "label": "Baseline",
        "parameter": None,
        "value": None,
        "is_baseline": True,
        "repetition": 3,
        "requested_settings": {},
        "optional_parameters_removed": False,
    }
