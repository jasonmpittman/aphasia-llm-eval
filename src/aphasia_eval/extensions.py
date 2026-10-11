"""Typed accessors for application-owned services."""

from __future__ import annotations

from flask import current_app

from .db import Database
from .services.litellm_client import LiteLLMClient
from .services.orchestrator import RunOrchestrator
from .services.pricing import PricingService


def get_database() -> Database:
    return current_app.extensions["aphasia_eval_database"]


def get_litellm_client() -> LiteLLMClient:
    return current_app.extensions["aphasia_eval_litellm"]


def get_pricing_service() -> PricingService:
    return current_app.extensions["aphasia_eval_pricing"]


def get_orchestrator() -> RunOrchestrator:
    return current_app.extensions["aphasia_eval_orchestrator"]
