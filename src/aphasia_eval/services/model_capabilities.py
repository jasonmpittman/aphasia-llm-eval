"""Model-agnostic text-capability resolution from metadata and local data."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CapabilityStatus = Literal["supported", "unsupported", "unknown"]


@dataclass(frozen=True, slots=True)
class TextCapability:
    """Whether a model is explicitly suitable for text-analysis tasks."""

    status: CapabilityStatus
    reason: str
    source: str


def load_capability_overrides(path: str | Path) -> dict[str, TextCapability]:
    """Load exact-model capability overrides without embedding model names in code."""

    capability_path = Path(path)
    if not capability_path.exists():
        return {}
    try:
        data = yaml.safe_load(capability_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(
            f"could not read model capability file {capability_path}: {error}"
        ) from error
    models = data.get("models") if isinstance(data, Mapping) else None
    if not isinstance(models, Mapping):
        raise ValueError("model capability file must contain a models mapping")
    overrides: dict[str, TextCapability] = {}
    for model_id, declaration in models.items():
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model capability IDs must be non-empty strings")
        if not isinstance(declaration, Mapping) or not isinstance(
            declaration.get("text_analysis"), bool
        ):
            raise ValueError(f"model capability {model_id!r} requires boolean text_analysis")
        supported = declaration["text_analysis"]
        reason = declaration.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "Configured as text-capable." if supported else "Configured as non-text."
        overrides[model_id.strip()] = TextCapability(
            status="supported" if supported else "unsupported",
            reason=reason.strip(),
            source="local_config",
        )
    return overrides


def resolve_text_capability(
    model_id: str,
    metadata: Mapping[str, Any] | None,
    overrides: Mapping[str, TextCapability] | None = None,
) -> TextCapability:
    """Resolve text support without model-name heuristics."""

    override = (overrides or {}).get(model_id)
    if override is not None:
        return override
    if not isinstance(metadata, Mapping):
        return TextCapability("unknown", "Gateway supplied no capability metadata.", "unknown")

    candidates = [metadata]
    for key in ("model_info", "capabilities"):
        nested = metadata.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)

    for candidate in candidates:
        for key in ("input_modalities", "supported_modalities", "modalities"):
            modalities = _string_values(candidate.get(key))
            if not modalities:
                continue
            if {"text", "chat"}.intersection(modalities):
                return TextCapability(
                    "supported", "Gateway metadata declares text input support.", "gateway"
                )
            return TextCapability(
                "unsupported",
                f"Gateway metadata declares only: {', '.join(sorted(modalities))}.",
                "gateway",
            )

    for candidate in candidates:
        mode = candidate.get("mode")
        if not isinstance(mode, str):
            continue
        normalized = mode.strip().lower().replace("-", "_")
        if normalized in {"chat", "completion", "text", "text_completion", "responses"}:
            return TextCapability("supported", f"Gateway declares {mode!r} mode.", "gateway")
        if normalized in {
            "audio",
            "audio_transcription",
            "embedding",
            "image",
            "image_generation",
            "rerank",
            "speech",
            "transcription",
        }:
            return TextCapability(
                "unsupported", f"Gateway declares non-text mode {mode!r}.", "gateway"
            )

    return TextCapability(
        "unknown", "Gateway metadata does not declare text capability.", "unknown"
    )


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        values: Sequence[Any] = [value]
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        values = value
    else:
        return set()
    return {
        item.strip().lower().replace("-", "_")
        for item in values
        if isinstance(item, str) and item.strip()
    }
