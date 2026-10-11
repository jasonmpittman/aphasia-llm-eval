from __future__ import annotations

import pytest

from aphasia_eval.comparison.derived import (
    DerivedValueError,
    counts_by,
    derive_values,
    per_minute,
    percentage,
    ratio,
    with_derived_values,
)


def test_numeric_derived_functions_and_undefined_denominators():
    assert percentage(3, 4) == 75
    assert per_minute(12, 30) == 24
    assert ratio(3, 2) == 1.5
    for function in (percentage, per_minute, ratio):
        assert function(1, 0) is None
        assert function(None, 2) is None
        assert function(True, 2) is None
        assert function("1", 2) is None


def test_counts_by_handles_values_empty_and_bad_inputs():
    assert counts_by(["NOUN", "VERB", "NOUN"]) == {"NOUN": 2, "VERB": 1}
    assert counts_by([]) == {}
    assert counts_by(None) is None
    assert counts_by("NOUN") is None
    assert counts_by(["NOUN", 1]) is None


def test_derived_values_resolve_lists_inputs_and_prior_values():
    declarations = [
        {"name": "counts", "function": "counts_by", "args": ["tags.pos"]},
        {"name": "nv_ratio", "function": "ratio", "args": ["counts.NOUN", "counts.VERB"]},
        {"name": "rate", "function": "per_minute", "args": ["total", "duration"]},
    ]
    answer = {
        "total": 4,
        "tags": [
            {"pos": "NOUN"},
            {"pos": "NOUN"},
            {"pos": "VERB"},
        ],
    }
    assert derive_values(declarations, answer, {"duration": 30}) == {
        "counts": {"NOUN": 2, "VERB": 1},
        "nv_ratio": 2,
        "rate": 8,
    }


def test_missing_nested_count_produces_null_ratio():
    declarations = [
        {"name": "counts", "function": "counts_by", "args": ["tags.pos"]},
        {"name": "nv_ratio", "function": "ratio", "args": ["counts.NOUN", "counts.VERB"]},
    ]
    assert derive_values(declarations, {"tags": [{"pos": "NOUN"}]})["nv_ratio"] is None


def test_optional_missing_argument_is_null_but_required_missing_raises():
    optional = [
        {
            "name": "rate",
            "function": "per_minute",
            "args": ["count", "duration"],
            "optional": True,
        }
    ]
    assert derive_values(optional, {"count": 2}) == {"rate": None}

    required = [{"name": "rate", "function": "ratio", "args": ["missing", "count"]}]
    with pytest.raises(DerivedValueError, match="cannot resolve"):
        derive_values(required, {"count": 2})


def test_invalid_derived_declarations_are_rejected():
    with pytest.raises(DerivedValueError, match="non-empty name"):
        derive_values([{"function": "ratio", "args": []}], {})
    with pytest.raises(DerivedValueError, match="unknown derived function"):
        derive_values([{"name": "x", "function": "unknown", "args": []}], {})
    with pytest.raises(DerivedValueError, match="conflicts"):
        derive_values([{"name": "count", "function": "counts_by", "args": []}], {"count": 1})
    with pytest.raises(DerivedValueError, match="args must be a list"):
        derive_values([{"name": "x", "function": "ratio", "args": "a"}], {})
    with pytest.raises(DerivedValueError, match=r"requires 2 argument\(s\)"):
        derive_values([{"name": "x", "function": "ratio", "args": ["a"]}], {})


def test_with_derived_values_does_not_mutate_answer():
    answer = {"correct": 1, "total": 2}
    result = with_derived_values(
        [{"name": "percent", "function": "percentage", "args": ["correct", "total"]}],
        answer,
    )
    assert answer == {"correct": 1, "total": 2}
    assert result["percent"] == 50
