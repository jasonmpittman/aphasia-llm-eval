from __future__ import annotations

import re
from pathlib import Path

import pytest

from aphasia_eval import create_app
from aphasia_eval.services.litellm_client import (
    CompletionResult,
    LiteLLMError,
    ModelInfoResult,
    ModelListResult,
)

ROOT = Path(__file__).parents[1]


class FakeLiteLLMClient:
    def __init__(self) -> None:
        self.models = ("good-model", "error-model", "bad-json", "alignment-model")
        self.calls = []

    def list_models(self):
        return ModelListResult(self.models, "gateway", 1, 1.0)

    def get_model_info(self):
        return ModelInfoResult(
            {
                "data": [
                    {
                        "model_name": "error-model",
                        "model_info": {
                            "input_cost_per_token": 0.000001,
                            "output_cost_per_token": 0.000002,
                        },
                    }
                ]
            },
            1,
            1.0,
        )

    def chat_completion(self, *, model, messages, temperature, max_tokens):
        self.calls.append((model, messages, temperature, max_tokens))
        common = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "reasoning_tokens": 1,
            "tokens_estimated": False,
            "latency_ms": 12.0,
            "total_elapsed_ms": 14.0,
            "attempts": 1,
        }
        if model == "error-model":
            return CompletionResult(
                raw_text=None,
                cost_headers={},
                error=LiteLLMError("http_error", "Synthetic gateway failure.", 500),
                **common,
            )
        if model == "bad-json":
            raw_text = "not valid JSON"
        elif "Tag every numbered token" in messages[1]["content"]:
            indices = [
                int(match.group(1))
                for match in re.finditer(r"(?m)^(\d+)\t", messages[1]["content"])
            ]
            if model == "alignment-model":
                indices = indices[:1]
            tags = ",".join(f'{{"i": {index}, "pos": "NOUN"}}' for index in indices)
            raw_text = f'{{"tags": [{tags}]}}'
        else:
            raw_text = '{"total_words": 3, "ciu_count": 3}'
        return CompletionResult(
            raw_text=raw_text,
            cost_headers={"x-litellm-response-cost": "0.001"},
            **common,
        )


@pytest.fixture
def fake_litellm():
    return FakeLiteLLMClient()


@pytest.fixture
def app(tmp_path, fake_litellm):
    return create_app(
        {
            "TESTING": True,
            "LITELLM_BASE_URL": "https://gateway.invalid/v1",
            "LITELLM_API_KEY": "unit-test-placeholder",  # pragma: allowlist secret
            "HOST": "127.0.0.1",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}",
            "TASKS_DIR": str(ROOT / "tasks"),
            "GOLD_TASKS_DIR": str(ROOT / "gold_tasks/examples"),
            "PRICING_OVERRIDES_PATH": str(tmp_path / "missing-pricing.yaml"),
            "LITELLM_CLIENT": fake_litellm,
            "RUN_SYNCHRONOUS": True,
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
