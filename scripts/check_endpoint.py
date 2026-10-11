"""Probe the configured LiteLLM gateway without exposing its API key."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from aphasia_eval.config import load_config, validate_required_config
from aphasia_eval.services.endpoint_check import run_checks
from aphasia_eval.services.litellm_client import LiteLLMClient


def main(argv: Sequence[str] | None = None) -> int:
    """Load ``.env`` and execute the live endpoint checks."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        help="model ID for the tiny completion (defaults to the first discovered model)",
    )
    args = parser.parse_args(argv)

    config = load_config()
    validate_required_config(config)
    with LiteLLMClient.from_config(config) as client:
        return run_checks(client, requested_model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
