"""Deterministic per-label metrics for token classification comparisons."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

MISSING_LABEL = "__missing__"
INVALID_LABEL = "__invalid__"
DUPLICATE_LABEL = "__duplicate__"
EXTRA_LABEL = "__extra__"
SPECIAL_LABELS = (MISSING_LABEL, INVALID_LABEL, DUPLICATE_LABEL, EXTRA_LABEL)


def classification_summary(
    labels: Sequence[str], observations: Iterable[tuple[str, str]]
) -> dict[str, Any]:
    """Return a confusion matrix and one-vs-rest metrics for declared labels."""

    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for gold_label, predicted_label in observations:
        confusion[str(gold_label)][str(predicted_label)] += 1
    return classification_from_confusion(labels, confusion)


def classification_from_confusion(
    labels: Sequence[str], confusion: Mapping[str, Mapping[str, int | float]]
) -> dict[str, Any]:
    """Normalize a confusion matrix and calculate per-label and macro metrics."""

    declared = tuple(dict.fromkeys(str(label) for label in labels))
    seen_gold = {str(label) for label in confusion}
    seen_predictions = {
        str(predicted) for row in confusion.values() for predicted, count in row.items() if count
    }
    gold_labels = _ordered_labels(declared, seen_gold)
    predicted_labels = _ordered_labels(declared, seen_predictions)
    normalized = {
        gold: {
            predicted: int(confusion.get(gold, {}).get(predicted, 0))
            for predicted in predicted_labels
        }
        for gold in gold_labels
    }
    per_label: dict[str, dict[str, int | float | None]] = {}
    for label in declared:
        true_positive = normalized.get(label, {}).get(label, 0)
        false_positive = sum(
            normalized.get(gold, {}).get(label, 0) for gold in gold_labels if gold != label
        )
        false_negative = sum(
            count for predicted, count in normalized.get(label, {}).items() if predicted != label
        )
        support = true_positive + false_negative
        predicted_count = true_positive + false_positive
        precision = (
            _safe_divide(true_positive, predicted_count)
            if predicted_count
            else 0.0
            if support
            else None
        )
        recall = _safe_divide(true_positive, support)
        f1 = _safe_divide(2 * true_positive, 2 * true_positive + false_positive + false_negative)
        per_label[label] = {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "support": support,
            "predicted_count": predicted_count,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    macro = {
        metric: _mean(
            value for values in per_label.values() if (value := values[metric]) is not None
        )
        for metric in ("precision", "recall", "f1")
    }
    return {
        "labels": list(declared),
        "gold_labels": gold_labels,
        "predicted_labels": predicted_labels,
        "confusion_matrix": normalized,
        "per_label": per_label,
        "macro": macro,
        "observations": sum(sum(row.values()) for row in normalized.values()),
    }


def _ordered_labels(declared: tuple[str, ...], seen: set[str]) -> list[str]:
    return [
        *declared,
        *(label for label in SPECIAL_LABELS if label in seen and label not in declared),
        *sorted(seen - set(declared) - set(SPECIAL_LABELS)),
    ]


def _safe_divide(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: Iterable[int | float]) -> float | None:
    collected = list(values)
    return sum(collected) / len(collected) if collected else None
