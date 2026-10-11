"""Flask application factory for aphasia-llm-eval."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from flask import Flask

__version__ = "0.1.0"


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    """Create and configure the localhost-only Flask application."""

    from .api import api_blueprints
    from .config import is_loopback_host, load_config, validate_required_config
    from .db import Database
    from .security import install_request_protections
    from .services.litellm_client import LiteLLMClient
    from .services.orchestrator import RunOrchestrator
    from .services.pricing import PricingService
    from .views import blueprint as web_blueprint

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(load_config())
    if test_config is not None:
        app.config.from_mapping(test_config)

    app.debug = False
    validate_required_config(app.config)

    bind_host = str(app.config["HOST"])
    if not is_loopback_host(bind_host):
        if not app.config.get("ALLOW_NON_LOCAL_BIND", False):
            raise RuntimeError(
                f"Refusing to bind to non-loopback HOST={bind_host!r}. "
                "Set ALLOW_NON_LOCAL_BIND=true only if you accept that this app has "
                "no authentication."
            )
        app.logger.warning(
            "ALLOW_NON_LOCAL_BIND is enabled for HOST=%s; this application has no authentication.",
            bind_host,
        )

    logging.getLogger().setLevel(str(app.config.get("LOG_LEVEL", "INFO")).upper())
    database = app.config.get("DATABASE") or Database(str(app.config["DATABASE_URL"]))
    database.create_all()
    stale_count = database.mark_stale_runs_interrupted()
    if stale_count:
        app.logger.warning("Marked %s stale run(s) as interrupted.", stale_count)

    litellm_client = app.config.get("LITELLM_CLIENT") or LiteLLMClient.from_config(app.config)
    pricing = app.config.get("PRICING_SERVICE") or PricingService(
        override_path=str(app.config["PRICING_OVERRIDES_PATH"]),
        client=litellm_client,
    )
    orchestrator = app.config.get("RUN_ORCHESTRATOR") or RunOrchestrator(
        database=database,
        client=litellm_client,
        pricing=pricing,
        app_version=__version__,
        litellm_host=str(app.config["LITELLM_BASE_URL"]),
        max_concurrency_total=int(app.config["MAX_CONCURRENCY_TOTAL"]),
        max_concurrency_per_model=int(app.config["MAX_CONCURRENCY_PER_MODEL"]),
        synchronous=bool(app.config.get("RUN_SYNCHRONOUS", False)),
    )
    app.extensions["aphasia_eval_database"] = database
    app.extensions["aphasia_eval_litellm"] = litellm_client
    app.extensions["aphasia_eval_pricing"] = pricing
    app.extensions["aphasia_eval_orchestrator"] = orchestrator

    install_request_protections(app)
    app.register_blueprint(web_blueprint)
    for blueprint in api_blueprints:
        app.register_blueprint(blueprint)
    return app


__all__ = ["create_app"]
