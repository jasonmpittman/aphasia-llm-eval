from __future__ import annotations

import logging
import re

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


def test_frontend_pages_render_without_inline_scripts(client):
    response = client.get("/", headers={"Host": "localhost:5000"})

    assert response.status_code == 200
    assert b"Compare models against verified language-analysis gold" in response.data
    assert b'id="model-list"' in response.data
    assert b'id="task-list"' in response.data
    assert b'id="confirmation-modal"' in response.data
    assert b'data-bs-theme="dark"' in response.data
    assert b'id="app-sidebar"' in response.data
    assert b'id="sidebar-toggle"' in response.data
    assert b'class="utility-header"' in response.data
    assert b"bento-hero-grid" in response.data
    assert "Access-Control-Allow-Origin" not in response.headers

    run_page = client.get("/runs/example-id", headers={"Host": "localhost:5000"})
    assert run_page.status_code == 200
    assert b'data-run-id="example-id"' in run_page.data
    assert b'id="overview-tab"' in run_page.data
    assert b'id="efficiency-tab"' in run_page.data
    assert b'id="task-detail-tab"' in run_page.data
    assert b'id="items-tab"' in run_page.data
    assert b'id="run-info-tab"' in run_page.data
    assert b"CSV package" in run_page.data
    assert b"Excel workbook" in run_page.data

    history = client.get("/history", headers={"Host": "localhost:5000"})
    assert history.status_code == 200
    assert b"Run history" in history.data
    assert b'id="history-body"' in history.data

    for page in (response, run_page, history):
        html = page.get_data(as_text=True)
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
        assert 'href="#main-content"' in html
        assert "Research evaluation only" in html


@pytest.mark.parametrize(
    "path",
    [
        "/static/css/app.css",
        "/static/js/common.js",
        "/static/js/select.js",
        "/static/js/run.js",
        "/static/js/history.js",
        "/static/vendor/bootstrap.min.css",
        "/static/vendor/bootstrap.bundle.min.js",
        "/static/vendor/chart.umd.min.js",
    ],
)
def test_frontend_assets_are_vendored(client, path):
    response = client.get(path, headers={"Host": "localhost"})

    assert response.status_code == 200


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


def test_non_json_patch_is_rejected(client):
    response = client.patch(
        "/api/runs/missing",
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
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "style-src 'self'" in policy
    assert "'unsafe-inline'" not in policy
