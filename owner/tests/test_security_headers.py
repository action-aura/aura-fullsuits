"""Phase 9 -- security-header coverage, including the staging/production HSTS parity gap found and
fixed this phase (HSTS previously only fired for ENV == 'production'; StagingConfig's real Caddy-
terminated HTTPS needs the same header)."""
from __future__ import annotations

import pytest


def test_csp_and_baseline_headers_present_in_every_env(client):
    resp = client.get("/health/live")
    assert resp.headers["Content-Security-Policy"].startswith("default-src 'self'")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    # 'same-origin', NOT 'no-referrer'. 'no-referrer' suppressed the Referer
    # header on this app's OWN same-origin form posts, which collides head-on
    # with Flask-WTF's WTF_CSRF_SSL_STRICT: that check requires a Referer
    # matching Host on every HTTPS POST, so under TLS every login and every
    # other CSRF-protected POST failed with 400 and no way to succeed.
    # Reproduced on a real HTTPS deployment 2026-08-19: identical request,
    # 400 without a Referer, 302 with one. 'same-origin' still sends zero
    # Referer to any third-party origin -- the privacy property 'no-referrer'
    # was chosen for -- while letting same-origin requests carry what CSRF
    # validation needs. See app/security/headers.py for the full rationale.
    assert resp.headers["Referrer-Policy"] == "same-origin"


@pytest.mark.parametrize("env_value", ["production", "staging"])
def test_hsts_present_for_production_and_staging(env_value, app):
    # ENV-driven config classes read os.environ once at module-import time
    # (same real pattern documented in retail-test-isolation-root-cause.md,
    # correct for a real single-process deployment) -- overriding
    # app.config['ENV'] directly on the already-constructed test app exercises
    # exactly the branch register_security_headers() actually checks, without
    # fighting that caching.
    app.config["ENV"] = env_value
    with app.test_client() as c:
        resp = c.get("/health/live")
        assert "Strict-Transport-Security" in resp.headers
        assert "max-age=63072000" in resp.headers["Strict-Transport-Security"]


def test_hsts_absent_in_development(client):
    # Development is plain HTTP -- an HSTS header there would be actively
    # wrong (it would tell a browser to force HTTPS against a server that
    # doesn't speak it).
    resp = client.get("/health/live")
    assert "Strict-Transport-Security" not in resp.headers
