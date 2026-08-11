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
    assert resp.headers["Referrer-Policy"] == "no-referrer"


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
