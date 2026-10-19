"""Recompute cached per-model, per-task run aggregates."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from aphasia_eval.comparison.classification import classification_from_confusion
from aphasia_eval.db import Aggregate, Database, Result, RunModel
from aphasia_eval.services.ablation import result_experiment
from aphasia_eval.services.task_loader import TaskDefinition


@dataclass(frozen=True, slots=True)
class AggregateValue:
    """One statistic ready to persist."""

    name: str
    value: float | None
    n: int
    field: str | None = None


def recompute_run_aggregates(
    database: Database,
    run_id: str,
    tasks: Mapping[str, TaskDefinition],
) -> None:
    """Replace all cached aggregates for a run from its persisted results."""

    with database.session() as session:
        results = list(
            session.scalars(
                select(Result)
                .where(Result.run_id == run_id)
                .order_by(Result.model_id, Result.task_id, Result.id)
            )
        )
        session.execute(delete(Aggregate).where(Aggregate.run_id == run_id))
        grouped: dict[tuple[str, str, str], list[Result]] = defaultdict(list)
        for result in results:
            condition_id = result_experiment(result.comparison_json, result.repetition)[
                "condition_id"
            ]
            grouped[(result.model_id, result.task_id, condition_id)].append(result)

        for (model_id, task_id, condition_id), group in grouped.items():
            task = tasks[task_id]
            for aggregate in _aggregate_group(group, task.config):
                session.add(
                    Aggregate(
                        run_id=run_id,
                        model_id=model_id,
                        task_id=task_id,
                        condition_id=condition_id,
                        stat_name=aggregate.name,
                        field=aggregate.field,
                        value=aggregate.value,
                        n=aggregate.n,
                    )
                )

        by_model: dict[str, set[str]] = defaultdict(set)
        for result in results:
            by_model[result.model_id].add(result.cost_source)
        for run_model in session.scalars(select(RunModel).where(RunModel.run_id == run_id)):
            sources = by_model.get(run_model.model_id, set())
            run_model.pricing_source_summary = (
                next(iter(sources)) if len(sources) == 1 else "mixed" if sources else "unknown"
            )


def _aggregate_group(results: Sequence[Result], task: Mapping[str, Any]) -> list[AggregateValue]:
    active = [result for result in results if result.status != "cancelled"]
    comparable = [
        result
        for result in active
        if result.status == "ok" and isinstance(result.comparison_json, Mapping)
    ]
    values = [
        AggregateValue("items_run", float(len(active)), len(active)),
        AggregateValue(
            "parse_failures",
            float(sum(result.status == "parse_failure" for result in active)),
            len(active),
        ),
        AggregateValue(
            "errors", float(sum(result.status == "error" for result in active)), len(active)
        ),
        AggregateValue(
            "item_match_rate",
            _safe_divide(sum(result.matched is True for result in active), len(active)),
            len(active),
        ),
        AggregateValue(
            "comparable_response_rate",
            _safe_divide(len(comparable), len(active)),
            len(active),
        ),
        AggregateValue(
            "truncated_responses",
            float(sum(_diagnostic_value(result, "error_code") == "truncated" for result in active)),
            len(active),
        ),
        AggregateValue(
            "optional_parameters_removed",
            float(
                sum(
                    result_experiment(result.comparison_json, result.repetition)[
                        "optional_parameters_removed"
                    ]
                    for result in active
                )
            ),
            len(active),
        ),
    ]
    values.extend(_efficiency_values(active))

    comparison_type = task["comparison"]["type"]
    if comparison_type in {"numeric_fields", "ciu_token_labels"}:
        values.extend(_numeric_values(comparable, task))
        if comparison_type == "ciu_token_labels":
            agreements = [
                result.comparison_json["statistics"]["token_label_agreement"]
                for result in comparable
                if result.comparison_json["statistics"]["token_label_agreement"] is not None
            ]
            values.append(
                AggregateValue("token_label_agreement", _mean(agreements), len(agreements))
            )
            values.extend(_classification_values(comparable, ("not_word", "not_ciu", "ciu")))
    elif comparison_type == "token_tags":
        values.extend(_token_values(comparable, task))
        values.extend(_classification_values(comparable, tuple(task.get("tagset", ()))))

    headline_values = _headline_by_repetition(comparable, task)
    headline_field = task["headline"].get("field")
    values.append(
        AggregateValue(
            "headline",
            _mean(headline_values),
            len(headline_values),
            headline_field,
        )
    )
    if len(headline_values) > 1:
        values.append(
            AggregateValue(
                "headline_stddev",
                statistics.pstdev(headline_values),
                len(headline_values),
                headline_field,
            )
        )
    return values


def _efficiency_values(results: Sequence[Result]) -> list[AggregateValue]:
    input_tokens = [result.input_tokens for result in results if result.input_tokens is not None]
    output_tokens = [result.output_tokens for result in results if result.output_tokens is not None]
    reasoning = [
        result.reasoning_tokens for result in results if result.reasoning_tokens is not None
    ]
    costs = [result.cost_usd for result in results if result.cost_usd is not None]
    latencies = [result.latency_ms for result in results if result.latency_ms is not None]
    elapsed = [result.total_elapsed_ms for result in results if result.total_elapsed_ms is not None]
    return [
        AggregateValue("input_tokens", float(sum(input_tokens)), len(input_tokens)),
        AggregateValue("output_tokens", float(sum(output_tokens)), len(output_tokens)),
        AggregateValue("reasoning_tokens", float(sum(reasoning)), len(reasoning)),
        AggregateValue("estimated_cost_usd", float(sum(costs)) if costs else None, len(costs)),
        AggregateValue("mean_latency_ms", _mean(latencies), len(latencies)),
        AggregateValue("p95_latency_ms", _percentile(latencies, 0.95), len(latencies)),
        AggregateValue("total_elapsed_ms", float(sum(elapsed)), len(elapsed)),
    ]


def _diagnostic_value(result: Result, name: str) -> Any:
    comparison = result.comparison_json
    if not isinstance(comparison, Mapping):
        return None
    diagnostics = comparison.get("diagnostics")
    return diagnostics.get(name) if isinstance(diagnostics, Mapping) else None


def _numeric_values(results: Sequence[Result], task: Mapping[str, Any]) -> list[AggregateValue]:
    values: list[AggregateValue] = []
    for field in task["comparison"]["fields"]:
        field_stats = [result.comparison_json["statistics"]["fields"][field] for result in results]
        absolute = [
            stats["absolute_difference"]
            for stats in field_stats
            if stats["absolute_difference"] is not None
        ]
        signed = [
            stats["signed_difference"]
            for stats in field_stats
            if stats["signed_difference"] is not None
        ]
        exact = [bool(stats["exact_match"]) for stats in field_stats]
        values.extend(
            (
                AggregateValue("mean_abs_diff", _mean(absolute), len(absolute), field),
                AggregateValue("mean_signed_diff", _mean(signed), len(signed), field),
                AggregateValue(
                    "exact_match_rate",
                    _safe_divide(sum(exact), len(exact)),
                    len(exact),
                    field,
                ),
            )
        )
    return values


def _token_values(results: Sequence[Result], task: Mapping[str, Any]) -> list[AggregateValue]:
    agreements = [
        result.comparison_json["statistics"]["token_agreement"]
        for result in results
        if result.comparison_json["statistics"]["token_agreement"] is not None
    ]
    values = [AggregateValue("token_agreement", _mean(agreements), len(agreements))]
    for category in task.get("tagset", ()):
        category_stats = [
            result.comparison_json["statistics"]["categories"][category] for result in results
        ]
        absolute = [stats["absolute_count_difference"] for stats in category_stats]
        signed = [stats["signed_count_difference"] for stats in category_stats]
        values.extend(
            (
                AggregateValue("mean_abs_count_diff", _mean(absolute), len(absolute), category),
                AggregateValue("mean_signed_count_diff", _mean(signed), len(signed), category),
            )
        )
    return values


def _classification_values(
    results: Sequence[Result], labels: Sequence[str]
) -> list[AggregateValue]:
    classifications = [
        classification
        for result in results
        if (classification := _result_classification(result)) is not None
    ]
    if not classifications:
        return []
    pooled = classification_from_confusion(labels, _sum_confusions(classifications))
    values = _classification_summary_values(pooled, len(classifications))

    by_repetition: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for result in results:
        classification = _result_classification(result)
        if classification is None:
            continue
        repetition = result_experiment(result.comparison_json, result.repetition)["repetition"]
        by_repetition[int(repetition)].append(classification)
    repetition_summaries = [
        classification_from_confusion(labels, _sum_confusions(group))
        for group in by_repetition.values()
    ]
    values.append(
        AggregateValue(
            "classification_repetitions",
            float(len(repetition_summaries)),
            len(repetition_summaries),
        )
    )
    for label in labels:
        for metric in ("precision", "recall", "f1"):
            observations = [
                float(value)
                for summary in repetition_summaries
                if (value := summary["per_label"][label][metric]) is not None
            ]
            values.append(
                AggregateValue(
                    f"{metric}_repetition_mean", _mean(observations), len(observations), label
                )
            )
            if len(observations) > 1:
                values.append(
                    AggregateValue(
                        f"{metric}_repetition_stddev",
                        statistics.pstdev(observations),
                        len(observations),
                        label,
                    )
                )
    return values


def _classification_summary_values(
    summary: Mapping[str, Any], result_count: int
) -> list[AggregateValue]:
    values = [
        AggregateValue(
            "classification_observations",
            float(summary["observations"]),
            result_count,
        )
    ]
    for metric, value in summary["macro"].items():
        defined = sum(
            label_metrics[metric] is not None for label_metrics in summary["per_label"].values()
        )
        values.append(
            AggregateValue(
                f"macro_{metric}",
                float(value) if value is not None else None,
                defined,
            )
        )
    for label, metrics in summary["per_label"].items():
        for metric in ("precision", "recall", "f1"):
            value = metrics[metric]
            values.append(
                AggregateValue(
                    metric,
                    float(value) if value is not None else None,
                    int(
                        metrics["predicted_count"] if metric == "precision" else metrics["support"]
                    ),
                    label,
                )
            )
        values.append(AggregateValue("support", float(metrics["support"]), result_count, label))
    for gold_label, row in summary["confusion_matrix"].items():
        for predicted_label, count in row.items():
            if count:
                values.append(
                    AggregateValue(
                        "confusion",
                        float(count),
                        result_count,
                        f"{gold_label} → {predicted_label}",
                    )
                )
    return values


def _result_classification(result: Result) -> Mapping[str, Any] | None:
    comparison = result.comparison_json
    if not isinstance(comparison, Mapping):
        return None
    statistics_value = comparison.get("statistics")
    if not isinstance(statistics_value, Mapping):
        return None
    classification = statistics_value.get("classification")
    return classification if isinstance(classification, Mapping) else None


def _sum_confusions(
    classifications: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for classification in classifications:
        confusion = classification.get("confusion_matrix", {})
        if not isinstance(confusion, Mapping):
            continue
        for gold_label, row in confusion.items():
            if not isinstance(row, Mapping):
                continue
            for predicted_label, count in row.items():
                if isinstance(count, int | float) and not isinstance(count, bool):
                    totals[str(gold_label)][str(predicted_label)] += int(count)
    return {gold: dict(row) for gold, row in totals.items()}


def _headline_by_repetition(results: Sequence[Result], task: Mapping[str, Any]) -> list[float]:
    grouped: dict[int, list[Result]] = defaultdict(list)
    for result in results:
        repetition = result_experiment(result.comparison_json, result.repetition)["repetition"]
        grouped[int(repetition)].append(result)
    headline = task["headline"]
    values: list[float] = []
    for repetition_results in grouped.values():
        if headline["stat"] == "mean_abs_diff":
            field = headline["field"]
            observations = [
                result.comparison_json["statistics"]["fields"][field]["absolute_difference"]
                for result in repetition_results
            ]
        elif headline["stat"] == "token_agreement":
            observations = [
                result.comparison_json["statistics"]["token_agreement"]
                for result in repetition_results
            ]
        else:
            observations = []
        numeric = [float(value) for value in observations if value is not None]
        if numeric:
            values.append(statistics.fmean(numeric))
    return values


def _mean(values: Sequence[float | int]) -> float | None:
    return statistics.fmean(values) if values else None


def _safe_divide(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: Sequence[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)
