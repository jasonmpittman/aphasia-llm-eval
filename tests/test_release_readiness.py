from __future__ import annotations

import shutil
from pathlib import Path

from aphasia_eval import create_app
from aphasia_eval.services.endpoint_check import run_checks
from aphasia_eval.services.litellm_client import (
    CompletionResult,
    LiteLLMError,
    ModelInfoResult,
    ModelListResult,
)

ROOT = Path(__file__).parents[1]
HEADERS = {"Host": "localhost"}


class ExtensionClient:
    def list_models(self):
        return ModelListResult(("fixture-model",), "gateway", 1, 1.0)

    def get_model_info(self):
        return ModelInfoResult(None, 1, 1.0, LiteLLMError("http_error", "Unavailable.", 403))

    def chat_completion(self, *, model, messages, temperature, max_tokens):
        count = 3 if "one two three" in messages[1]["content"] else 2
        return CompletionResult(
            raw_text=f'{{"word_count": {count}}}',
            prompt_tokens=10,
            completion_tokens=4,
            reasoning_tokens=None,
            tokens_estimated=False,
            latency_ms=2.0,
            total_elapsed_ms=2.0,
            attempts=1,
        )


def test_yaml_only_third_task_appears_and_runs_end_to_end(tmp_path):
    tasks_dir = tmp_path / "tasks"
    schema_dir = tasks_dir / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy(ROOT / "tasks/schema/task.schema.json", schema_dir / "task.schema.json")
    shutil.copy(
        ROOT / "tests/fixtures/extensibility/tasks/word_count.yaml",
        tasks_dir / "word_count.yaml",
    )
    app = create_app(
        {
            "TESTING": True,
            "LITELLM_BASE_URL": "https://gateway.invalid/v1",
            "LITELLM_API_KEY": "release-test-placeholder",  # pragma: allowlist secret
            "DATABASE_URL": f"sqlite:///{tmp_path / 'release.db'}",
            "TASKS_DIR": str(tasks_dir),
            "GOLD_TASKS_DIR": str(ROOT / "tests/fixtures/extensibility/gold"),
            "PRICING_OVERRIDES_PATH": str(tmp_path / "missing-pricing.yaml"),
            "LITELLM_CLIENT": ExtensionClient(),
            "RUN_SYNCHRONOUS": True,
        }
    )
    client = app.test_client()

    tasks = client.get("/api/tasks", headers=HEADERS).get_json()["tasks"]
    assert [(task["task_id"], task["enabled"], task["item_count"]) for task in tasks] == [
        ("word_count", True, 2)
    ]

    response = client.post(
        "/api/runs",
        json={
            "models": ["fixture-model"],
            "tasks": ["word_count"],
            "settings": {
                "temperature": 0,
                "max_tokens": 20,
                "repetitions": 1,
                "max_items_per_task": None,
            },
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    run_id = response.get_json()["run_id"]
    detail = client.get(f"/api/runs/{run_id}", headers=HEADERS).get_json()
    assert detail["run"]["status"] == "completed"
    assert detail["progress"]["by_status"] == {"ok": 2}
    assert any(
        aggregate["stat_name"] == "headline" and aggregate["value"] == 0
        for aggregate in detail["aggregates"]
    )


def test_endpoint_checker_reports_verified_gateway_capabilities():
    lines: list[str] = []
    status = run_checks(ExtensionClient(), output=lines.append)

    assert status == 0
    assert "[models endpoint] available (1 model(s))" in lines
    assert "[pricing endpoint] unavailable (HTTP 403); proxy pricing is optional" in lines
    assert "[optional parameters] accepted" in lines
    assert "[cost header] absent; override/proxy/unknown fallback applies" in lines
    assert "[usage] present" in lines


def test_endpoint_checker_stops_when_no_models_are_available():
    class EmptyClient:
        def list_models(self):
            return ModelListResult(
                (),
                "gateway",
                1,
                1.0,
                LiteLLMError("network_error", "Gateway unavailable."),
            )

    lines: list[str] = []
    assert run_checks(EmptyClient(), output=lines.append) == 1
    assert all("secret" not in line.lower() for line in lines)
