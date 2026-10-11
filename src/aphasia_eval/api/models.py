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
        elif model_id in pricing.proxy_pricing:
            price_source = "proxy_pricing"
        else:
            price_source = "unknown"
        models.append({"id": model_id, "pricing_source": price_source})
    return jsonify(
        {
            "models": models,
            "source": discovery.source,
            "error": discovery.error.message if discovery.error is not None else None,
        }
    )
