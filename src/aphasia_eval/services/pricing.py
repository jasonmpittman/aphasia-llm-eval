"""Deterministic estimated-cost resolution for LiteLLM results."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from .litellm_client import LiteLLMClient, ModelInfoResult

CostSource = Literal["override", "header", "proxy_pricing", "unknown"]
HEADER_COST_NAMES = ("x-litellm-response-cost",)


@dataclass(frozen=True, slots=True)
class TokenPricing:
    """Per-token input and output prices in USD."""

    input_cost_per_token: float
    output_cost_per_token: float


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A cost value and the source used to derive it."""

    cost_usd: float | None
    cost_source: CostSource


def load_pricing_overrides(path: str | Path) -> dict[str, TokenPricing]:
    """Load and validate USD-per-million-token model overrides with safe YAML."""

    override_path = Path(path)
    if not override_path.exists():
        return {}
    try:
        document = yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(
            f"Could not read pricing overrides from {override_path}: {error}"
        ) from error

    if not isinstance(document, Mapping):
        raise ValueError("Pricing override file must contain a mapping.")
    models = document.get("models", document)
    if not isinstance(models, Mapping):
        raise ValueError("Pricing override 'models' value must be a mapping.")

    result: dict[str, TokenPricing] = {}
    for model, entry in models.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(entry, Mapping):
            raise ValueError("Each pricing override must map a model ID to a pricing mapping.")
        input_per_million = _required_price(entry, "input_usd_per_1m_tokens", model)
        output_per_million = _required_price(entry, "output_usd_per_1m_tokens", model)
        result[model.strip()] = TokenPricing(
            input_cost_per_token=input_per_million / 1_000_000,
            output_cost_per_token=output_per_million / 1_000_000,
        )
    return result


def parse_proxy_pricing(payload: Any) -> dict[str, TokenPricing]:
    """Normalize LiteLLM ``/model/info`` payloads into per-token prices."""

    if not isinstance(payload, Mapping):
        return {}
    entries = payload.get("data")
    if not isinstance(entries, list):
        return {}

    pricing: dict[str, TokenPricing] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        info = entry.get("model_info")
        info = info if isinstance(info, Mapping) else entry
        model = _first_string(entry, "model_name", "model", "id") or _first_string(
            info, "model_name", "model", "id"
        )
        input_price = _optional_price(info.get("input_cost_per_token"))
        output_price = _optional_price(info.get("output_cost_per_token"))
        if model and input_price is not None and output_price is not None:
            pricing[model] = TokenPricing(input_price, output_price)
    return pricing


def resolve_cost(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    response_headers: Mapping[str, str] | None = None,
    overrides: Mapping[str, TokenPricing] | None = None,
    proxy_pricing: Mapping[str, TokenPricing] | None = None,
) -> CostEstimate:
    """Resolve estimated cost in required override/header/proxy/unknown order."""

    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("Token counts must not be negative.")

    override = (overrides or {}).get(model)
    if override is not None:
        return CostEstimate(
            cost_usd=_token_cost(prompt_tokens, completion_tokens, override),
            cost_source="override",
        )

    header_cost = _cost_from_headers(response_headers or {})
    if header_cost is not None:
        return CostEstimate(cost_usd=header_cost, cost_source="header")

    proxy = (proxy_pricing or {}).get(model)
    if proxy is not None:
        return CostEstimate(
            cost_usd=_token_cost(prompt_tokens, completion_tokens, proxy),
            cost_source="proxy_pricing",
        )
    return CostEstimate(cost_usd=None, cost_source="unknown")


class PricingService:
    """Hold local overrides and cached optional proxy pricing metadata."""

    def __init__(
        self,
        *,
        override_path: str | Path = "config/pricing_overrides.yaml",
        client: LiteLLMClient | None = None,
    ) -> None:
        self.overrides = load_pricing_overrides(override_path)
        self.proxy_pricing: dict[str, TokenPricing] = {}
        self.client = client
        self.proxy_error = None

    def refresh_proxy_pricing(self) -> ModelInfoResult | None:
        """Fetch pricing metadata; unavailable endpoints are nonfatal."""

        if self.client is None:
            return None
        result = self.client.get_model_info()
        self.proxy_error = result.error
        self.proxy_pricing = parse_proxy_pricing(result.data) if result.error is None else {}
        return result

    def resolve(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        response_headers: Mapping[str, str] | None = None,
    ) -> CostEstimate:
        """Resolve one request's estimated cost from the available sources."""

        return resolve_cost(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            response_headers=response_headers,
            overrides=self.overrides,
            proxy_pricing=self.proxy_pricing,
        )


def _required_price(entry: Mapping[str, Any], key: str, model: str) -> float:
    value = _optional_price(entry.get(key))
    if value is None:
        raise ValueError(f"Pricing override for {model!r} requires a nonnegative {key}.")
    return value


def _optional_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _first_string(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _cost_from_headers(headers: Mapping[str, str]) -> float | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for name in HEADER_COST_NAMES:
        value = _optional_price(normalized.get(name))
        if value is not None:
            return value
    return None


def _token_cost(prompt_tokens: int, completion_tokens: int, pricing: TokenPricing) -> float:
    return (
        prompt_tokens * pricing.input_cost_per_token
        + completion_tokens * pricing.output_cost_per_token
    )
