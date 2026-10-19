from __future__ import annotations

import pytest

from aphasia_eval.comparison.classification import (
    EXTRA_LABEL,
    INVALID_LABEL,
    MISSING_LABEL,
    classification_from_confusion,
    classification_summary,
)


def test_classification_summary_has_hand_computed_metrics_and_special_buckets():
    summary = classification_summary(
        ("A", "B"),
        [
            ("A", "A"),
            ("A", "B"),
            ("A", MISSING_LABEL),
            ("B", "B"),
            ("B", INVALID_LABEL),
            (EXTRA_LABEL, "A"),
        ],
    )

    assert summary["observations"] == 6
    assert summary["gold_labels"] == ["A", "B", EXTRA_LABEL]
    assert summary["predicted_labels"] == ["A", "B", MISSING_LABEL, INVALID_LABEL]
    assert summary["confusion_matrix"]["A"] == {
        "A": 1,
        "B": 1,
        MISSING_LABEL: 1,
        INVALID_LABEL: 0,
    }
    assert summary["per_label"]["A"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 2,
        "support": 3,
        "predicted_count": 2,
        "precision": 0.5,
        "recall": pytest.approx(1 / 3),
        "f1": 0.4,
    }
    assert summary["per_label"]["B"]["f1"] == 0.5
    assert summary["macro"]["f1"] == 0.45


def test_missing_prediction_for_present_label_uses_zero_precision():
    summary = classification_from_confusion(("A", "B"), {"A": {MISSING_LABEL: 2}})

    assert summary["per_label"]["A"]["precision"] == 0
    assert summary["per_label"]["A"]["recall"] == 0
    assert summary["per_label"]["A"]["f1"] == 0
    assert summary["per_label"]["B"]["precision"] is None
    assert summary["per_label"]["B"]["recall"] is None
    assert summary["per_label"]["B"]["f1"] is None
