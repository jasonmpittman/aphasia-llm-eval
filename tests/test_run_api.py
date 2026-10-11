from __future__ import annotations

import json

import pytest

from aphasia_eval.services.litellm_client import LiteLLMError, ModelListResult
from aphasia_eval.services.pricing import TokenPricing

HEADERS = {"Host": "localhost"}


def _create_run(client, **overrides):
    body = {
        "models": ["good-model", "error-model", "bad-json"],
        "tasks": ["ciu_scoring"],
        "settings": {
            "temperature": 0,
            "max_tokens": 100,
            "repetitions": 2,
            "max_items_per_task": 2,
        },
        "label": "Synthetic integration run",
    }
    body.update(overrides)
    return client.post("/api/runs", json=body, headers=HEADERS)


def test_model_and_task_discovery(app, client):
    app.extensions["aphasia_eval_pricing"].overrides["good-model"] = TokenPricing(0.1, 0.2)
    models = client.get("/api/models", headers=HEADERS)
    assert models.status_code == 200
    assert models.get_json() == {
        "models": [
            {"id": "good-model", "pricing_source": "override"},
            {"id": "error-model", "pricing_source": "proxy_pricing"},
            {"id": "bad-json", "pricing_source": "unknown"},
            {"id": "alignment-model", "pricing_source": "unknown"},
        ],
        "source": "gateway",
        "error": None,
    }

    tasks = client.get("/api/tasks", headers=HEADERS).get_json()["tasks"]
    assert [task["task_id"] for task in tasks] == ["ciu_scoring", "pos_breakdown"]
    assert all(task["enabled"] and task["gold_status"] == "found" for task in tasks)
    assert [task["item_count"] for task in tasks] == [5, 5]
    assert tasks[0]["view_descriptor"]["type"] == "numeric_fields"


def test_degraded_health_and_missing_gold_status(app, client, fake_litellm, tmp_path):
    fake_litellm.list_models = lambda: ModelListResult(
        ("fallback-model",),
        "allowlist",
        1,
        1.0,
        LiteLLMError("network_error", "Gateway unavailable."),
    )
    health = client.get("/api/health", headers=HEADERS).get_json()
    assert health["litellm"] == {
        "configured": True,
        "reachable": False,
        "models_available": 1,
        "error": "Gateway unavailable.",
    }

    app.config["GOLD_TASKS_DIR"] = str(tmp_path / "absent-gold")
    tasks = client.get("/api/tasks", headers=HEADERS).get_json()["tasks"]
    assert all(not task["enabled"] and task["gold_status"] == "missing" for task in tasks)


def test_full_run_with_success_error_and_parse_failure(client, fake_litellm):
    response = _create_run(client)
    assert response.status_code == 202
    run_id = response.get_json()["run_id"]
    assert len(fake_litellm.calls) == 12

    detail_response = client.get(f"/api/runs/{run_id}", headers=HEADERS)
    assert detail_response.status_code == 200
    detail = detail_response.get_json()
    assert detail["run"]["status"] == "completed"
    assert detail["run"]["label"] == "Synthetic integration run"
    assert detail["run"]["settings"]["total_requests"] == 12
    assert detail["progress"] == {
        "finished": 12,
        "total": 12,
        "by_status": {"error": 4, "ok": 4, "parse_failure": 4},
    }
    assert {task["task_id"] for task in detail["tasks"]} == {"ciu_scoring"}
    assert all(len(task["task_file_sha256"]) == 64 for task in detail["tasks"])
    assert all(len(task["prompt_sha256"]) == 64 for task in detail["tasks"])
    assert all(len(task["gold_file_sha256"]) == 64 for task in detail["tasks"])

    aggregates = {
        (entry["model_id"], entry["stat_name"], entry["field"]): entry
        for entry in detail["aggregates"]
    }
    assert aggregates[("good-model", "items_run", None)]["value"] == 4
    assert aggregates[("good-model", "mean_abs_diff", "ciu_count")]["value"] == 0.5
    assert aggregates[("good-model", "headline", "ciu_count")]["value"] == 0.5
    assert aggregates[("good-model", "headline_stddev", "ciu_count")]["value"] == 0
    assert aggregates[("good-model", "estimated_cost_usd", None)]["value"] == 0.004
    assert aggregates[("bad-json", "parse_failures", None)]["value"] == 4
    assert aggregates[("error-model", "errors", None)]["value"] == 4
    assert {entry["task_id"] for entry in detail["aggregates"]} == {"ciu_scoring"}

    page = client.get(
        f"/api/runs/{run_id}/items?model=good-model&per_page=2&page=2", headers=HEADERS
    ).get_json()
    assert page["pagination"] == {"page": 2, "per_page": 2, "total": 4, "pages": 2}
    assert len(page["items"]) == 2
    assert all(item["status"] == "ok" for item in page["items"])
    assert page["items"][0]["prompt_rendered"]["system"]
    assert page["items"][0]["input"]["transcript"]
    assert page["items"][0]["comparison"]["details"]["fields"]

    failures = client.get(
        f"/api/runs/{run_id}/items?parse_failures=true", headers=HEADERS
    ).get_json()
    assert failures["pagination"]["total"] == 4
    assert all(item["status"] == "parse_failure" for item in failures["items"])
    disagreements = client.get(
        f"/api/runs/{run_id}/items?disagreements=true", headers=HEADERS
    ).get_json()
    assert disagreements["pagination"]["total"] == 2

    event_response = client.get(f"/api/runs/{run_id}/events", headers=HEADERS)
    event_text = event_response.get_data(as_text=True)
    assert event_response.mimetype == "text/event-stream"
    assert "event: run_started" in event_text
    assert "event: job_completed" in event_text
    assert "event: job_failed" in event_text
    assert "event: run_completed" in event_text
    data_lines = [line[6:] for line in event_text.splitlines() if line.startswith("data: ")]
    assert all(json.loads(line)["run_id"] == run_id for line in data_lines)
    job_events = [json.loads(line) for line in data_lines if "model_id" in json.loads(line)]
    assert {event["model_id"] for event in job_events} == {
        "good-model",
        "error-model",
        "bad-json",
    }
    assert all(event["task_id"] == "ciu_scoring" for event in job_events)
    last_event_id = [line[4:] for line in event_text.splitlines() if line.startswith("id: ")][-1]
    resumed = client.get(
        f"/api/runs/{run_id}/events",
        headers={**HEADERS, "Last-Event-ID": last_event_id},
    )
    assert resumed.get_data(as_text=True) == ""
    invalid_resume = client.get(
        f"/api/runs/{run_id}/events",
        headers={**HEADERS, "Last-Event-ID": "not-an-integer"},
    )
    assert invalid_resume.status_code == 400

    listed = client.get("/api/runs", headers=HEADERS).get_json()["runs"]
    assert listed[0]["id"] == run_id
    assert listed[0]["models"] == ["bad-json", "error-model", "good-model"]
    assert listed[0]["tasks"] == ["ciu_scoring"]

    cancel = client.post(f"/api/runs/{run_id}/cancel", json={}, headers=HEADERS)
    assert cancel.status_code == 409
    deleted = client.delete(f"/api/runs/{run_id}", json={}, headers=HEADERS)
    assert deleted.status_code == 204
    assert client.get(f"/api/runs/{run_id}", headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "body, code",
    [
        ([], "invalid_request"),
        ({"models": [], "tasks": ["ciu_scoring"]}, "invalid_request"),
        ({"models": ["good-model", "good-model"], "tasks": ["ciu_scoring"]}, "invalid_request"),
        ({"models": ["missing-model"], "tasks": ["ciu_scoring"]}, "invalid_models"),
        ({"models": ["good-model"], "tasks": ["missing_task"]}, "invalid_tasks"),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "settings": {"repetitions": 0},
            },
            "invalid_request",
        ),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "settings": "not-an-object",
            },
            "invalid_request",
        ),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "settings": {"temperature": float("inf")},
            },
            "invalid_request",
        ),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "settings": {"max_items_per_task": 0},
            },
            "invalid_request",
        ),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "label": 42,
            },
            "invalid_request",
        ),
        (
            {
                "models": ["good-model"],
                "tasks": ["ciu_scoring"],
                "unknown": True,
            },
            "invalid_request",
        ),
    ],
)
def test_create_run_validation(client, body, code):
    response = client.post("/api/runs", json=body, headers=HEADERS)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == code


def test_request_cap_and_query_validation(app, client):
    app.config["MAX_REQUESTS_PER_RUN"] = 1
    response = _create_run(
        client,
        models=["good-model"],
        settings={"repetitions": 1, "max_items_per_task": 2},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "request_limit_exceeded"

    assert client.get("/api/runs/missing/items", headers=HEADERS).status_code == 404
    response = client.get("/api/runs/missing/items?page=zero", headers=HEADERS)
    assert response.status_code == 400
    response = client.get("/api/runs/missing/items?parse_failures=maybe", headers=HEADERS)
    assert response.status_code == 400
    assert client.get("/api/runs/missing/events", headers=HEADERS).status_code == 404
    assert client.post("/api/runs/missing/cancel", json={}, headers=HEADERS).status_code == 404
    assert client.delete("/api/runs/missing", json={}, headers=HEADERS).status_code == 404


def test_pos_aggregation_and_alignment_filter(client):
    response = _create_run(
        client,
        models=["alignment-model"],
        tasks=["pos_breakdown"],
        settings={"repetitions": 1, "max_items_per_task": 1},
    )
    run_id = response.get_json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}", headers=HEADERS).get_json()
    aggregates = {
        (entry["stat_name"], entry["field"]): entry["value"] for entry in detail["aggregates"]
    }
    assert aggregates[("token_agreement", None)] == 0
    assert aggregates[("mean_signed_count_diff", "NOUN")] == 0
    assert aggregates[("mean_signed_count_diff", "VERB")] == -1
    assert aggregates[("headline", None)] == 0
    assert ("headline_stddev", None) not in aggregates

    filtered = client.get(
        f"/api/runs/{run_id}/items?alignment_problems=true", headers=HEADERS
    ).get_json()
    assert filtered["pagination"]["total"] == 1
    assert filtered["items"][0]["comparison"]["details"]["alignment_status"] == "missing"
