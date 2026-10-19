from __future__ import annotations

import pytest

from aphasia_eval.services.model_capabilities import (
    TextCapability,
    load_capability_overrides,
    resolve_text_capability,
)


def test_overrides_are_exact_and_take_precedence(tmp_path):
    path = tmp_path / "capabilities.yaml"
    path.write_text(
        "models:\n  audio-only:\n    text_analysis: false\n    reason: ASR endpoint.\n",
        encoding="utf-8",
    )
    overrides = load_capability_overrides(path)

    assert overrides["audio-only"] == TextCapability("unsupported", "ASR endpoint.", "local_config")
    assert (
        resolve_text_capability("audio-only", {"modalities": ["text"]}, overrides).status
        == "unsupported"
    )
    assert resolve_text_capability("AUDIO-ONLY", None, overrides).status == "unknown"


@pytest.mark.parametrize(
    "metadata, status",
    [
        ({"input_modalities": ["text", "image"]}, "supported"),
        ({"model_info": {"mode": "chat"}}, "supported"),
        ({"capabilities": {"supported_modalities": ["audio"]}}, "unsupported"),
        ({"mode": "embedding"}, "unsupported"),
        ({"owned_by": "provider"}, "unknown"),
        (None, "unknown"),
    ],
)
def test_capability_resolution_uses_metadata_not_model_names(metadata, status):
    assert resolve_text_capability("arbitrary-new-model", metadata).status == status


def test_missing_and_malformed_override_files(tmp_path):
    assert load_capability_overrides(tmp_path / "missing.yaml") == {}
    malformed = tmp_path / "bad.yaml"
    malformed.write_text("models:\n  model-a:\n    text_analysis: maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="boolean text_analysis"):
        load_capability_overrides(malformed)
