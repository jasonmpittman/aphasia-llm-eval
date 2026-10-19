"""Comparator for compact word and CIU index annotations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .numeric_fields import NumericFieldsComparator


class CIUTokenLabelsComparator:
    """Compare three-state token labels plus their derived count fields."""

    name = "ciu_token_labels"

    def compare(
        self,
        task: Mapping[str, Any],
        model_answer: Mapping[str, Any],
        gold_answer: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        numeric = NumericFieldsComparator()
        numeric_details, numeric_matched, numeric_statistics = numeric.compare(
            task, model_answer, gold_answer
        )
        token_count = gold_answer.get("token_count")
        token_count = token_count if _positive_integer(token_count) else 0
        model_words = set(_index_list(model_answer.get("word_indices")))
        model_cius = set(_index_list(model_answer.get("ciu_indices")))
        gold_words = set(_index_list(gold_answer.get("word_indices")))
        gold_cius = set(_index_list(gold_answer.get("ciu_indices")))
        valid_indices = set(range(1, token_count + 1))
        out_of_range = sorted((model_words | model_cius) - valid_indices)
        ciu_not_word = sorted(model_cius - model_words)

        token_details = []
        agreements = 0
        word_agreements = 0
        for index in range(1, token_count + 1):
            gold_label = _label(index, gold_words, gold_cius)
            model_label = _label(index, model_words, model_cius)
            agreed = gold_label == model_label
            word_agreed = (index in gold_words) == (index in model_words)
            agreements += int(agreed)
            word_agreements += int(word_agreed)
            token_details.append(
                {
                    "i": index,
                    "gold": gold_label,
                    "model": model_label,
                    "word_agreed": word_agreed,
                    "agreed": agreed,
                }
            )

        token_label_agreement = agreements / token_count if token_count else None
        word_label_agreement = word_agreements / token_count if token_count else None
        annotations_valid = not out_of_range and not ciu_not_word
        matched = bool(numeric_matched and annotations_valid and token_label_agreement == 1.0)
        details = {
            **numeric_details,
            "token_count": token_count,
            "tokens": token_details,
            "token_label_agreement": token_label_agreement,
            "word_label_agreement": word_label_agreement,
            "out_of_range_indices": out_of_range,
            "ciu_not_word_indices": ciu_not_word,
        }
        statistics = {
            **numeric_statistics,
            "token_label_agreement": token_label_agreement,
            "word_label_agreement": word_label_agreement,
            "alignment_problem": not annotations_valid,
        }
        return details, matched, statistics

    def validate_gold(
        self, task: Mapping[str, Any], gold_answer: Mapping[str, Any]
    ) -> tuple[str, ...]:
        errors: list[str] = []
        token_count = gold_answer.get("token_count")
        if not _positive_integer(token_count):
            errors.append("CIU token gold requires a positive token_count")
            return tuple(errors)
        for field in ("word_indices", "ciu_indices"):
            values = gold_answer.get(field)
            if not isinstance(values, list) or any(
                not _positive_integer(value) for value in values
            ):
                errors.append(f"CIU token gold requires {field} as positive integer indices")
            elif len(values) != len(set(values)):
                errors.append(f"CIU token gold {field} contains duplicate indices")
            elif any(value > token_count for value in values):
                errors.append(f"CIU token gold {field} contains an out-of-range index")
        words = set(_index_list(gold_answer.get("word_indices")))
        cius = set(_index_list(gold_answer.get("ciu_indices")))
        if not cius.issubset(words):
            errors.append("CIU token gold ciu_indices must be a subset of word_indices")
        errors.extend(NumericFieldsComparator().validate_gold(task, gold_answer))
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
            "token_annotations": True,
        }


def _index_list(value: Any) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [item for item in value if _positive_integer(item)]


def _positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _label(index: int, words: set[int], cius: set[int]) -> str:
    if index not in words:
        return "not_word"
    return "ciu" if index in cius else "not_ciu"
