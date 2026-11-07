"""Local-only browser boundary, security headers, and request size policy."""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import Flask, Response, abort, g, request

from evaluator.config import BootstrapSettings

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


def _without_port(host: str) -> str:
    if host.startswith("["):
        return host[1 : host.find("]")].lower() if "]" in host else host.lower()
    return host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()


def is_allowed_host(host: str) -> bool:
    return _without_port(host) in {"localhost", "127.0.0.1", "::1"}


def is_allowed_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def install_web_security(app: Flask, settings: BootstrapSettings, max_upload_bytes: int) -> None:
    if max_upload_bytes <= 0:
        raise ValueError("max_upload_bytes must be positive")
    # Multipart framing needs bounded headroom; import service still enforces exact file bytes.
    app.config["MAX_CONTENT_LENGTH"] = max_upload_bytes + 64 * 1024

    @app.before_request
    def enforce_local_browser_boundary() -> None:
        if not is_allowed_host(request.host):
            abort(400, description="Ongeldige Host-header.")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            # Some privacy tools serialize a same-origin form submission as Origin: null.
            # GUI mutations still require an unguessable session-bound CSRF token below.
            null_origin_gui_form = origin == "null" and request.blueprint == "gui"
            if origin is not None and not null_origin_gui_form and not is_allowed_origin(origin):
                abort(403, description="Cross-origin mutatie is geweigerd.")
        g.csp_nonce = secrets.token_urlsafe(18)

    @app.after_request
    def security_headers(response: Response) -> Response:
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            f"script-src 'nonce-{nonce}'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; object-src 'none'"
        )
        return response

    app.jinja_env.globals["csp_nonce"] = lambda: getattr(g, "csp_nonce", "")


def constant_time_equal(left: str, right: str) -> bool:
    """Small public helper used by security policy tests."""
    return hmac.compare_digest(left, right)
