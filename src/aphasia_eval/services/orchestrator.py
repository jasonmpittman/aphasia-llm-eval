"""Concurrent, failure-isolated execution of persisted evaluation runs."""

from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from aphasia_eval.comparison import compare_answers
from aphasia_eval.db import Database, Result, Run, RunModel, RunTask
from aphasia_eval.services.ablation import ExperimentCondition, experiment_conditions
from aphasia_eval.services.aggregation import recompute_run_aggregates
from aphasia_eval.services.gold_loader import GoldItem
from aphasia_eval.services.litellm_client import LiteLLMClient
from aphasia_eval.services.parsing import parse_model_output
from aphasia_eval.services.pricing import PricingService
from aphasia_eval.services.task_loader import TaskDefinition, render_prompt

TERMINAL_RUN_STATUSES = frozenset({"completed", "cancelled", "failed", "interrupted"})
TERMINAL_EVENT_TYPES = frozenset({"run_completed", "run_cancelled"})


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One ordered progress event."""

    id: int
    event: str
    data: dict[str, Any]


class EventBroker:
    """Keep replayable in-process events and wake SSE listeners."""

    def __init__(self) -> None:
        self._events: dict[str, list[RunEvent]] = defaultdict(list)
        self._condition = threading.Condition()

    def publish(self, run_id: str, event: str, data: Mapping[str, Any]) -> RunEvent:
        with self._condition:
            item = RunEvent(
                id=len(self._events[run_id]) + 1,
                event=event,
                data={"run_id": run_id, **dict(data)},
            )
            self._events[run_id].append(item)
            self._condition.notify_all()
            return item

    def snapshot(self, run_id: str, after_id: int = 0) -> tuple[RunEvent, ...]:
        with self._condition:
            return tuple(event for event in self._events.get(run_id, ()) if event.id > after_id)

    def stream(
        self, run_id: str, after_id: int = 0, heartbeat_seconds: float = 15.0
    ) -> Iterator[RunEvent | None]:
        """Yield new events and ``None`` heartbeats until a terminal event."""

        cursor = max(after_id, 0)
        while True:
            heartbeat = False
            with self._condition:
                events = self._events.get(run_id, ())
                pending = [event for event in events if event.id > cursor]
                if not pending:
                    if events and events[-1].event in TERMINAL_EVENT_TYPES:
                        return
                    notified = self._condition.wait(timeout=heartbeat_seconds)
                    if not notified:
                        heartbeat = True
            if heartbeat:
                yield None
                continue
            if not pending:
                continue
            for event in pending:
                cursor = event.id
                yield event
                if event.event in TERMINAL_EVENT_TYPES:
                    return


@dataclass(frozen=True, slots=True)
class Job:
    """One expanded model/task/item/repetition operation."""

    run_id: str
    model_id: str
    task: TaskDefinition
    item: GoldItem
    repetition: int
    storage_repetition: int
    condition: ExperimentCondition
    settings: Mapping[str, Any]


class RunOrchestrator:
    """Create, execute, cancel, and report on evaluation runs."""

    def __init__(
        self,
        *,
        database: Database,
        client: LiteLLMClient,
        pricing: PricingService,
        app_version: str,
        litellm_host: str,
        max_concurrency_total: int,
        max_concurrency_per_model: int,
        synchronous: bool = False,
        events: EventBroker | None = None,
    ) -> None:
        if max_concurrency_total < 1 or max_concurrency_per_model < 1:
            raise ValueError("concurrency limits must be positive")
        self.database = database
        self.client = client
        self.pricing = pricing
        self.app_version = app_version
        self.litellm_host = litellm_host
        self.max_concurrency_total = max_concurrency_total
        self.max_concurrency_per_model = max_concurrency_per_model
        self.synchronous = synchronous
        self.events = events or EventBroker()
        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}

    def create_run(
        self,
        *,
        models: Sequence[str],
        tasks: Sequence[TaskDefinition],
        settings: Mapping[str, Any],
        label: str | None,
    ) -> str:
        """Persist a reproducible run definition and begin its execution."""

        run_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        persisted_settings = dict(settings)
        conditions = experiment_conditions(settings)
        ablation = persisted_settings.get("ablation")
        if isinstance(ablation, Mapping):
            persisted_settings["ablation"] = {
                **dict(ablation),
                "conditions": [condition.as_dict() for condition in conditions],
                "task_headlines": {task.task_id: dict(task.config["headline"]) for task in tasks},
                "selection_rule": (
                    "Highest comparable-response rate, then closest task headline to gold, "
                    "then lower estimated cost and latency."
                ),
            }
        persisted_settings.update(
            {
                "max_concurrency_total": self.max_concurrency_total,
                "max_concurrency_per_model": self.max_concurrency_per_model,
                "total_requests": _request_count(models, tasks, settings),
            }
        )
        with self.database.session() as session:
            session.add(
                Run(
                    id=run_id,
                    label=label,
                    status="queued",
                    created_at=created_at,
                    started_at=None,
                    finished_at=None,
                    settings_json=persisted_settings,
                    app_version=self.app_version,
                    litellm_host=self.litellm_host,
                    notes=None,
                )
            )
            session.add_all(
                RunModel(run_id=run_id, model_id=model, pricing_source_summary=None)
                for model in models
            )
            for task in tasks:
                assert task.gold is not None
                session.add(
                    RunTask(
                        run_id=run_id,
                        task_id=task.task_id,
                        task_version=task.config["version"],
                        task_file_sha256=task.task_sha256,
                        gold_file_sha256=task.gold.sha256,
                        prompt_sha256=task.prompt_sha256,
                    )
                )

        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[run_id] = cancel_event
        arguments = (run_id, tuple(models), tuple(tasks), dict(settings), cancel_event)
        if self.synchronous:
            self._execute_run(*arguments)
        else:
            thread = threading.Thread(
                target=self._execute_run,
                args=arguments,
                name=f"aphasia-eval-{run_id[:8]}",
                daemon=True,
            )
            with self._lock:
                self._threads[run_id] = thread
            thread.start()
        return run_id

    def cancel(self, run_id: str) -> bool:
        """Request cooperative cancellation for an active run."""

        with self._lock:
            event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def wait(self, run_id: str, timeout: float | None = None) -> None:
        """Wait for an asynchronous run; primarily useful to tests and shutdown code."""

        with self._lock:
            thread = self._threads.get(run_id)
        if thread is not None:
            thread.join(timeout)

    def _execute_run(
        self,
        run_id: str,
        models: Sequence[str],
        tasks: Sequence[TaskDefinition],
        settings: Mapping[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        task_map = {task.task_id: task for task in tasks}
        jobs = _expand_jobs(run_id, models, tasks, settings)
        totals = {
            "total": len(jobs),
            "finished": 0,
            "ok": 0,
            "parse_failures": 0,
            "failed": 0,
            "cancelled": 0,
        }
        try:
            self._update_run(run_id, status="running", started_at=datetime.now(UTC))
            self.events.publish(run_id, "run_started", totals)
            semaphores = {
                model: threading.BoundedSemaphore(self.max_concurrency_per_model)
                for model in models
            }
            with ThreadPoolExecutor(
                max_workers=self.max_concurrency_total,
                thread_name_prefix=f"run-{run_id[:8]}",
            ) as executor:
                futures = {
                    executor.submit(
                        self._execute_job_guarded,
                        job,
                        semaphores[job.model_id],
                        cancel_event,
                    ): job
                    for job in jobs
                }
                for future in as_completed(futures):
                    job = futures[future]
                    status = future.result()
                    totals["finished"] += 1
                    if status == "ok":
                        totals["ok"] += 1
                        event_name = "job_completed"
                    elif status == "parse_failure":
                        totals["parse_failures"] += 1
                        event_name = "job_failed"
                    elif status == "cancelled":
                        totals["cancelled"] += 1
                        event_name = "run_progress"
                    else:
                        totals["failed"] += 1
                        event_name = "job_failed"
                    event_data = {
                        "model_id": job.model_id,
                        "task_id": job.task.task_id,
                        "item_id": job.item.item_id,
                        "repetition": job.repetition,
                        "condition_id": job.condition.condition_id,
                        "status": status,
                        **totals,
                    }
                    self.events.publish(run_id, event_name, event_data)
                    if event_name != "run_progress":
                        self.events.publish(run_id, "run_progress", event_data)

            recompute_run_aggregates(self.database, run_id, task_map)
            final_status = "cancelled" if cancel_event.is_set() else "completed"
            self._update_run(run_id, status=final_status, finished_at=datetime.now(UTC))
            final_event = "run_cancelled" if final_status == "cancelled" else "run_completed"
            self.events.publish(run_id, final_event, {"status": final_status, **totals})
        except Exception:
            self._update_run(
                run_id,
                status="failed",
                finished_at=datetime.now(UTC),
                notes="Internal orchestration failure.",
            )
            self.events.publish(run_id, "run_completed", {"status": "failed", **totals})
        finally:
            with self._lock:
                self._cancel_events.pop(run_id, None)
                self._threads.pop(run_id, None)

    def _execute_job_guarded(
        self,
        job: Job,
        model_semaphore: threading.BoundedSemaphore,
        cancel_event: threading.Event,
    ) -> str:
        try:
            if cancel_event.is_set():
                return self._persist_cancelled(job)
            with model_semaphore:
                if cancel_event.is_set():
                    return self._persist_cancelled(job)
                return self._execute_job(job)
        except Exception:
            return self._persist_unexpected_error(job)

    def _execute_job(self, job: Job) -> str:
        prompt = render_prompt(job.task.config, job.item)
        completion = self.client.chat_completion(
            model=job.model_id,
            messages=(
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ),
            temperature=job.settings["temperature"],
            max_tokens=job.settings["max_tokens"],
        )
        common = {
            "prompt_rendered": {"system": prompt.system, "user": prompt.user},
            "raw_output": completion.raw_text,
            "input_tokens": completion.prompt_tokens,
            "output_tokens": completion.completion_tokens,
            "reasoning_tokens": completion.reasoning_tokens,
            "tokens_estimated": completion.tokens_estimated,
            "latency_ms": completion.latency_ms,
            "total_elapsed_ms": completion.total_elapsed_ms,
            "attempts": completion.attempts,
        }
        diagnostics = dict(completion.diagnostics)
        diagnostics["optional_parameters_removed"] = completion.optional_parameters_removed
        if completion.error is not None:
            diagnostics["error_code"] = completion.error.code
            self._persist_result(
                job,
                status="error",
                error_message=completion.error.message,
                comparison_json={"diagnostics": diagnostics},
                cost_source="unknown",
                **common,
            )
            return "error"

        assert completion.raw_text is not None
        assert completion.prompt_tokens is not None
        assert completion.completion_tokens is not None
        cost = self.pricing.resolve(
            model=job.model_id,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            response_headers=completion.cost_headers,
        )
        parsed = parse_model_output(completion.raw_text, job.task.config["output"]["schema"])
        if not parsed.ok:
            assert parsed.error is not None
            self._persist_result(
                job,
                status="parse_failure",
                error_message=parsed.error.message,
                comparison_json={"diagnostics": diagnostics},
                matched=False,
                cost_usd=cost.cost_usd,
                cost_source=cost.cost_source,
                **common,
            )
            return "parse_failure"

        assert parsed.value is not None
        outcome = compare_answers(
            job.task.config,
            parsed.value,
            job.item.gold,
            job.item.inputs,
        )
        self._persist_result(
            job,
            status="ok",
            parsed_output_json=parsed.value,
            derived_json={"model": outcome.model_derived, "gold": outcome.gold_derived},
            comparison_json={
                "details": outcome.details,
                "statistics": outcome.statistics,
                "view_descriptor": outcome.view_descriptor,
                "diagnostics": diagnostics,
            },
            matched=outcome.matched,
            cost_usd=cost.cost_usd,
            cost_source=cost.cost_source,
            **common,
        )
        return "ok"

    def _persist_cancelled(self, job: Job) -> str:
        self._persist_result(job, status="cancelled", error_message="Cancelled before request.")
        return "cancelled"

    def _persist_unexpected_error(self, job: Job) -> str:
        with self.database.session() as session:
            existing = session.scalar(
                select(Result.id).where(
                    Result.run_id == job.run_id,
                    Result.model_id == job.model_id,
                    Result.task_id == job.task.task_id,
                    Result.item_id == job.item.item_id,
                    Result.repetition == job.storage_repetition,
                )
            )
            if existing is None:
                session.add(
                    _result_record(
                        job,
                        status="error",
                        error_message="Job failed unexpectedly.",
                    )
                )
        return "error"

    def _persist_result(self, job: Job, *, status: str, **values: Any) -> None:
        with self.database.session() as session:
            session.add(_result_record(job, status=status, **values))

    def _update_run(self, run_id: str, **values: Any) -> None:
        with self.database.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                return
            for name, value in values.items():
                setattr(run, name, value)


def _result_record(job: Job, *, status: str, **values: Any) -> Result:
    defaults: dict[str, Any] = {
        "prompt_rendered": None,
        "raw_output": None,
        "parsed_output_json": None,
        "derived_json": None,
        "comparison_json": None,
        "matched": None,
        "input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "tokens_estimated": False,
        "cost_usd": None,
        "cost_source": "unknown",
        "latency_ms": None,
        "total_elapsed_ms": None,
        "attempts": 0,
        "error_message": None,
    }
    defaults.update(values)
    comparison = defaults.get("comparison_json")
    comparison = dict(comparison) if isinstance(comparison, Mapping) else {}
    comparison["experiment"] = {
        **job.condition.as_dict(),
        "repetition": job.repetition,
        "requested_settings": {
            "temperature": job.settings["temperature"],
            "max_tokens": job.settings["max_tokens"],
        },
        "optional_parameters_removed": bool(
            comparison.get("diagnostics", {}).get("optional_parameters_removed", False)
        ),
    }
    defaults["comparison_json"] = comparison
    return Result(
        run_id=job.run_id,
        model_id=job.model_id,
        task_id=job.task.task_id,
        item_id=job.item.item_id,
        repetition=job.storage_repetition,
        input_json=job.item.inputs,
        gold_json=job.item.gold,
        status=status,
        created_at=datetime.now(UTC),
        **defaults,
    )


def _request_count(
    models: Sequence[str], tasks: Sequence[TaskDefinition], settings: Mapping[str, Any]
) -> int:
    item_limit = settings.get("max_items_per_task")
    repetitions = int(settings["repetitions"])
    condition_count = len(experiment_conditions(settings))
    items = sum(
        min(len(task.gold.items), item_limit) if item_limit is not None else len(task.gold.items)
        for task in tasks
        if task.gold is not None
    )
    return len(models) * items * repetitions * condition_count


def _expand_jobs(
    run_id: str,
    models: Sequence[str],
    tasks: Sequence[TaskDefinition],
    settings: Mapping[str, Any],
) -> tuple[Job, ...]:
    jobs: list[Job] = []
    item_limit = settings.get("max_items_per_task")
    repetitions = int(settings["repetitions"])
    for condition_index, condition in enumerate(experiment_conditions(settings)):
        for task in tasks:
            assert task.gold is not None
            items = task.gold.items[:item_limit] if item_limit is not None else task.gold.items
            for repetition in range(1, repetitions + 1):
                storage_repetition = condition_index * repetitions + repetition
                for item in items:
                    for model in models:
                        jobs.append(
                            Job(
                                run_id=run_id,
                                model_id=model,
                                task=task,
                                item=item,
                                repetition=repetition,
                                storage_repetition=storage_repetition,
                                condition=condition,
                                settings=condition.settings,
                            )
                        )
    return tuple(jobs)


def request_count(
    models: Sequence[str], tasks: Sequence[TaskDefinition], settings: Mapping[str, Any]
) -> int:
    """Public request-count helper used by API validation."""

    return _request_count(models, tasks, settings)
