"""Flask application factory for aphasia-llm-eval."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from flask import Flask

from .api import health_blueprint
from .config import is_loopback_host, load_config, validate_required_config
from .security import install_request_protections
from .views import blueprint as web_blueprint

__version__ = "0.1.0"


def create_app(test_config: Mapping[str, Any] | None = None) -> Flask:
    """Create and configure the localhost-only Flask application."""

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
    install_request_protections(app)
    app.register_blueprint(web_blueprint)
    app.register_blueprint(health_blueprint)
    return app


__all__ = ["create_app"]
