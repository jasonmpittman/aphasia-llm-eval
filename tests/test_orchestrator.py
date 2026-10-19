from __future__ import annotations

import threading
import time
from pathlib import Path

from aphasia_eval import create_app
from aphasia_eval.services.litellm_client import (
    CompletionResult,
    ModelInfoResult,
    ModelListResult,
)

ROOT = Path(__file__).parents[1]
HEADERS = {"Host": "localhost"}


class BlockingClient:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def list_models(self):
        return ModelListResult(("blocking-model",), "gateway", 1, 0.0)

    def get_model_info(self):
        return ModelInfoResult({"data": []}, 1, 0.0)

    def chat_completion(self, **_kwargs):
        self.started.set()
        assert self.release.wait(3)
        return _successful_completion()


class TrackingClient:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active_total = 0
        self.active_by_model = {"model-a": 0, "model-b": 0}
        self.max_total = 0
        self.max_by_model = {"model-a": 0, "model-b": 0}

    def list_models(self):
        return ModelListResult(("model-a", "model-b"), "gateway", 1, 0.0)

    def get_model_info(self):
        return ModelInfoResult({"data": []}, 1, 0.0)

    def chat_completion(self, *, model, **_kwargs):
        with self.lock:
            self.active_total += 1
            self.active_by_model[model] += 1
            self.max_total = max(self.max_total, self.active_total)
            self.max_by_model[model] = max(self.max_by_model[model], self.active_by_model[model])
        time.sleep(0.02)
        with self.lock:
            self.active_total -= 1
            self.active_by_model[model] -= 1
        return _successful_completion()


class RaisingClient:
    def list_models(self):
        return ModelListResult(("raising-model",), "gateway", 1, 0.0)

    def get_model_info(self):
        return ModelInfoResult({"data": []}, 1, 0.0)

    def chat_completion(self, **_kwargs):
        raise RuntimeError("synthetic unexpected failure")


def _successful_completion():
    return CompletionResult(
        raw_text='{"word_indices": [1, 2, 3], "ciu_indices": [1, 2, 3]}',
        prompt_tokens=4,
        completion_tokens=2,
        reasoning_tokens=None,
        tokens_estimated=False,
        latency_ms=10.0,
        total_elapsed_ms=11.0,
        attempts=1,
    )


def _app_config(tmp_path, service, *, synchronous, total=2, per_model=1):
    return {
        "TESTING": True,
        "LITELLM_BASE_URL": "https://gateway.invalid/v1",
        "LITELLM_API_KEY": "unit-test-placeholder",  # pragma: allowlist secret
        "HOST": "127.0.0.1",
        "DATABASE_URL": f"sqlite:///{tmp_path / 'runs.db'}",
        "TASKS_DIR": str(ROOT / "tasks"),
        "GOLD_TASKS_DIR": str(ROOT / "gold_tasks/examples"),
        "PRICING_OVERRIDES_PATH": str(tmp_path / "pricing.yaml"),
        "LITELLM_CLIENT": service,
        "RUN_SYNCHRONOUS": synchronous,
        "MAX_CONCURRENCY_TOTAL": total,
        "MAX_CONCURRENCY_PER_MODEL": per_model,
    }


def test_cancel_finishes_inflight_and_marks_queued_jobs(tmp_path):
    service = BlockingClient()
    app = create_app(_app_config(tmp_path, service, synchronous=False, total=1, per_model=1))
    client = app.test_client()
    response = client.post(
        "/api/runs",
        json={
            "models": ["blocking-model"],
            "tasks": ["ciu_scoring"],
            "settings": {"repetitions": 1},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    run_id = response.get_json()["run_id"]
    assert service.started.wait(2)
    assert client.delete(f"/api/runs/{run_id}", json={}, headers=HEADERS).status_code == 409

    cancellation = client.post(f"/api/runs/{run_id}/cancel", json={}, headers=HEADERS)
    assert cancellation.status_code == 202
    service.release.set()
    app.extensions["aphasia_eval_orchestrator"].wait(run_id, 3)

    detail = client.get(f"/api/runs/{run_id}", headers=HEADERS).get_json()
    assert detail["run"]["status"] == "cancelled"
    assert detail["progress"]["finished"] == 5
    assert detail["progress"]["by_status"] == {"cancelled": 4, "ok": 1}
    events = client.get(f"/api/runs/{run_id}/events", headers=HEADERS).get_data(as_text=True)
    assert "event: run_cancelled" in events


def test_total_and_per_model_concurrency_are_bounded(tmp_path):
    service = TrackingClient()
    app = create_app(_app_config(tmp_path, service, synchronous=True, total=2, per_model=1))
    client = app.test_client()
    response = client.post(
        "/api/runs",
        json={
            "models": ["model-a", "model-b"],
            "tasks": ["ciu_scoring"],
            "settings": {"repetitions": 1, "max_items_per_task": 3},
        },
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert service.max_total == 2
    assert service.max_by_model == {"model-a": 1, "model-b": 1}
    detail = client.get(f"/api/runs/{response.get_json()['run_id']}", headers=HEADERS).get_json()
    assert detail["run"]["status"] == "completed"
    assert detail["run"]["settings"]["max_concurrency_total"] == 2
    assert detail["run"]["settings"]["max_concurrency_per_model"] == 1


def test_unexpected_job_failure_does_not_abort_run(tmp_path):
    app = create_app(_app_config(tmp_path, RaisingClient(), synchronous=True))
    client = app.test_client()
    response = client.post(
        "/api/runs",
        json={
            "models": ["raising-model"],
            "tasks": ["ciu_scoring"],
            "settings": {"max_items_per_task": 2},
        },
        headers=HEADERS,
    )
    run_id = response.get_json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}", headers=HEADERS).get_json()
    assert detail["run"]["status"] == "completed"
    assert detail["progress"]["by_status"] == {"error": 2}
    items = client.get(f"/api/runs/{run_id}/items", headers=HEADERS).get_json()["items"]
    assert {item["error_message"] for item in items} == {"Job failed unexpectedly."}
