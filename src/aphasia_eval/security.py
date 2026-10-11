"""Request-boundary protections for the localhost-only application."""

from __future__ import annotations

from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request

from .config import is_loopback_host


def _request_hostname(host_header: str) -> str:
    if host_header.startswith("["):
        closing_bracket = host_header.find("]")
        return host_header[1:closing_bracket] if closing_bracket > 0 else ""
    return host_header.rsplit(":", 1)[0] if host_header.count(":") == 1 else host_header


def _is_loopback_origin(origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and is_loopback_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and (parsed.path in {"", "/"})
        and not parsed.query
        and not parsed.fragment
        and (port is None or 0 < port <= 65535)
    )


def _json_error(code: str, message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": {"code": code, "message": message}}), status


def install_request_protections(app: Flask) -> None:
    """Install DNS-rebinding, cross-origin, and JSON content-type checks."""

    @app.before_request
    def protect_local_boundary() -> tuple[Response, int] | None:
        if not is_loopback_host(_request_hostname(request.host)):
            return _json_error("invalid_host", "Requests must use a loopback Host header.", 400)

        if request.method in {"POST", "PATCH", "DELETE"}:
            if request.mimetype != "application/json":
                return _json_error(
                    "json_required",
                    "State-changing requests require Content-Type: application/json.",
                    415,
                )
            origin = request.headers.get("Origin")
            if origin is not None and not _is_loopback_origin(origin):
                return _json_error(
                    "invalid_origin", "Origin must refer to a loopback address.", 403
                )
        return None

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
