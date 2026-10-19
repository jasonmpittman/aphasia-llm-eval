from __future__ import annotations

import pytest

from aphasia_eval.comparison import compare_answers, get_comparator, register_comparator


def test_numeric_fields_has_hand_computed_differences_and_tolerances():
    task = {
        "comparison": {
            "type": "numeric_fields",
            "fields": ["total", "correct", "percent"],
            "tolerance": {"total": 1, "correct": 0, "percent": 0.5},
        },
        "derived": [{"name": "percent", "function": "percentage", "args": ["correct", "total"]}],
        "headline": {"stat": "mean_abs_diff", "field": "correct", "better": "lower"},
    }

    outcome = compare_answers(task, {"total": 11, "correct": 8}, {"total": 10, "correct": 8})

    assert outcome.matched is False
    assert outcome.model_derived == {"percent": pytest.approx(72.7272727)}
    assert outcome.gold_derived == {"percent": 80.0}
    assert outcome.details["fields"]["total"] == {
        "model": 11,
        "gold": 10,
        "difference": 1,
        "absolute_difference": 1,
        "tolerance": 1,
        "within_tolerance": True,
    }
    assert outcome.details["fields"]["correct"]["within_tolerance"] is True
    assert outcome.details["fields"]["percent"]["difference"] == pytest.approx(-7.2727273)
    assert outcome.statistics["fields"]["correct"] == {
        "absolute_difference": 0,
        "signed_difference": 0,
        "exact_match": True,
    }
    assert outcome.view_descriptor["chart"]["identity_line"] is True


def test_numeric_fields_missing_value_is_a_non_match():
    comparator = get_comparator("numeric_fields")
    task = {
        "comparison": {"type": "numeric_fields", "fields": ["count"]},
        "headline": {"stat": "mean_abs_diff", "field": "count", "better": "lower"},
    }

    details, matched, statistics = comparator.compare(task, {}, {"count": 2})

    assert matched is False
    assert details["fields"]["count"]["difference"] is None
    assert statistics["fields"]["count"]["exact_match"] is False
    assert comparator.validate_gold(task, {}) == (
        "comparison field 'count' is absent from gold and derived values",
    )
    assert comparator.validate_gold(task, {"count": "two"}) == (
        "comparison field 'count' must be numeric",
    )


def test_ciu_token_labels_reports_three_states_and_alignment_errors():
    task = {
        "comparison": {
            "type": "ciu_token_labels",
            "fields": ["total_words", "ciu_count", "percent_ciu"],
            "tolerance": {"total_words": 0, "ciu_count": 0, "percent_ciu": 0.5},
        },
        "derived": [
            {"name": "total_words", "function": "length", "args": ["word_indices"]},
            {"name": "ciu_count", "function": "length", "args": ["ciu_indices"]},
            {
                "name": "percent_ciu",
                "function": "percentage",
                "args": ["ciu_count", "total_words"],
            },
        ],
        "headline": {"stat": "mean_abs_diff", "field": "ciu_count", "better": "lower"},
    }
    gold = {"token_count": 4, "word_indices": [1, 2, 4], "ciu_indices": [1, 4]}
    model = {"word_indices": [1, 2, 3], "ciu_indices": [1, 3, 5]}

    outcome = compare_answers(task, model, gold)

    assert outcome.matched is False
    assert [row["gold"] for row in outcome.details["tokens"]] == [
        "ciu",
        "not_ciu",
        "not_word",
        "ciu",
    ]
    assert outcome.details["token_label_agreement"] == 0.5
    assert outcome.details["out_of_range_indices"] == [5]
    assert outcome.details["ciu_not_word_indices"] == [5]
    assert outcome.details["classification"]["observations"] == 5
    assert outcome.details["classification"]["per_label"]["ciu"]["precision"] == 0.5
    assert outcome.details["classification"]["per_label"]["ciu"]["recall"] == 0.5
    assert outcome.details["classification"]["per_label"]["ciu"]["f1"] == 0.5
    assert outcome.details["classification"]["confusion_matrix"]["__extra__"]["__invalid__"] == 1
    assert outcome.statistics["alignment_problem"] is True
    assert outcome.view_descriptor["token_annotations"] is True
    assert outcome.view_descriptor["classification_labels"] == ["not_word", "not_ciu", "ciu"]


def test_ciu_token_labels_validates_gold_contract():
    comparator = get_comparator("ciu_token_labels")
    task = {
        "comparison": {"type": "ciu_token_labels", "fields": []},
        "headline": {"stat": "mean_abs_diff", "field": "ciu_count", "better": "lower"},
    }
    errors = comparator.validate_gold(
        task,
        {"token_count": 2, "word_indices": [1, 1], "ciu_indices": [2, 3]},
    )
    assert "CIU token gold word_indices contains duplicate indices" in errors
    assert "CIU token gold ciu_indices contains an out-of-range index" in errors
    assert "CIU token gold ciu_indices must be a subset of word_indices" in errors


def _token_task():
    return {
        "tagset": ["DET", "NOUN", "VERB"],
        "comparison": {
            "type": "token_tags",
            "index_field": "i",
            "tag_field": "pos",
            "breakdown": True,
        },
        "derived": [
            {"name": "counts", "function": "counts_by", "args": ["tags.pos"]},
            {
                "name": "noun_verb",
                "function": "ratio",
                "args": ["counts.NOUN", "counts.VERB"],
            },
        ],
        "headline": {"stat": "token_agreement", "better": "higher"},
    }


def test_token_tags_reports_every_alignment_and_category_detail():
    gold = {
        "tags": [
            {"i": 1, "pos": "DET"},
            {"i": 2, "pos": "NOUN"},
            {"i": 3, "pos": "VERB"},
            {"i": 4, "pos": "NOUN"},
        ]
    }
    model = {
        "tags": [
            {"i": 1, "pos": "DET"},
            {"i": 2, "pos": "VERB"},
            {"i": 2, "pos": "NOUN"},
            {"i": 4, "pos": "ALIEN"},
            {"i": 5, "pos": "NOUN"},
        ]
    }

    outcome = compare_answers(_token_task(), model, gold)

    assert outcome.matched is False
    assert outcome.details["alignment_status"] == "missing"
    assert outcome.details["missing_indices"] == [3]
    assert outcome.details["duplicate_indices"] == [2]
    assert outcome.details["extra_indices"] == [5]
    assert outcome.details["out_of_tagset"] == [{"i": 4, "tag": "ALIEN"}]
    assert outcome.details["token_agreement"] == 0.25
    assert outcome.details["categories"]["NOUN"] == {
        "model_count": 2,
        "gold_count": 2,
        "signed_difference": 0,
        "model_proportion": 0.5,
        "gold_proportion": 0.5,
    }
    assert outcome.details["categories"]["VERB"]["signed_difference"] == 0
    assert outcome.details["derived_ratios"]["noun_verb"] == {
        "model": 2.0,
        "gold": 2.0,
    }
    assert outcome.statistics["alignment_problem"] is True
    assert outcome.statistics["classification"]["observations"] == 5
    assert outcome.statistics["classification"]["per_label"]["NOUN"]["precision"] == 0
    assert outcome.statistics["classification"]["confusion_matrix"]["NOUN"]["__duplicate__"] == 1
    assert outcome.statistics["classification"]["confusion_matrix"]["__extra__"]["NOUN"] == 1
    assert outcome.view_descriptor["chart"]["categories"] == ["DET", "NOUN", "VERB"]
    assert outcome.view_descriptor["classification_labels"] == ["DET", "NOUN", "VERB"]


@pytest.mark.parametrize(
    "model_tags, status",
    [
        ([{"i": 1, "pos": "NOUN"}], "missing"),
        (
            [
                {"i": 1, "pos": "NOUN"},
                {"i": 1, "pos": "NOUN"},
                {"i": 2, "pos": "VERB"},
            ],
            "duplicate",
        ),
        (
            [
                {"i": 1, "pos": "NOUN"},
                {"i": 2, "pos": "VERB"},
                {"i": 3, "pos": "DET"},
            ],
            "extra",
        ),
        ([{"i": 1, "pos": "NOUN"}, {"i": 2, "pos": "VERB"}], "complete"),
    ],
)
def test_token_tag_alignment_statuses(model_tags, status):
    comparator = get_comparator("token_tags")
    gold = {"tags": [{"i": 1, "pos": "NOUN"}, {"i": 2, "pos": "VERB"}]}

    details, _matched, _statistics = comparator.compare(_token_task(), {"tags": model_tags}, gold)

    assert details["alignment_status"] == status


def test_token_tags_validates_gold_and_registry_errors():
    comparator = get_comparator("token_tags")
    errors = comparator.validate_gold(
        _token_task(),
        {
            "tags": [
                {"i": 1, "pos": "NOUN"},
                {"i": 1, "pos": "OUTSIDE"},
                {"i": 0, "pos": "VERB"},
                "not-an-object",
            ]
        },
    )
    assert "duplicate gold tag index 1" in errors
    assert "gold tag 'OUTSIDE' is outside the declared tagset" in errors
    assert "gold tag index must be a positive integer, got 0" in errors
    assert "each gold tag must be an object" in errors
    assert comparator.validate_gold(_token_task(), {}) == (
        "token_tags gold answer requires a non-empty tags list",
    )

    with pytest.raises(ValueError, match="unknown comparator"):
        get_comparator("unknown")
    with pytest.raises(ValueError, match="already registered"):
        register_comparator(comparator)
