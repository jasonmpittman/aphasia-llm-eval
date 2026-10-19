"""Thread-safe HTTP client for the OpenAI-compatible LiteLLM gateway."""

from __future__ import annotations

import email.utils
import math
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import tiktoken

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
OPTIONAL_PARAMETERS = frozenset({"temperature", "max_tokens"})
COST_HEADER_NAMES = frozenset({"x-litellm-response-cost"})

ErrorCode = Literal[
    "http_error",
    "invalid_response",
    "network_error",
    "no_final_content",
    "token_estimation_error",
    "timeout",
    "truncated",
]
ModelSource = Literal["gateway", "allowlist"]

TokenEstimator = Callable[[str, Sequence[Mapping[str, str]], str], tuple[int, int]]


@dataclass(frozen=True, slots=True)
class LiteLLMError:
    """A safe, structured gateway error suitable for persistence and API output."""

    code: ErrorCode
    message: str
    status_code: int | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """The complete outcome of one chat-completion operation."""

    raw_text: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    tokens_estimated: bool
    latency_ms: float | None
    total_elapsed_ms: float
    attempts: int
    cost_headers: Mapping[str, str] = field(default_factory=dict)
    optional_parameters_removed: bool = False
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    error: LiteLLMError | None = None

    @property
    def ok(self) -> bool:
        """Return whether the gateway produced a valid completion response."""

        return self.error is None


@dataclass(frozen=True, slots=True)
class ModelListResult:
    """Model discovery result, optionally degraded to the configured allowlist."""

    models: tuple[str, ...]
    source: ModelSource
    attempts: int
    total_elapsed_ms: float
    error: LiteLLMError | None = None
    metadata: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Return whether models are available for selection."""

        return bool(self.models)


@dataclass(frozen=True, slots=True)
class ModelInfoResult:
    """Raw model-pricing metadata returned by the proxy."""

    data: Any | None
    attempts: int
    total_elapsed_ms: float
    error: LiteLLMError | None = None


def parse_model_allowlist(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """Normalize a comma-separated or iterable model allowlist."""

    if value is None:
        return ()
    values = value.split(",") if isinstance(value, str) else value
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def estimate_tokens(
    model: str, messages: Sequence[Mapping[str, str]], completion: str
) -> tuple[int, int]:
    """Estimate prompt and completion counts with ``tiktoken``.

    The small per-message allowance accounts for chat framing. It is an estimate,
    not a claim about any provider's exact tokenizer.
    """

    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    prompt_tokens = 2
    for message in messages:
        prompt_tokens += 4
        prompt_tokens += len(encoding.encode(message.get("role", "")))
        prompt_tokens += len(encoding.encode(message.get("content", "")))
    return prompt_tokens, len(encoding.encode(completion))


class LiteLLMClient:
    """Synchronous, pooled client for LiteLLM chat and metadata endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
        model_allowlist: str | Sequence[str] | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        perf_counter: Callable[[], float] = time.perf_counter,
        token_estimator: TokenEstimator = estimate_tokens,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_retries = max_retries
        self._model_allowlist = parse_model_allowlist(model_allowlist)
        self._sleep = sleep
        self._random_value = random_value
        self._perf_counter = perf_counter
        self._token_estimator = token_estimator
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @classmethod
    def from_config(cls, config: Mapping[str, Any], **kwargs: Any) -> LiteLLMClient:
        """Build a client from Flask-compatible application configuration."""

        return cls(
            base_url=str(config["LITELLM_BASE_URL"]),
            api_key=str(config["LITELLM_API_KEY"]),
            timeout_seconds=float(config.get("REQUEST_TIMEOUT_SECONDS", 120.0)),
            max_retries=int(config.get("MAX_RETRIES", 3)),
            model_allowlist=config.get("MODEL_ALLOWLIST"),
            **kwargs,
        )

    def __enter__(self) -> LiteLLMClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally owned HTTP connection pool."""

        if self._owns_client:
            self._client.close()

    def chat_completion(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, str]],
        temperature: float | None = 0.0,
        max_tokens: int | None = 2048,
    ) -> CompletionResult:
        """Send a completion request with retry and usage fallback handling."""

        request_messages = [
            {"role": str(message["role"]), "content": str(message["content"])}
            for message in messages
        ]
        payload: dict[str, Any] = {"model": model, "messages": request_messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        operation_started = self._perf_counter()
        attempts = 0
        retry_count = 0
        optional_parameters_removed = False

        while True:
            attempts += 1
            attempt_started = self._perf_counter()
            try:
                response = self._client.post(
                    self._url("chat/completions"),
                    headers=self._request_headers(),
                    json=payload,
                )
                attempt_finished = self._perf_counter()
            except httpx.TimeoutException:
                if retry_count < self._max_retries:
                    self._sleep(self._backoff_seconds(retry_count))
                    retry_count += 1
                    continue
                return self._completion_failure(
                    operation_started,
                    attempts,
                    LiteLLMError(
                        code="timeout",
                        message="LiteLLM request timed out.",
                        retryable=True,
                    ),
                    optional_parameters_removed,
                )
            except httpx.RequestError as error:
                return self._completion_failure(
                    operation_started,
                    attempts,
                    LiteLLMError(
                        code="network_error",
                        message=self._redact(f"LiteLLM request failed: {error}"),
                    ),
                    optional_parameters_removed,
                )

            if 200 <= response.status_code < 300:
                latency_ms = (attempt_finished - attempt_started) * 1000
                return self._parse_completion_response(
                    response=response,
                    model=model,
                    messages=request_messages,
                    operation_started=operation_started,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    optional_parameters_removed=optional_parameters_removed,
                )

            if (
                response.status_code == 400
                and not optional_parameters_removed
                and OPTIONAL_PARAMETERS.intersection(payload)
                and self._is_unsupported_parameter_error(response)
            ):
                for parameter in OPTIONAL_PARAMETERS:
                    payload.pop(parameter, None)
                optional_parameters_removed = True
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < self._max_retries:
                self._sleep(self._retry_delay(response, retry_count))
                retry_count += 1
                continue

            return self._completion_failure(
                operation_started,
                attempts,
                self._response_error(response),
                optional_parameters_removed,
            )

    def list_models(self) -> ModelListResult:
        """Discover gateway models, falling back to the configured allowlist."""

        data, error, attempts, elapsed_ms = self._get_json("models")
        if error is not None:
            return ModelListResult(
                models=self._model_allowlist,
                source="allowlist" if self._model_allowlist else "gateway",
                attempts=attempts,
                total_elapsed_ms=elapsed_ms,
                error=error,
            )

        try:
            entries = data["data"]
            if not isinstance(entries, list):
                raise TypeError
            valid_entries = [
                entry
                for entry in entries
                if isinstance(entry, Mapping)
                and isinstance(entry.get("id"), str)
                and entry["id"].strip()
            ]
            discovered = tuple(dict.fromkeys(entry["id"].strip() for entry in valid_entries))
            metadata = {entry["id"].strip(): dict(entry) for entry in valid_entries}
        except (KeyError, TypeError):
            invalid_error = LiteLLMError(
                code="invalid_response",
                message="LiteLLM model response did not contain a data list.",
            )
            return ModelListResult(
                models=self._model_allowlist,
                source="allowlist" if self._model_allowlist else "gateway",
                attempts=attempts,
                total_elapsed_ms=elapsed_ms,
                error=invalid_error,
            )

        if self._model_allowlist:
            return ModelListResult(
                models=self._model_allowlist,
                source="allowlist",
                attempts=attempts,
                total_elapsed_ms=elapsed_ms,
                metadata={
                    model: metadata[model] for model in self._model_allowlist if model in metadata
                },
            )
        return ModelListResult(
            models=discovered,
            source="gateway",
            attempts=attempts,
            total_elapsed_ms=elapsed_ms,
            metadata=metadata,
        )

    def get_model_info(self) -> ModelInfoResult:
        """Fetch optional proxy model metadata used for cost estimation."""

        data, error, attempts, elapsed_ms = self._get_json("model/info")
        return ModelInfoResult(
            data=data,
            attempts=attempts,
            total_elapsed_ms=elapsed_ms,
            error=error,
        )

    def _get_json(self, path: str) -> tuple[Any | None, LiteLLMError | None, int, float]:
        operation_started = self._perf_counter()
        attempts = 0
        retry_count = 0
        while True:
            attempts += 1
            try:
                response = self._client.get(self._url(path), headers=self._request_headers())
            except httpx.TimeoutException:
                if retry_count < self._max_retries:
                    self._sleep(self._backoff_seconds(retry_count))
                    retry_count += 1
                    continue
                return (
                    None,
                    LiteLLMError(
                        code="timeout",
                        message="LiteLLM request timed out.",
                        retryable=True,
                    ),
                    attempts,
                    self._elapsed_ms(operation_started),
                )
            except httpx.RequestError as error:
                return (
                    None,
                    LiteLLMError(
                        code="network_error",
                        message=self._redact(f"LiteLLM request failed: {error}"),
                    ),
                    attempts,
                    self._elapsed_ms(operation_started),
                )

            if 200 <= response.status_code < 300:
                try:
                    return response.json(), None, attempts, self._elapsed_ms(operation_started)
                except ValueError:
                    return (
                        None,
                        LiteLLMError(
                            code="invalid_response",
                            message="LiteLLM returned a non-JSON response.",
                        ),
                        attempts,
                        self._elapsed_ms(operation_started),
                    )

            if response.status_code in RETRYABLE_STATUS_CODES and retry_count < self._max_retries:
                self._sleep(self._retry_delay(response, retry_count))
                retry_count += 1
                continue
            return (
                None,
                self._response_error(response),
                attempts,
                self._elapsed_ms(operation_started),
            )

    def _parse_completion_response(
        self,
        *,
        response: httpx.Response,
        model: str,
        messages: Sequence[Mapping[str, str]],
        operation_started: float,
        latency_ms: float,
        attempts: int,
        optional_parameters_removed: bool,
    ) -> CompletionResult:
        try:
            data = response.json()
        except ValueError:
            return self._completion_failure(
                operation_started,
                attempts,
                LiteLLMError(
                    code="invalid_response",
                    message="LiteLLM completion response had an unexpected shape.",
                ),
                optional_parameters_removed,
                latency_ms=latency_ms,
                diagnostics={"response_type": "non_json"},
            )
        diagnostics = _completion_diagnostics(data)
        try:
            content = data["choices"][0]["message"]["content"]
            raw_text = _message_text(content)
        except (IndexError, KeyError, TypeError, ValueError):
            finish_reason = diagnostics.get("finish_reason")
            reasoning_present = diagnostics.get("reasoning_present") is True
            if finish_reason == "length":
                error = LiteLLMError(
                    code="truncated",
                    message=(
                        "Model exhausted its output token limit before returning final answer text."
                    ),
                )
            elif reasoning_present:
                error = LiteLLMError(
                    code="no_final_content",
                    message="Model returned reasoning but no final answer text.",
                )
            elif diagnostics.get("content_state") == "null":
                error = LiteLLMError(
                    code="no_final_content",
                    message="Model returned no final answer text.",
                )
            else:
                error = LiteLLMError(
                    code="invalid_response",
                    message="LiteLLM completion response had an unexpected shape.",
                )
            return self._completion_failure(
                operation_started,
                attempts,
                error,
                optional_parameters_removed,
                latency_ms=latency_ms,
                diagnostics=diagnostics,
            )

        usage = data.get("usage") if isinstance(data, Mapping) else None
        usage = usage if isinstance(usage, Mapping) else {}
        prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
        completion_tokens = _nonnegative_int(usage.get("completion_tokens"))
        tokens_estimated = prompt_tokens is None or completion_tokens is None
        if tokens_estimated:
            try:
                estimated_prompt, estimated_completion = self._token_estimator(
                    model, messages, raw_text
                )
            except Exception:
                return self._completion_failure(
                    operation_started,
                    attempts,
                    LiteLLMError(
                        code="token_estimation_error",
                        message="Token usage was missing and could not be estimated.",
                    ),
                    optional_parameters_removed,
                    latency_ms=latency_ms,
                    diagnostics=diagnostics,
                )
            if estimated_prompt < 0 or estimated_completion < 0:
                return self._completion_failure(
                    operation_started,
                    attempts,
                    LiteLLMError(
                        code="token_estimation_error",
                        message="Token estimator returned an invalid count.",
                    ),
                    optional_parameters_removed,
                    latency_ms=latency_ms,
                    diagnostics=diagnostics,
                )
            prompt_tokens = prompt_tokens if prompt_tokens is not None else estimated_prompt
            completion_tokens = (
                completion_tokens if completion_tokens is not None else estimated_completion
            )

        details = usage.get("completion_tokens_details")
        reasoning_tokens = _nonnegative_int(usage.get("reasoning_tokens"))
        if reasoning_tokens is None and isinstance(details, Mapping):
            reasoning_tokens = _nonnegative_int(details.get("reasoning_tokens"))

        cost_headers = {
            name.lower(): value
            for name, value in response.headers.items()
            if name.lower() in COST_HEADER_NAMES
        }
        return CompletionResult(
            raw_text=raw_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            tokens_estimated=tokens_estimated,
            latency_ms=latency_ms,
            total_elapsed_ms=self._elapsed_ms(operation_started),
            attempts=attempts,
            cost_headers=cost_headers,
            optional_parameters_removed=optional_parameters_removed,
            diagnostics=diagnostics,
        )

    def _completion_failure(
        self,
        operation_started: float,
        attempts: int,
        error: LiteLLMError,
        optional_parameters_removed: bool,
        latency_ms: float | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> CompletionResult:
        return CompletionResult(
            raw_text=None,
            prompt_tokens=None,
            completion_tokens=None,
            reasoning_tokens=None,
            tokens_estimated=False,
            latency_ms=latency_ms,
            total_elapsed_ms=self._elapsed_ms(operation_started),
            attempts=attempts,
            optional_parameters_removed=optional_parameters_removed,
            diagnostics=diagnostics or {},
            error=error,
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _request_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
        }

    def _elapsed_ms(self, started: float) -> float:
        return (self._perf_counter() - started) * 1000

    def _backoff_seconds(self, retry_count: int) -> float:
        base = 0.5 * (2**retry_count)
        return base + (base * 0.25 * self._random_value())

    def _retry_delay(self, response: httpx.Response, retry_count: int) -> float:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        return retry_after if retry_after is not None else self._backoff_seconds(retry_count)

    def _is_unsupported_parameter_error(self, response: httpx.Response) -> bool:
        message = _response_message(response).lower()
        parameter_mentioned = any(parameter in message for parameter in OPTIONAL_PARAMETERS)
        unsupported_wording = any(
            phrase in message
            for phrase in (
                "not support",
                "unsupported",
                "unrecognized",
                "unknown parameter",
                "unexpected keyword",
                "extra inputs",
            )
        )
        return parameter_mentioned and unsupported_wording

    def _response_error(self, response: httpx.Response) -> LiteLLMError:
        status = response.status_code
        detail = _response_message(response)
        generic = f"LiteLLM returned HTTP {status}."
        message = f"{generic} {detail}" if detail else generic
        return LiteLLMError(
            code="http_error",
            message=self._redact(message),
            status_code=status,
            retryable=status in RETRYABLE_STATUS_CODES,
        )

    def _redact(self, value: str) -> str:
        clean = " ".join(value.split())[:500]
        return clean.replace(self._api_key, "[REDACTED]")


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = [
            part["text"]
            for part in content
            if isinstance(part, Mapping) and isinstance(part.get("text"), str)
        ]
        if pieces:
            return "".join(pieces)
    raise ValueError("completion content is not textual")


def _completion_diagnostics(data: Any) -> dict[str, Any]:
    """Extract safe, model-agnostic response metadata without reasoning text."""

    diagnostics: dict[str, Any] = {
        "response_type": type(data).__name__,
        "content_state": "unavailable",
        "reasoning_present": False,
    }
    if not isinstance(data, Mapping):
        return diagnostics
    for source, target in (
        ("object", "response_object"),
        ("model", "response_model"),
        ("system_fingerprint", "system_fingerprint"),
    ):
        value = data.get(source)
        if isinstance(value, str) and value:
            diagnostics[target] = value[:200]
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return diagnostics
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str):
        diagnostics["finish_reason"] = finish_reason[:100]
    message = choice.get("message")
    if not isinstance(message, Mapping):
        return diagnostics
    content = message.get("content")
    if content is None:
        diagnostics["content_state"] = "null"
    elif isinstance(content, str):
        diagnostics["content_state"] = "text"
    elif isinstance(content, list):
        diagnostics["content_state"] = "parts"
    else:
        diagnostics["content_state"] = type(content).__name__
    provider_fields = message.get("provider_specific_fields")
    provider_reasoning = (
        provider_fields.get("reasoning") if isinstance(provider_fields, Mapping) else None
    )
    reasoning = message.get("reasoning_content")
    diagnostics["reasoning_present"] = bool(reasoning or provider_reasoning)
    return diagnostics


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _response_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, Mapping):
        return ""
    error = data.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("message"), str):
        return " ".join(error["message"].split())[:400]
    for key in ("detail", "message"):
        value = data.get(key)
        if isinstance(value, str):
            return " ".join(value.split())[:400]
    return ""


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except ValueError:
        pass
    try:
        retry_at = email.utils.parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
