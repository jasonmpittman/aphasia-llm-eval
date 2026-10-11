from __future__ import annotations

import httpx
import pytest

from aphasia_eval.services.litellm_client import LiteLLMClient
from aphasia_eval.services.pricing import (
    PricingService,
    TokenPricing,
    load_pricing_overrides,
    parse_proxy_pricing,
    resolve_cost,
)


def test_load_overrides_converts_usd_per_million(tmp_path):
    path = tmp_path / "pricing.yaml"
    path.write_text(
        """
models:
  model-a:
    input_usd_per_1m_tokens: 2.50
    output_usd_per_1m_tokens: 10
""".strip(),
        encoding="utf-8",
    )

    result = load_pricing_overrides(path)

    assert result["model-a"].input_cost_per_token == pytest.approx(0.0000025)
    assert result["model-a"].output_cost_per_token == pytest.approx(0.00001)
    assert load_pricing_overrides(tmp_path / "missing.yaml") == {}


@pytest.mark.parametrize(
    "contents, message",
    [
        ("- invalid", "must contain a mapping"),
        ("models: []", "must be a mapping"),
        ("models:\n  model-a: nope", "model ID"),
        (
            "models:\n  model-a:\n    input_usd_per_1m_tokens: -1",
            "requires a nonnegative",
        ),
        ("models: [", "Could not read"),
    ],
)
def test_invalid_override_files_are_rejected(tmp_path, contents, message):
    path = tmp_path / "pricing.yaml"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_pricing_overrides(path)


def test_override_has_priority_over_header_and_proxy():
    result = resolve_cost(
        model="model-a",
        prompt_tokens=1_000,
        completion_tokens=500,
        overrides={"model-a": TokenPricing(0.000002, 0.000004)},
        response_headers={"x-litellm-response-cost": "99"},
        proxy_pricing={"model-a": TokenPricing(1.0, 1.0)},
    )

    assert result.cost_source == "override"
    assert result.cost_usd == pytest.approx(0.004)


def test_header_has_priority_over_proxy_and_is_case_insensitive():
    result = resolve_cost(
        model="model-a",
        prompt_tokens=10,
        completion_tokens=5,
        response_headers={"X-LiteLLM-Response-Cost": "0.00125"},
        proxy_pricing={"model-a": TokenPricing(1.0, 1.0)},
    )

    assert result.cost_source == "header"
    assert result.cost_usd == pytest.approx(0.00125)


def test_invalid_header_falls_through_to_proxy_pricing():
    result = resolve_cost(
        model="model-a",
        prompt_tokens=100,
        completion_tokens=20,
        response_headers={"x-litellm-response-cost": "nan"},
        proxy_pricing={"model-a": TokenPricing(0.001, 0.002)},
    )

    assert result.cost_source == "proxy_pricing"
    assert result.cost_usd == pytest.approx(0.14)


def test_unknown_cost_is_none_not_zero():
    result = resolve_cost(model="unknown", prompt_tokens=0, completion_tokens=0)
    assert result.cost_source == "unknown"
    assert result.cost_usd is None


def test_negative_token_counts_are_rejected():
    with pytest.raises(ValueError, match="must not be negative"):
        resolve_cost(model="model-a", prompt_tokens=-1, completion_tokens=2)


def test_parse_proxy_pricing_handles_litellm_shape_and_ignores_bad_entries():
    result = parse_proxy_pricing(
        {
            "data": [
                {
                    "model_name": "model-a",
                    "model_info": {
                        "input_cost_per_token": "0.000001",
                        "output_cost_per_token": 0.000002,
                    },
                },
                {
                    "id": "model-b",
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0.5,
                },
                {"model_name": "missing-output", "model_info": {"input_cost_per_token": 1}},
                "invalid",
            ]
        }
    )

    assert result == {
        "model-a": TokenPricing(0.000001, 0.000002),
        "model-b": TokenPricing(0, 0.5),
    }
    assert parse_proxy_pricing(None) == {}
    assert parse_proxy_pricing({"data": {}}) == {}


def test_pricing_service_refreshes_and_uses_proxy_metadata(tmp_path):
    override_path = tmp_path / "overrides.yaml"
    override_path.write_text("models: {}", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/model/info")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "model_name": "model-a",
                        "model_info": {
                            "input_cost_per_token": 0.01,
                            "output_cost_per_token": 0.02,
                        },
                    }
                ]
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    llm_client = LiteLLMClient(
        base_url="https://gateway.example/v1",
        api_key="test-placeholder",  # pragma: allowlist secret
        client=http_client,
        max_retries=0,
    )
    service = PricingService(override_path=override_path, client=llm_client)

    metadata = service.refresh_proxy_pricing()
    estimate = service.resolve(model="model-a", prompt_tokens=10, completion_tokens=5)
    http_client.close()

    assert metadata is not None and metadata.error is None
    assert estimate.cost_source == "proxy_pricing"
    assert estimate.cost_usd == pytest.approx(0.2)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_restricted_model_info_is_nonfatal_and_cost_stays_unknown(tmp_path, status):
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status))
    )
    llm_client = LiteLLMClient(
        base_url="https://gateway.example/v1",
        api_key="test-placeholder",  # pragma: allowlist secret
        client=http_client,
        max_retries=0,
    )
    service = PricingService(override_path=tmp_path / "missing.yaml", client=llm_client)

    metadata = service.refresh_proxy_pricing()
    estimate = service.resolve(model="model-a", prompt_tokens=10, completion_tokens=5)
    http_client.close()

    assert metadata is not None and metadata.error is not None
    assert service.proxy_pricing == {}
    assert estimate.cost_source == "unknown"
    assert estimate.cost_usd is None


def test_pricing_service_without_client_does_not_refresh(tmp_path):
    service = PricingService(override_path=tmp_path / "missing.yaml")
    assert service.refresh_proxy_pricing() is None
