from __future__ import annotations

import json

import httpx
import pytest

from aphasia_eval.services.litellm_client import (
    LiteLLMClient,
    estimate_tokens,
    parse_model_allowlist,
)

BASE_URL = "https://gateway.example/v1"
MESSAGES = [
    {"role": "system", "content": "Return JSON."},
    {"role": "user", "content": "Score this."},
]


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


def completion_response(
    *, content: object = '{"answer": 1}', usage: object = None
) -> dict[str, object]:
    result: dict[str, object] = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        result["usage"] = usage
    return result


def make_client(
    handler,
    *,
    clock: FakeClock | None = None,
    max_retries: int = 3,
    allowlist: str | None = None,
    token_estimator=lambda _model, _messages, _completion: (101, 17),
):
    test_clock = clock or FakeClock()
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    service = LiteLLMClient(
        base_url=f"{BASE_URL}/",
        api_key="unit-test-placeholder",  # pragma: allowlist secret
        max_retries=max_retries,
        model_allowlist=allowlist,
        client=http_client,
        sleep=test_clock.sleep,
        random_value=lambda: 0.0,
        perf_counter=test_clock,
        token_estimator=token_estimator,
    )
    return service, http_client, test_clock


def test_completion_success_records_usage_reasoning_headers_and_timing():
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/chat/completions"
        assert request.headers["Authorization"] == "Bearer unit-test-placeholder"
        payload = json.loads(request.content)
        assert payload == {
            "model": "model-a",
            "messages": MESSAGES,
            "temperature": 0.2,
            "max_tokens": 99,
        }
        clock.advance(0.025)
        return httpx.Response(
            200,
            json=completion_response(
                usage={
                    "prompt_tokens": 12,
                    "completion_tokens": 7,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                }
            ),
            headers={"X-LiteLLM-Response-Cost": "0.0012", "X-Ignored": "value"},
        )

    client, http_client, _ = make_client(handler, clock=clock)
    result = client.chat_completion(
        model="model-a", messages=MESSAGES, temperature=0.2, max_tokens=99
    )
    http_client.close()

    assert result.ok
    assert result.raw_text == '{"answer": 1}'
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 7
    assert result.reasoning_tokens == 3
    assert result.tokens_estimated is False
    assert result.latency_ms == pytest.approx(25)
    assert result.total_elapsed_ms == pytest.approx(25)
    assert result.attempts == 1
    assert result.cost_headers == {"x-litellm-response-cost": "0.0012"}
    assert result.optional_parameters_removed is False


def test_retryable_statuses_use_exponential_backoff_and_final_attempt_latency():
    clock = FakeClock()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        clock.advance(0.01 * calls)
        if calls < 3:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json=completion_response(usage={"prompt_tokens": 5, "completion_tokens": 2}),
        )

    client, http_client, _ = make_client(handler, clock=clock)
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.ok
    assert result.attempts == 3
    assert clock.sleeps == [0.5, 1.0]
    assert result.latency_ms == pytest.approx(30)
    assert result.total_elapsed_ms == pytest.approx(1560)


def test_429_respects_retry_after_header():
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "2.25"}),
            httpx.Response(
                200,
                json=completion_response(usage={"prompt_tokens": 3, "completion_tokens": 1}),
            ),
        ]
    )
    client, http_client, clock = make_client(lambda _request: next(responses))

    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.ok
    assert result.attempts == 2
    assert clock.sleeps == [2.25]


def test_timeout_retries_then_returns_structured_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider stalled", request=request)

    client, http_client, clock = make_client(handler, max_retries=2)
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "timeout"
    assert result.error.retryable is True
    assert result.attempts == 3
    assert clock.sleeps == [0.5, 1.0]
    assert result.latency_ms is None


def test_unsupported_optional_parameters_are_removed_once():
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "temperature is not supported"}},
            )
        return httpx.Response(
            200,
            json=completion_response(usage={"prompt_tokens": 8, "completion_tokens": 4}),
        )

    client, http_client, _ = make_client(handler, max_retries=0)
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.ok
    assert result.attempts == 2
    assert result.optional_parameters_removed is True
    assert payloads[0]["temperature"] == 0.0
    assert payloads[0]["max_tokens"] == 2048
    assert "temperature" not in payloads[1]
    assert "max_tokens" not in payloads[1]


def test_unrelated_400_is_not_retried_and_api_key_is_redacted():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "bad request unit-test-placeholder"}},
        )

    client, http_client, _ = make_client(handler)
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.error is not None
    assert result.error.code == "http_error"
    assert result.error.status_code == 400
    assert result.attempts == 1
    assert "unit-test-placeholder" not in result.error.message
    assert "[REDACTED]" in result.error.message


def test_missing_usage_uses_estimator_and_accepts_text_parts():
    seen: list[tuple[str, object, str]] = []

    def estimator(model, messages, completion):
        seen.append((model, messages, completion))
        return 40, 9

    client, http_client, _ = make_client(
        lambda _request: httpx.Response(
            200,
            json=completion_response(
                content=[{"type": "text", "text": "first"}, {"text": " second"}]
            ),
        ),
        token_estimator=estimator,
    )
    result = client.chat_completion(model="unknown-model", messages=MESSAGES)
    http_client.close()

    assert result.raw_text == "first second"
    assert result.prompt_tokens == 40
    assert result.completion_tokens == 9
    assert result.tokens_estimated is True
    assert seen == [("unknown-model", MESSAGES, "first second")]


@pytest.mark.parametrize(
    "estimator",
    [
        lambda _model, _messages, _completion: (_ for _ in ()).throw(ValueError()),
        lambda _model, _messages, _completion: (-1, 2),
    ],
)
def test_token_estimation_failure_is_structured(estimator):
    client, http_client, _ = make_client(
        lambda _request: httpx.Response(200, json=completion_response()),
        token_estimator=estimator,
    )
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.error is not None
    assert result.error.code == "token_estimation_error"
    assert result.latency_ms is not None


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": 42}}]},
        "not-json",
    ],
)
def test_invalid_completion_shape_is_a_structured_failure(body):
    response_factory = (
        (lambda: httpx.Response(200, text=body))
        if isinstance(body, str)
        else (lambda: httpx.Response(200, json=body))
    )
    client, http_client, _ = make_client(lambda _request: response_factory())
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.error is not None
    assert result.error.code == "invalid_response"
    assert result.raw_text is None
    assert result.latency_ms is not None


def test_network_error_is_structured_without_retry():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client, http_client, _ = make_client(handler)
    result = client.chat_completion(model="model-a", messages=MESSAGES)
    http_client.close()

    assert result.error is not None
    assert result.error.code == "network_error"
    assert result.attempts == 1


def test_list_models_discovers_unique_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{BASE_URL}/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"}]},
        )

    client, http_client, _ = make_client(handler)
    result = client.list_models()
    http_client.close()

    assert result.ok
    assert result.models == ("model-b", "model-a")
    assert result.source == "gateway"
    assert result.error is None


def test_list_models_uses_allowlist_when_endpoint_fails():
    client, http_client, _ = make_client(
        lambda _request: httpx.Response(403, json={"error": {"message": "forbidden"}}),
        max_retries=0,
        allowlist=" model-z,model-a,model-z ",
    )
    result = client.list_models()
    http_client.close()

    assert result.ok
    assert result.models == ("model-z", "model-a")
    assert result.source == "allowlist"
    assert result.error is not None
    assert result.error.status_code == 403


def test_list_models_reports_invalid_payload_without_allowlist():
    client, http_client, _ = make_client(lambda _request: httpx.Response(200, json={"data": {}}))
    result = client.list_models()
    http_client.close()

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "invalid_response"


def test_model_info_treats_restricted_endpoint_as_structured_nonfatal_result():
    client, http_client, _ = make_client(
        lambda _request: httpx.Response(404, json={"detail": "not enabled"})
    )
    result = client.get_model_info()
    http_client.close()

    assert result.data is None
    assert result.error is not None
    assert result.error.status_code == 404


def test_parse_allowlist_and_constructor_validation():
    assert parse_model_allowlist(None) == ()
    assert parse_model_allowlist(["a", " b ", "a", ""]) == ("a", "b")
    with pytest.raises(ValueError, match="base_url"):
        LiteLLMClient(base_url="", api_key="x")
    with pytest.raises(ValueError, match="api_key"):
        LiteLLMClient(base_url=BASE_URL, api_key="")
    with pytest.raises(ValueError, match="greater than zero"):
        LiteLLMClient(base_url=BASE_URL, api_key="x", timeout_seconds=0)
    with pytest.raises(ValueError, match="negative"):
        LiteLLMClient(base_url=BASE_URL, api_key="x", max_retries=-1)


def test_from_config_and_context_manager_close_owned_client():
    client = LiteLLMClient.from_config(
        {
            "LITELLM_BASE_URL": BASE_URL,
            "LITELLM_API_KEY": "x",
            "REQUEST_TIMEOUT_SECONDS": 4,
            "MAX_RETRIES": 0,
            "MODEL_ALLOWLIST": "a,b",
        }
    )
    with client as entered:
        assert entered is client
        assert not client._client.is_closed
    assert client._client.is_closed


def test_tiktoken_estimator_returns_nonnegative_counts(monkeypatch):
    class Encoding:
        @staticmethod
        def encode(value):
            return value.split()

    monkeypatch.setattr("tiktoken.encoding_for_model", lambda _model: Encoding())
    prompt, completion = estimate_tokens("known", MESSAGES, "two words")
    assert prompt > 0
    assert completion == 2

    def unknown_model(_model):
        raise KeyError

    monkeypatch.setattr("tiktoken.encoding_for_model", unknown_model)
    monkeypatch.setattr("tiktoken.get_encoding", lambda _name: Encoding())
    assert estimate_tokens("unknown", MESSAGES, "one")[-1] == 1
