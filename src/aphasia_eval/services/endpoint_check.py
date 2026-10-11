"""Human-readable checks for uncertain LiteLLM gateway capabilities."""

from __future__ import annotations

from collections.abc import Callable

from .litellm_client import LiteLLMClient

Output = Callable[[str], None]


def run_checks(
    client: LiteLLMClient,
    *,
    requested_model: str | None = None,
    output: Output = print,
) -> int:
    """Run the live checks and return zero when the completion succeeds."""

    discovery = client.list_models()
    if discovery.error is not None:
        output(f"[models endpoint] unavailable: {discovery.error.message}")
        if discovery.source == "allowlist" and discovery.models:
            output("[models fallback] using MODEL_ALLOWLIST")
    else:
        output(f"[models endpoint] available ({len(discovery.models)} model(s))")

    if discovery.models:
        output("Models:")
        for model in discovery.models:
            output(f"  - {model}")
    else:
        output("No models are available; the completion checks were skipped.")
        return 1

    if requested_model is not None and requested_model not in discovery.models:
        output(f"Requested model {requested_model!r} is not in the available model list.")
        return 1
    model = requested_model or discovery.models[0]

    pricing = client.get_model_info()
    if pricing.error is None:
        output("[pricing endpoint] available")
    else:
        status = f" (HTTP {pricing.error.status_code})" if pricing.error.status_code else ""
        output(f"[pricing endpoint] unavailable{status}; proxy pricing is optional")

    output(f"Sending one minimal completion with model: {model}")
    completion = client.chat_completion(
        model=model,
        messages=(
            {"role": "system", "content": "Reply with the single word OK."},
            {"role": "user", "content": "Reply now."},
        ),
        temperature=0,
        max_tokens=2,
    )
    if completion.optional_parameters_removed:
        output("[optional parameters] rejected; retry without them was attempted")
    elif completion.error is None:
        output("[optional parameters] accepted")
    else:
        output("[optional parameters] unknown because the completion failed")

    if completion.error is not None:
        output(f"[completion] failed: {completion.error.message}")
        output("[cost header] not checked")
        output("[usage] not checked")
        return 1

    output("[completion] succeeded")
    output(
        "[cost header] present"
        if completion.cost_headers
        else "[cost header] absent; override/proxy/unknown fallback applies"
    )
    output(
        "[usage] absent; token counts were estimated"
        if completion.tokens_estimated
        else "[usage] present"
    )
    return 0
