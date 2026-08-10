"""Security headers + CSP, applied to every response (Part X)."""
from __future__ import annotations

from flask import Flask, Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'"
)


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def _set_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Phase 9.5C -- geolocation is now a real, explicit-action feature
        # (Milestone 12: Lead/Customer location capture, one-shot
        # getCurrentPosition() from a button click, never on page load,
        # never watchPosition). "self" allows this origin's own pages to
        # request it while still blocking any third-party/embedded
        # content -- found to be a real, structural blocker (the browser
        # silently refused the permission request entirely) via Milestone
        # 23 real-browser validation. Camera/microphone remain fully
        # denied -- unused anywhere in this application.
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        # Phase 9: staging is real HTTPS (Caddy-terminated) too, not just
        # production -- HSTS belongs to "this environment genuinely only
        # serves over TLS", which is true for staging as much as prod.
        if app.config.get("ENV") in ("production", "staging"):
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
