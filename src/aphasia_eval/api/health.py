"""Liveness endpoint."""

from flask import Blueprint, current_app, jsonify

blueprint = Blueprint("health", __name__)


@blueprint.get("/api/health")
def health() -> tuple[dict[str, object], int]:
    """Report local liveness; gateway reachability is added with the client in M1."""

    return (
        jsonify(
            {
                "status": "ok",
                "litellm": {
                    "configured": bool(current_app.config.get("LITELLM_API_KEY")),
                    "reachable": None,
                },
            }
        ),
        200,
    )
