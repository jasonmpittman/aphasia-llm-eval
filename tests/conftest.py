from __future__ import annotations

import pytest

from aphasia_eval import create_app


@pytest.fixture
def app():
    return create_app(
        {
            "TESTING": True,
            "LITELLM_BASE_URL": "https://gateway.invalid/v1",
            "LITELLM_API_KEY": "unit-test-placeholder",  # pragma: allowlist secret
            "HOST": "127.0.0.1",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()
