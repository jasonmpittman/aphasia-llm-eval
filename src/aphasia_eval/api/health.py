"""Liveness endpoint."""

from flask import Blueprint, current_app, jsonify

from aphasia_eval.extensions import get_litellm_client

blueprint = Blueprint("health", __name__)


@blueprint.get("/api/health")
def health() -> tuple[dict[str, object], int]:
    """Report local liveness and current gateway reachability."""

    result = get_litellm_client().list_models()

    return (
        jsonify(
            {
                "status": "ok",
                "litellm": {
                    "configured": bool(current_app.config.get("LITELLM_API_KEY")),
                    "reachable": result.error is None,
                    "models_available": len(result.models),
                    "error": result.error.message if result.error is not None else None,
                },
            }
        ),
        200,
    )
