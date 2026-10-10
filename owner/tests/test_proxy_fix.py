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
