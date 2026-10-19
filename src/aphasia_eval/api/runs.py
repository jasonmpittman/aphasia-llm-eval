"""Run creation, inspection, cancellation, deletion, and event streaming."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context
from sqlalchemy import func, select

from aphasia_eval.db import Aggregate, Result, Run, RunModel, RunTask
from aphasia_eval.extensions import (
    get_database,
    get_litellm_client,
    get_orchestrator,
    get_pricing_service,
)
from aphasia_eval.services.ablation import normalize_ablation, result_experiment
from aphasia_eval.services.model_capabilities import (
    load_capability_overrides,
    resolve_text_capability,
)
from aphasia_eval.services.orchestrator import (
    TERMINAL_RUN_STATUSES,
    RunEvent,
    request_count,
)
from aphasia_eval.services.task_loader import load_tasks

from .common import json_error

blueprint = Blueprint("runs_api", __name__)
INVALID_BOOLEAN = object()


@blueprint.post("/api/runs")
def create_run():
    """Validate, persist, and start one evaluation run."""

    body = request.get_json(silent=True)
    if not isinstance(body, Mapping):
        return json_error("invalid_request", "Request body must be a JSON object.", 400)
    error = _reject_unknown_keys(body, {"models", "tasks", "settings", "label"}, "request")
    if error:
        return error

    models, error = _string_list(body.get("models"), "models")
    if error:
        return error
    task_ids, error = _string_list(body.get("tasks"), "tasks")
    if error:
        return error
    settings, error = _normalize_settings(body.get("settings", {}))
    if error:
        return error
    label = body.get("label")
    if label is not None and (not isinstance(label, str) or len(label.strip()) > 200):
        return json_error(
            "invalid_request", "label must be a string of at most 200 characters.", 400
        )
    label = label.strip() or None if isinstance(label, str) else None

    discovery = get_litellm_client().list_models()
    available_models = set(discovery.models)
    unavailable_models = sorted(set(models) - available_models)
    if unavailable_models:
        return json_error(
            "invalid_models",
            f"Unknown or unavailable model(s): {', '.join(unavailable_models)}.",
            400,
        )
    overrides = load_capability_overrides(current_app.config["MODEL_CAPABILITIES_PATH"])
    incompatible_models = sorted(
        model
        for model in models
        if resolve_text_capability(model, discovery.metadata.get(model), overrides).status
        == "unsupported"
    )
    if incompatible_models:
        return json_error(
            "incompatible_models",
            "Model(s) explicitly marked incompatible with text analysis: "
            + ", ".join(incompatible_models),
            400,
        )

    task_results = load_tasks(current_app.config["TASKS_DIR"], current_app.config["GOLD_TASKS_DIR"])
    enabled_tasks = {
        result.task_id: result.task
        for result in task_results
        if result.enabled and result.task is not None
    }
    unavailable_tasks = sorted(set(task_ids) - enabled_tasks.keys())
    if unavailable_tasks:
        return json_error(
            "invalid_tasks",
            f"Unknown, invalid, or gold-less task(s): {', '.join(unavailable_tasks)}.",
            400,
        )
    selected_tasks = [enabled_tasks[task_id] for task_id in task_ids]
    total_requests = request_count(models, selected_tasks, settings)
    maximum = int(current_app.config["MAX_REQUESTS_PER_RUN"])
    if total_requests > maximum:
        return json_error(
            "request_limit_exceeded",
            f"Run expands to {total_requests} requests; maximum is {maximum}.",
            400,
        )

    get_pricing_service().refresh_proxy_pricing()
    run_id = get_orchestrator().create_run(
        models=models,
        tasks=selected_tasks,
        settings=settings,
        label=label,
    )
    return jsonify({"run_id": run_id}), 202


@blueprint.get("/api/runs")
def list_runs():
    """List run metadata newest first."""

    with get_database().session() as session:
        runs = list(session.scalars(select(Run).order_by(Run.created_at.desc()).limit(200)))
        payload = [_run_summary(session, run) for run in runs]
    return jsonify({"runs": payload})


@blueprint.get("/api/runs/<run_id>")
def get_run(run_id: str):
    """Return run metadata, provenance, progress, and cached aggregates."""

    with get_database().session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return json_error("not_found", "Run not found.", 404)
        payload = _run_detail(session, run)
    return jsonify(payload)


@blueprint.patch("/api/runs/<run_id>")
def update_run(run_id: str):
    """Update the user-editable label on a run."""

    body = request.get_json(silent=True)
    if not isinstance(body, Mapping) or set(body) != {"label"}:
        return json_error("invalid_request", "Body must contain only label.", 400)
    label = body["label"]
    if label is not None and (not isinstance(label, str) or len(label.strip()) > 200):
        return json_error(
            "invalid_request", "label must be null or a string of at most 200 characters.", 400
        )
    normalized = label.strip() or None if isinstance(label, str) else None
    with get_database().session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return json_error("not_found", "Run not found.", 404)
        run.label = normalized
    return jsonify({"run_id": run_id, "label": normalized})


@blueprint.get("/api/runs/<run_id>/items")
def list_run_items(run_id: str):
    """Return paginated item results with optional failure filters."""

    page = _positive_query_int("page", 1)
    per_page = _positive_query_int("per_page", 50, maximum=200)
    if page is None or per_page is None:
        return json_error("invalid_query", "page and per_page must be positive integers.", 400)
    parse_failures = _query_bool("parse_failures")
    disagreements = _query_bool("disagreements")
    alignment_problems = _query_bool("alignment_problems")
    if any(
        value is INVALID_BOOLEAN for value in (parse_failures, disagreements, alignment_problems)
    ):
        return json_error("invalid_query", "Boolean filters must be true or false.", 400)

    with get_database().session() as session:
        if session.get(Run, run_id) is None:
            return json_error("not_found", "Run not found.", 404)
        statement = select(Result).where(Result.run_id == run_id)
        count_statement = select(func.count(Result.id)).where(Result.run_id == run_id)
        expressions = []
        model_id = request.args.get("model")
        task_id = request.args.get("task")
        status = request.args.get("status")
        if model_id:
            expressions.append(Result.model_id == model_id)
        if task_id:
            expressions.append(Result.task_id == task_id)
        if status:
            expressions.append(Result.status == status)
        if parse_failures:
            expressions.append(Result.status == "parse_failure")
        if disagreements:
            expressions.extend((Result.status == "ok", Result.matched.is_(False)))
        if alignment_problems:
            expressions.append(
                Result.comparison_json["statistics"]["alignment_problem"].as_boolean().is_(True)
            )
        for expression in expressions:
            statement = statement.where(expression)
            count_statement = count_statement.where(expression)
        total = session.scalar(count_statement) or 0
        rows = list(
            session.scalars(
                statement.order_by(Result.id).offset((page - 1) * per_page).limit(per_page)
            )
        )
        items = [_serialize_result(row) for row in rows]
    return jsonify(
        {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": math.ceil(total / per_page) if total else 0,
            },
        }
    )


@blueprint.get("/api/runs/<run_id>/events")
def run_events(run_id: str):
    """Replay and stream ordered Server-Sent Events until the run is terminal."""

    with get_database().session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return json_error("not_found", "Run not found.", 404)
        terminal_status = run.status if run.status in TERMINAL_RUN_STATUSES else None
    try:
        after_id = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError:
        return json_error("invalid_query", "Last-Event-ID must be an integer.", 400)
    broker = get_orchestrator().events

    @stream_with_context
    def generate():
        all_events = broker.snapshot(run_id)
        if not all_events and terminal_status is not None:
            event_name = "run_cancelled" if terminal_status == "cancelled" else "run_completed"
            yield _format_sse(
                RunEvent(1, event_name, {"run_id": run_id, "status": terminal_status})
            )
            return
        for event in broker.stream(run_id, after_id):
            yield ": keep-alive\n\n" if event is None else _format_sse(event)

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@blueprint.post("/api/runs/<run_id>/cancel")
def cancel_run(run_id: str):
    """Request cooperative cancellation of an active run."""

    with get_database().session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return json_error("not_found", "Run not found.", 404)
        if run.status in TERMINAL_RUN_STATUSES:
            return json_error("run_not_active", "Run is already terminal.", 409)
    if not get_orchestrator().cancel(run_id):
        return json_error("run_not_active", "Run is not active in this process.", 409)
    return jsonify({"run_id": run_id, "status": "cancellation_requested"}), 202


@blueprint.delete("/api/runs/<run_id>")
def delete_run(run_id: str):
    """Delete one terminal run and all dependent rows."""

    with get_database().session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return json_error("not_found", "Run not found.", 404)
        if run.status not in TERMINAL_RUN_STATUSES:
            return json_error("run_active", "Cancel the active run before deleting it.", 409)
        session.delete(run)
    return "", 204


def _normalize_settings(value: Any):
    if not isinstance(value, Mapping):
        return None, json_error("invalid_request", "settings must be an object.", 400)
    error = _reject_unknown_keys(
        value,
        {"temperature", "max_tokens", "repetitions", "max_items_per_task", "ablation"},
        "settings",
    )
    if error:
        return None, error
    temperature = value.get("temperature", current_app.config["DEFAULT_TEMPERATURE"])
    max_tokens = value.get("max_tokens", current_app.config["DEFAULT_MAX_OUTPUT_TOKENS"])
    repetitions = value.get("repetitions", current_app.config["DEFAULT_REPETITIONS"])
    max_items = value.get("max_items_per_task")
    if (
        isinstance(temperature, bool)
        or not isinstance(temperature, int | float)
        or not math.isfinite(temperature)
        or temperature < 0
    ):
        return None, json_error(
            "invalid_request", "temperature must be a finite nonnegative number.", 400
        )
    for name, setting in (("max_tokens", max_tokens), ("repetitions", repetitions)):
        if isinstance(setting, bool) or not isinstance(setting, int) or setting < 1:
            return None, json_error("invalid_request", f"{name} must be a positive integer.", 400)
    if max_items is not None and (
        isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1
    ):
        return None, json_error(
            "invalid_request", "max_items_per_task must be null or a positive integer.", 400
        )
    ablation, ablation_error = normalize_ablation(
        value.get("ablation"), baseline_max_tokens=max_tokens
    )
    if ablation_error:
        return None, json_error("invalid_request", ablation_error + ".", 400)
    normalized = {
        "temperature": float(temperature),
        "max_tokens": max_tokens,
        "repetitions": repetitions,
        "max_items_per_task": max_items,
    }
    if ablation is not None:
        normalized["ablation"] = ablation
    return normalized, None


def _string_list(value: Any, name: str):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        return None, json_error(
            "invalid_request", f"{name} must be a non-empty list of strings.", 400
        )
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        return None, json_error("invalid_request", f"{name} must not contain duplicates.", 400)
    return normalized, None


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], label: str):
    unknown = sorted(set(value) - allowed)
    if unknown:
        return json_error(
            "invalid_request", f"Unknown {label} field(s): {', '.join(unknown)}.", 400
        )
    return None


def _run_summary(session, run: Run) -> dict[str, Any]:
    models = list(
        session.scalars(
            select(RunModel.model_id).where(RunModel.run_id == run.id).order_by(RunModel.model_id)
        )
    )
    tasks = list(
        session.scalars(
            select(RunTask.task_id).where(RunTask.run_id == run.id).order_by(RunTask.task_id)
        )
    )
    return {
        "id": run.id,
        "label": run.label,
        "status": run.status,
        "created_at": _isoformat(run.created_at),
        "started_at": _isoformat(run.started_at),
        "finished_at": _isoformat(run.finished_at),
        "models": models,
        "tasks": tasks,
        "total_requests": run.settings_json.get("total_requests"),
    }


def _run_detail(session, run: Run) -> dict[str, Any]:
    models = list(
        session.scalars(
            select(RunModel).where(RunModel.run_id == run.id).order_by(RunModel.model_id)
        )
    )
    tasks = list(
        session.scalars(select(RunTask).where(RunTask.run_id == run.id).order_by(RunTask.task_id))
    )
    aggregates = list(
        session.scalars(
            select(Aggregate)
            .where(Aggregate.run_id == run.id)
            .order_by(Aggregate.model_id, Aggregate.task_id, Aggregate.stat_name, Aggregate.field)
        )
    )
    status_counts = dict(
        session.execute(
            select(Result.status, func.count(Result.id))
            .where(Result.run_id == run.id)
            .group_by(Result.status)
        ).all()
    )
    finished = sum(status_counts.values())
    return {
        "run": {
            **_run_summary(session, run),
            "settings": run.settings_json,
            "app_version": run.app_version,
            "litellm_host": run.litellm_host,
            "notes": run.notes,
        },
        "models": [
            {"model_id": model.model_id, "pricing_source": model.pricing_source_summary}
            for model in models
        ],
        "tasks": [
            {
                "task_id": task.task_id,
                "version": task.task_version,
                "task_file_sha256": task.task_file_sha256,
                "gold_file_sha256": task.gold_file_sha256,
                "prompt_sha256": task.prompt_sha256,
            }
            for task in tasks
        ],
        "progress": {
            "finished": finished,
            "total": run.settings_json.get("total_requests", 0),
            "by_status": status_counts,
        },
        "aggregates": [
            {
                "model_id": aggregate.model_id,
                "task_id": aggregate.task_id,
                "condition_id": aggregate.condition_id,
                "stat_name": aggregate.stat_name,
                "field": aggregate.field,
                "value": aggregate.value,
                "n": aggregate.n,
            }
            for aggregate in aggregates
        ],
        "ablation": _ablation_detail(run.settings_json, aggregates),
    }


def _serialize_result(result: Result) -> dict[str, Any]:
    experiment = result_experiment(result.comparison_json, result.repetition)
    return {
        "id": result.id,
        "run_id": result.run_id,
        "model_id": result.model_id,
        "task_id": result.task_id,
        "item_id": result.item_id,
        "repetition": experiment["repetition"],
        "condition_id": experiment["condition_id"],
        "condition": experiment,
        "input": result.input_json,
        "gold": result.gold_json,
        "prompt_rendered": result.prompt_rendered,
        "raw_output": result.raw_output,
        "parsed_output": result.parsed_output_json,
        "derived": result.derived_json,
        "comparison": result.comparison_json,
        "matched": result.matched,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "tokens_estimated": result.tokens_estimated,
        "cost_usd": result.cost_usd,
        "cost_source": result.cost_source,
        "latency_ms": result.latency_ms,
        "total_elapsed_ms": result.total_elapsed_ms,
        "attempts": result.attempts,
        "status": result.status,
        "error_message": result.error_message,
        "created_at": _isoformat(result.created_at),
    }


def _ablation_detail(settings: Mapping[str, Any], aggregates: list[Aggregate]) -> dict[str, Any]:
    ablation = settings.get("ablation")
    if not isinstance(ablation, Mapping):
        return {
            "enabled": False,
            "conditions": [],
            "summaries": [],
            "best_conditions": [],
            "closest_conditions": [],
        }
    aggregate_lookup = {
        (
            aggregate.model_id,
            aggregate.task_id,
            aggregate.condition_id,
            aggregate.stat_name,
            aggregate.field,
        ): aggregate.value
        for aggregate in aggregates
    }
    groups = sorted(
        {
            (aggregate.model_id, aggregate.task_id, aggregate.condition_id)
            for aggregate in aggregates
        }
    )
    conditions = ablation.get("conditions", [])
    condition_lookup = {
        condition["condition_id"]: condition
        for condition in conditions
        if isinstance(condition, Mapping) and "condition_id" in condition
    }
    task_headlines = ablation.get("task_headlines", {})
    summaries: list[dict[str, Any]] = []
    for model_id, task_id, condition_id in groups:
        headline = task_headlines.get(task_id, {})
        headline_field = headline.get("field") if isinstance(headline, Mapping) else None

        def metric(
            name: str,
            field: str | None = None,
            group: tuple[str, str, str] = (model_id, task_id, condition_id),
        ):
            return aggregate_lookup.get((*group, name, field))

        condition = condition_lookup.get(condition_id, {})
        token_agreement = metric("token_agreement")
        if token_agreement is None:
            token_agreement = metric("token_label_agreement")
        summaries.append(
            {
                "model_id": model_id,
                "task_id": task_id,
                "condition_id": condition_id,
                "label": condition.get("label", condition_id),
                "parameter": condition.get("parameter"),
                "value": condition.get("value"),
                "is_baseline": bool(condition.get("is_baseline", False)),
                "items_run": metric("items_run"),
                "comparable_response_rate": metric("comparable_response_rate"),
                "parse_failures": metric("parse_failures"),
                "errors": metric("errors"),
                "truncated_responses": metric("truncated_responses"),
                "optional_parameters_removed": metric("optional_parameters_removed"),
                "headline": metric("headline", headline_field),
                "headline_stddev": metric("headline_stddev", headline_field),
                "headline_field": headline_field,
                "headline_better": headline.get("better", "lower"),
                "token_agreement": token_agreement,
                "estimated_cost_usd": metric("estimated_cost_usd"),
                "mean_latency_ms": metric("mean_latency_ms"),
            }
        )
    best_conditions = []
    closest_conditions = []
    pairs = sorted({(row["model_id"], row["task_id"]) for row in summaries})
    for model_id, task_id in pairs:
        candidates = [
            row for row in summaries if row["model_id"] == model_id and row["task_id"] == task_id
        ]
        best = min(candidates, key=_ablation_selection_key)
        best_conditions.append(
            {
                "model_id": model_id,
                "task_id": task_id,
                "condition_id": best["condition_id"],
            }
        )
        baseline = next((row for row in candidates if row["is_baseline"]), None)
        valid_conditions = [
            row
            for row in candidates
            if row["headline"] is not None and (row["comparable_response_rate"] or 0) > 0
        ]
        closest_conditions.append(
            {
                "model_id": model_id,
                "task_id": task_id,
                "condition_id": best["condition_id"],
                "condition_label": best["label"],
                "condition_value": best["value"],
                "has_comparable_result": best in valid_conditions,
                "headline": best["headline"],
                "headline_field": best["headline_field"],
                "headline_better": best["headline_better"],
                "comparable_response_rate": best["comparable_response_rate"],
                "valid_condition_count": len(valid_conditions),
                "condition_count": len(candidates),
                "baseline_has_comparable_result": bool(
                    baseline
                    and baseline["headline"] is not None
                    and (baseline["comparable_response_rate"] or 0) > 0
                ),
                "baseline_headline": baseline["headline"] if baseline else None,
            }
        )
    return {
        "enabled": True,
        "parameter": ablation.get("parameter"),
        "baseline_value": ablation.get("baseline_value"),
        "conditions": conditions,
        "selection_rule": ablation.get("selection_rule"),
        "summaries": summaries,
        "best_conditions": best_conditions,
        "closest_conditions": closest_conditions,
        "same_dataset_warning": (
            "Closest valid observed conditions were selected on these same gold items and are "
            "exploratory, not held-out performance estimates."
        ),
    }


def _ablation_selection_key(summary: Mapping[str, Any]) -> tuple[Any, ...]:
    coverage = summary.get("comparable_response_rate")
    coverage_key = -float(coverage) if coverage is not None else math.inf
    headline = summary.get("headline")
    if headline is None:
        headline_key = math.inf
    else:
        headline_key = float(headline)
        if summary.get("headline_better") == "higher":
            headline_key = -headline_key
    cost = summary.get("estimated_cost_usd")
    latency = summary.get("mean_latency_ms")
    return (
        coverage_key,
        headline_key,
        float(cost) if cost is not None else math.inf,
        float(latency) if latency is not None else math.inf,
        0 if summary.get("is_baseline") else 1,
    )


def _positive_query_int(name: str, default: int, maximum: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 1 or (maximum is not None and value > maximum):
        return None
    return value


def _query_bool(name: str) -> bool | None | object:
    raw = request.args.get(name)
    if raw is None:
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return INVALID_BOOLEAN


def _format_sse(event: RunEvent) -> str:
    return f"id: {event.id}\nevent: {event.event}\ndata: {json.dumps(event.data)}\n\n"


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
