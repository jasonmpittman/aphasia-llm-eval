"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

DEFAULT_LITELLM_BASE_URL = "https://reallms.rescloud.iu.edu/direct/v1"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _as_bool(value: str | bool | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}.") from error


def _as_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number, got {raw!r}.") from error


def load_config() -> dict[str, Any]:
    """Load the local ``.env`` file and return Flask configuration values."""

    load_dotenv()
    return {
        "LITELLM_BASE_URL": os.getenv("LITELLM_BASE_URL"),
        "LITELLM_API_KEY": os.getenv("LITELLM_API_KEY"),
        "HOST": os.getenv("HOST", "127.0.0.1"),
        "PORT": _as_int("PORT", 5000),
        "SECRET_KEY": os.getenv("FLASK_SECRET_KEY", "change-me"),
        "DATABASE_URL": os.getenv("DATABASE_URL", "sqlite:///instance/aphasia_eval.db"),
        "TASKS_DIR": os.getenv("TASKS_DIR", "tasks"),
        "GOLD_TASKS_DIR": os.getenv("GOLD_TASKS_DIR", "gold_tasks"),
        "MAX_CONCURRENCY_PER_MODEL": _as_int("MAX_CONCURRENCY_PER_MODEL", 4),
        "MAX_CONCURRENCY_TOTAL": _as_int("MAX_CONCURRENCY_TOTAL", 8),
        "MAX_REQUESTS_PER_RUN": _as_int("MAX_REQUESTS_PER_RUN", 5000),
        "REQUEST_TIMEOUT_SECONDS": _as_float("REQUEST_TIMEOUT_SECONDS", 120.0),
        "MAX_RETRIES": _as_int("MAX_RETRIES", 3),
        "DEFAULT_TEMPERATURE": _as_float("DEFAULT_TEMPERATURE", 0.0),
        "DEFAULT_MAX_OUTPUT_TOKENS": _as_int("DEFAULT_MAX_OUTPUT_TOKENS", 2048),
        "DEFAULT_REPETITIONS": _as_int("DEFAULT_REPETITIONS", 1),
        "MODEL_ALLOWLIST": os.getenv("MODEL_ALLOWLIST", ""),
        "PRICING_OVERRIDES_PATH": os.getenv(
            "PRICING_OVERRIDES_PATH", "config/pricing_overrides.yaml"
        ),
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
        "ALLOW_NON_LOCAL_BIND": _as_bool(os.getenv("ALLOW_NON_LOCAL_BIND")),
        "DEBUG": False,
    }


def validate_required_config(config: dict[str, Any]) -> None:
    """Fail with an actionable error when required gateway settings are absent."""

    missing = [
        name
        for name in ("LITELLM_BASE_URL", "LITELLM_API_KEY")
        if not str(config.get(name, "")).strip()
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"Missing required configuration: {joined}. "
            "Copy .env.example to .env and set the required values."
        )


def is_loopback_host(host: str) -> bool:
    """Return whether *host* is one of the explicitly supported loopback names."""

    return host.strip().lower().strip("[]") in LOOPBACK_HOSTS
