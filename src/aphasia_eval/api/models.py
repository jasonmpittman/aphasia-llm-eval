"""Model discovery and pricing-status API."""

from __future__ import annotations

from flask import Blueprint, jsonify

from aphasia_eval.extensions import get_litellm_client, get_pricing_service

blueprint = Blueprint("models_api", __name__)


@blueprint.get("/api/models")
def list_models():
    """List selectable models even when discovery degrades to an allowlist."""

    client = get_litellm_client()
    pricing = get_pricing_service()
    discovery = client.list_models()
    pricing.refresh_proxy_pricing()
    models = []
    for model_id in discovery.models:
        if model_id in pricing.overrides:
            price_source = "override"
            token_pricing = pricing.overrides[model_id]
        elif model_id in pricing.proxy_pricing:
            price_source = "proxy_pricing"
            token_pricing = pricing.proxy_pricing[model_id]
        else:
            price_source = "unknown"
            token_pricing = None
        models.append(
            {
                "id": model_id,
                "pricing_source": price_source,
                "input_usd_per_1m_tokens": (
                    token_pricing.input_cost_per_token * 1_000_000 if token_pricing else None
                ),
                "output_usd_per_1m_tokens": (
                    token_pricing.output_cost_per_token * 1_000_000 if token_pricing else None
                ),
            }
        )
    return jsonify(
        {
            "models": models,
            "source": discovery.source,
            "error": discovery.error.message if discovery.error is not None else None,
        }
    )
