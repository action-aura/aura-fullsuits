"""OWNER_TRUSTED_PROXY_COUNT must actually be APPLIED (found 2026-10-10).

It was validated (>=1 in production) but nothing wrapped the WSGI app, so
behind Caddy every caller had Caddy's IP: the device API rate limits were one
bucket for the whole fleet, audit IPs were all the proxy, and invitation links
were http://. These tests drive real requests through create_app()'s real WSGI
stack, with the config count patched on the class before the app is built.

ALLOWED_HOSTS (config.ALLOWED_HOSTS) is only validated non-empty in
staging/production; nothing in app/ enforces it against the Host header. That
is reported, not changed here.
"""
from __future__ import annotations

import json

import pytest

from app.config import TestingConfig
from app.licensing_service.ratelimit import POLICIES

_ACTIVATIONS = "/api/licensing/v1/activations"


def _make_app(request, monkeypatch, count: int):
    monkeypatch.setattr(TestingConfig, "TRUSTED_PROXY_COUNT", count)
    application = request.getfixturevalue("app")

    @application.get("/_probe_client")
    def _probe_client():
        from flask import request as flask_request

        return {"addr": flask_request.remote_addr, "scheme": flask_request.scheme}

    return application


@pytest.fixture()
def proxied_app(request, monkeypatch):
    return _make_app(request, monkeypatch, 1)


@pytest.fixture()
def unproxied_app(request, monkeypatch):
    return _make_app(request, monkeypatch, 0)


def _post_activation(client, forwarded_for: str):
    return client.post(
        _ACTIVATIONS,
        data=json.dumps({}),
        content_type="application/json",
        headers={"X-Forwarded-For": forwarded_for},
    )


def test_count_one_uses_forwarded_for_and_proto(proxied_app):
    resp = proxied_app.test_client().get(
        "/_probe_client", headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
    )
    assert resp.get_json() == {"addr": "203.0.113.9", "scheme": "https"}


def test_count_zero_ignores_forwarded_headers(unproxied_app):
    resp = unproxied_app.test_client().get(
        "/_probe_client", headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
    )
    body = resp.get_json()
    assert body["addr"] != "203.0.113.9"
    assert body["scheme"] == "http"


def test_distinct_forwarded_ips_get_distinct_activation_rate_limit_buckets(proxied_app, seeded, signing_key):
    limit = POLICIES["activation"].max_requests
    client = proxied_app.test_client()
    statuses_a = [_post_activation(client, "203.0.113.10").status_code for _ in range(limit + 1)]
    assert statuses_a[-1] == 429, statuses_a
    assert 429 not in statuses_a[:-1], statuses_a
    # A different client IP behind the same proxy must still have its own budget.
    assert _post_activation(client, "203.0.113.11").status_code != 429


def test_without_proxy_trust_all_callers_share_one_bucket(unproxied_app, seeded, signing_key):
    """The control: this is the pre-fix behaviour, kept so the test above is
    known to be sensitive to the bucket key and not passing vacuously."""
    limit = POLICIES["activation"].max_requests
    client = unproxied_app.test_client()
    for _ in range(limit):
        _post_activation(client, "203.0.113.10")
    assert _post_activation(client, "203.0.113.11").status_code == 429


# ---------------------------------------------------------------------------
# Host / X-Forwarded-Host validation (found by adversarial verification
# 2026-10-10, before go-live). Reset and invitation links are built from the
# Host header; without an allow-list a forged Host put the attacker's domain in
# OUR password-reset mail. These drive the real create_app() WSGI stack.
# ---------------------------------------------------------------------------
def _hosts_app(request, monkeypatch, *, hosts, proxies=0):
    monkeypatch.setattr(TestingConfig, "ALLOWED_HOSTS", hosts)
    monkeypatch.setattr(TestingConfig, "TRUSTED_PROXY_COUNT", proxies)
    return request.getfixturevalue("app")


def _forgot_post(application, email, headers=None, base="http://owner.example.com"):
    from tests.conftest import get_csrf

    client = application.test_client()
    form = client.get("/auth/forgot-password", base_url=base, headers=headers or {})
    if form.status_code != 200:
        return form
    csrf = get_csrf(form.get_data(as_text=True))
    return client.post("/auth/forgot-password", data={"csrf_token": csrf, "email": email}, base_url=base, headers=headers or {})


def _outbox_count(application):
    from sqlalchemy import func, select

    from app.extensions import db_session
    from app.models.email_outbox import EmailOutbox
    from app.models.staff import LoginAttempt

    with application.app_context():
        return (
            db_session.execute(select(func.count(EmailOutbox.id))).scalar_one(),
            db_session.execute(select(func.count(LoginAttempt.id))).scalar_one(),
        )


def test_forged_host_header_is_refused_and_writes_nothing(request, monkeypatch):
    application = _hosts_app(request, monkeypatch, hosts=("owner.example.com",))
    ok = _forgot_post(application, "nobody@example.com")                       # positive control: real host works
    assert ok.status_code == 200
    before = _outbox_count(application)
    # The whole flow (form GET for a valid CSRF token, then the POST) is driven
    # AGAINST the forged host, so a 400 can only come from the host check -- a
    # bare POST would be refused by CSRF whether or not the host guard ran.
    resp = _forgot_post(application, "nobody@example.com", base="http://evil.example")
    assert resp.status_code == 400
    assert _outbox_count(application) == before


def test_forged_x_forwarded_host_is_refused_behind_proxyfix(request, monkeypatch):
    application = _hosts_app(request, monkeypatch, hosts=("owner.example.com",), proxies=1)
    resp = _forgot_post(application, "nobody@example.com", headers={"X-Forwarded-Host": "evil.example"})
    assert resp.status_code == 400
    good = application.test_client().get(
        "/auth/forgot-password", base_url="http://127.0.0.1", headers={"X-Forwarded-Host": "owner.example.com"}
    )
    assert good.status_code == 200      # control: a forwarded host that IS allowed passes


def test_loopback_hosts_stay_allowed_for_the_container_healthcheck(request, monkeypatch):
    application = _hosts_app(request, monkeypatch, hosts=("owner.example.com",))
    for base in ("http://127.0.0.1:5000", "http://localhost:5000"):
        assert application.test_client().get("/health/live", base_url=base).status_code == 200, base


def test_empty_allowed_hosts_enforces_nothing(request, monkeypatch):
    application = _hosts_app(request, monkeypatch, hosts=())
    assert "TRUSTED_HOSTS" not in application.config or not application.config["TRUSTED_HOSTS"]
    assert application.test_client().get("/auth/forgot-password", base_url="http://anything.example").status_code == 200
