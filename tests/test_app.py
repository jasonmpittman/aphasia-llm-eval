from __future__ import annotations

import logging

import pytest

from aphasia_eval import create_app


def _config(**overrides):
    config = {
        "TESTING": True,
        "LITELLM_BASE_URL": "https://gateway.invalid/v1",
        "LITELLM_API_KEY": "unit-test-placeholder",  # pragma: allowlist secret
        "HOST": "127.0.0.1",
    }
    config.update(overrides)
    return config


def test_missing_required_config_has_actionable_error(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match=r"\.env\.example"):
        create_app({"LITELLM_BASE_URL": "", "LITELLM_API_KEY": ""})


def test_refuses_non_loopback_bind():
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        create_app(_config(HOST="0.0.0.0"))


def test_non_loopback_override_logs_warning(caplog):
    with caplog.at_level(logging.WARNING):
        app = create_app(_config(HOST="0.0.0.0", ALLOW_NON_LOCAL_BIND=True))

    assert app.config["HOST"] == "0.0.0.0"
    assert "no authentication" in caplog.text


def test_placeholder_index(client):
    response = client.get("/", headers={"Host": "localhost:5000"})

    assert response.status_code == 200
    assert b"Application scaffold ready" in response.data
    assert "Access-Control-Allow-Origin" not in response.headers


def test_health(client):
    response = client.get("/api/health", headers={"Host": "127.0.0.1:5000"})

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "litellm": {
            "configured": True,
            "reachable": True,
            "models_available": 4,
            "error": None,
        },
    }


@pytest.mark.parametrize("host", ["example.com", "192.168.1.20:5000", "evil.localhost.test"])
def test_foreign_host_is_rejected(client, host):
    response = client.get("/api/health", headers={"Host": host})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_host"


@pytest.mark.parametrize("host", ["localhost", "localhost:8000", "[::1]:5000"])
def test_loopback_host_is_allowed(client, host):
    response = client.get("/api/health", headers={"Host": host})

    assert response.status_code == 200


def test_foreign_origin_is_rejected(client):
    response = client.post(
        "/api/health",
        json={},
        headers={"Host": "localhost", "Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "invalid_origin"


def test_non_json_post_is_rejected(client):
    response = client.post(
        "/api/health",
        data="not json",
        headers={"Host": "localhost", "Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.get_json()["error"]["code"] == "json_required"


def test_loopback_origin_reaches_normal_routing(client):
    response = client.post(
        "/api/health",
        json={},
        headers={"Host": "localhost", "Origin": "http://127.0.0.1:5000"},
    )

    assert response.status_code == 405


def test_security_headers_are_present(client):
    response = client.get("/api/health", headers={"Host": "localhost"})

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
