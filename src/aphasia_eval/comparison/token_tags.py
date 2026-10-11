"""Comparator for indexed token-label answers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any


class TokenTagsComparator:
    """Compare model tags to gold indices without changing gold tokenization."""

    name = "token_tags"

    def compare(
        self,
        task: Mapping[str, Any],
        model_answer: Mapping[str, Any],
        gold_answer: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool, dict[str, Any]]:
        comparison = task["comparison"]
        index_field = comparison.get("index_field", "i")
        tag_field = comparison.get("tag_field", "pos")
        tagset = tuple(task.get("tagset", ()))
        allowed_tags = set(tagset)
        gold_rows = gold_answer.get("tags", [])
        model_rows = model_answer.get("tags", [])

        gold_by_index = {
            row[index_field]: row[tag_field]
            for row in gold_rows
            if isinstance(row, Mapping) and index_field in row and tag_field in row
        }
        model_by_index: dict[Any, list[Any]] = defaultdict(list)
        for row in model_rows:
            if isinstance(row, Mapping) and index_field in row:
                model_by_index[row[index_field]].append(row.get(tag_field))

        gold_indices = set(gold_by_index)
        model_indices = set(model_by_index)
        missing_indices = sorted(gold_indices - model_indices)
        extra_indices = sorted(
            index for index in model_indices - gold_indices if isinstance(index, int)
        )
        duplicate_indices = sorted(
            index
            for index, values in model_by_index.items()
            if len(values) > 1 and isinstance(index, int)
        )
        out_of_tagset = [
            {"i": index, "tag": tag}
            for index, values in model_by_index.items()
            for tag in values
            if tag not in allowed_tags
        ]

        if missing_indices:
            alignment_status = "missing"
        elif duplicate_indices:
            alignment_status = "duplicate"
        elif extra_indices:
            alignment_status = "extra"
        else:
            alignment_status = "complete"

        token_details = []
        agreements = 0
        for index in sorted(gold_indices):
            gold_tag = gold_by_index[index]
            values = model_by_index.get(index, [])
            model_tag = values[0] if len(values) == 1 else None
            agreed = bool(model_tag in allowed_tags and model_tag == gold_tag)
            agreements += int(agreed)
            token_details.append(
                {
                    "i": index,
                    "gold": gold_tag,
                    "model": model_tag,
                    "agreed": agreed,
                }
            )

        token_agreement = agreements / len(gold_by_index) if gold_by_index else None
        gold_counts = Counter(gold_by_index.values())
        model_counts = Counter(
            tag for values in model_by_index.values() for tag in values if tag in allowed_tags
        )
        valid_model_total = sum(model_counts.values())
        gold_total = sum(gold_counts.values())
        categories = {
            tag: {
                "model_count": model_counts[tag],
                "gold_count": gold_counts[tag],
                "signed_difference": model_counts[tag] - gold_counts[tag],
                "model_proportion": (
                    model_counts[tag] / valid_model_total if valid_model_total else None
                ),
                "gold_proportion": gold_counts[tag] / gold_total if gold_total else None,
            }
            for tag in tagset
        }
        derived_ratios = {
            declaration["name"]: {
                "model": model_answer.get(declaration["name"]),
                "gold": gold_answer.get(declaration["name"]),
            }
            for declaration in task.get("derived", ())
            if declaration.get("function") == "ratio"
        }
        matched = bool(
            token_agreement == 1.0 and alignment_status == "complete" and not out_of_tagset
        )
        category_statistics = {
            tag: {
                "absolute_count_difference": abs(values["signed_difference"]),
                "signed_count_difference": values["signed_difference"],
            }
            for tag, values in categories.items()
        }
        details = {
            "alignment_status": alignment_status,
            "missing_indices": missing_indices,
            "duplicate_indices": duplicate_indices,
            "extra_indices": extra_indices,
            "out_of_tagset": out_of_tagset,
            "token_agreement": token_agreement,
            "tokens": token_details,
            "categories": categories,
            "derived_ratios": derived_ratios,
        }
        statistics = {
            "token_agreement": token_agreement,
            "categories": category_statistics,
            "alignment_problem": alignment_status != "complete",
        }
        return details, matched, statistics

    def validate_gold(
        self, task: Mapping[str, Any], gold_answer: Mapping[str, Any]
    ) -> tuple[str, ...]:
        rows = gold_answer.get("tags")
        if not isinstance(rows, list) or not rows:
            return ("token_tags gold answer requires a non-empty tags list",)
        index_field = task["comparison"].get("index_field", "i")
        tag_field = task["comparison"].get("tag_field", "pos")
        tagset = set(task.get("tagset", ()))
        errors: list[str] = []
        seen: set[Any] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                errors.append("each gold tag must be an object")
                continue
            index = row.get(index_field)
            tag = row.get(tag_field)
            if not isinstance(index, int) or isinstance(index, bool) or index < 1:
                errors.append(f"gold tag index must be a positive integer, got {index!r}")
            elif index in seen:
                errors.append(f"duplicate gold tag index {index}")
            else:
                seen.add(index)
            if tag not in tagset:
                errors.append(f"gold tag {tag!r} is outside the declared tagset")
        return tuple(errors)

    def view_descriptor(self, task: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "type": self.name,
            "table": {"fields": ["token_agreement", "alignment_status"]},
            "chart": {
                "type": "gold_vs_model_category_bars",
                "categories": list(task.get("tagset", ())),
            },
        }
