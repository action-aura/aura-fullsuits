"""Aura Retail -- the AI Assistant must refuse FAST when it has no server.

THE BUG THIS PINS. AURA_AI_ENDPOINT_URL used to default to the demo droplet's
own address, https://104-248-35-215.sslip.io/api/generate. config.py argued for
that default, and the argument was sound when written:

    "An unset token here means the /ai/chat route's request to the droplet gets
     a 401, which the route already treats as 'AI assistant is temporarily
     unavailable' -- fails closed and gracefully, never a crash."

That depends on a droplet being there to answer 401. The droplet is gone -- the
DigitalOcean account was locked and both were destroyed -- and sslip.io still
resolves the IP straight out of the hostname, so DNS succeeds and the TCP
connect goes to an unrouted address. No RST, no 401, just silence until
AURA_AI_TIMEOUT_SECONDS (45 seconds) expires, because the route only catches
RequestException AFTER the wait. The documented graceful failure had become a
45-second hang ending in a message that was wrong twice over: nothing was
temporary, and nothing was configured.

WHAT IS ASSERTED, and the first one is the point. "Returns 503" is not enough --
the old behaviour also returned 503, just three quarters of a minute later. The
observable difference is that NO REQUEST IS MADE AT ALL, so these tests fail the
build if the guard is removed even though the status code would still look
right.

Both directions are covered, because a guard that refuses everything would pass
the deny half and silently disable a real feature:

  1. no endpoint  -> 503, distinct message, requests.post never called
  2. no endpoint, stream=True -> same (the guard sits before the branch)
  3. an endpoint  -> the route still reaches upstream and answers 200

Run:
    pytest products/retail/tests/retail_ai_unconfigured_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import requests

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_ai_unconf_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402

CHAT_URL = '/api/sub/retail/ai/chat'
MESSAGE = 'How do I add a new product?'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user():
    """One real login. The route resolves the company id from the session
    before doing anything else, so an unauthenticated call would never reach
    the guard under test."""
    email = f'ai-unconf-{uuid.uuid4().hex[:8]}@test.local'
    password = 'AiUnconfTestPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


@pytest.fixture
def client():
    return _make_user()


class _PostSpy:
    """Records calls and refuses to perform one.

    Deliberately RAISES rather than returning a canned reply: if the guard ever
    stops working, the test fails with this explicit message instead of quietly
    exercising the upstream path and reporting something vaguer.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, *a, **kw):
        self.calls += 1
        raise AssertionError(
            'requests.post was called even though AURA_AI_ENDPOINT_URL is empty. '
            'The route must refuse before opening a socket -- that is the whole '
            'difference between failing in milliseconds and hanging for the full '
            f'{retail_api_module.AURA_AI_TIMEOUT_SECONDS}s timeout.'
        )


def test_unconfigured_endpoint_refuses_without_opening_a_socket(client, monkeypatch):
    spy = _PostSpy()
    monkeypatch.setattr(requests, 'post', spy)
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', '')

    r = client.post(CHAT_URL, json={'message': MESSAGE})

    assert r.status_code == 503, r.get_json()
    assert spy.calls == 0, 'the route opened a connection before checking its own configuration'


def test_unconfigured_streaming_request_is_refused_too(client, monkeypatch):
    # The guard must sit BEFORE the stream/non-stream split. A guard placed
    # inside the non-streaming branch would leave the streaming path hanging on
    # a dead host, and sub-ai.js opts into streaming.
    spy = _PostSpy()
    monkeypatch.setattr(requests, 'post', spy)
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', '')

    r = client.post(CHAT_URL, json={'message': MESSAGE, 'stream': True})

    assert r.status_code == 503, r.get_json()
    assert spy.calls == 0, 'the streaming branch opened a connection to an unconfigured endpoint'


def test_unconfigured_message_does_not_claim_the_outage_is_temporary(client, monkeypatch):
    # "Temporarily unavailable" tells an owner to wait for something that is
    # never coming. When the feature was never configured, the actionable fact
    # is that a setting is missing.
    monkeypatch.setattr(requests, 'post', _PostSpy())
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', '')

    body = client.post(CHAT_URL, json={'message': MESSAGE}).get_json()
    error = (body or {}).get('error', '')

    assert 'not configured' in error.lower(), (
        f'expected the message to say the assistant is not configured; got {error!r}'
    )
    assert 'temporarily' not in error.lower(), (
        'an install that was never given an AI endpoint is not a temporary outage, '
        f'and saying so sends the owner to wait instead of to the setting. Got {error!r}'
    )


def test_a_configured_endpoint_still_reaches_upstream(client, monkeypatch):
    """THE ALLOW HALF.

    A guard that refused every request would satisfy all three checks above
    while disabling a working feature. This proves the route still calls
    upstream and returns its reply whenever an endpoint IS set.
    """
    calls = {'n': 0}

    class _Reply:
        status_code = 200

        @staticmethod
        def json():
            return {'response': 'You can add a product from the Products screen.'}

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls['n'] += 1
        assert url == 'http://ai.test.local/api/generate', f'route posted to {url!r}'
        return _Reply()

    monkeypatch.setattr(requests, 'post', _fake_post)
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', 'http://ai.test.local/api/generate')

    r = client.post(CHAT_URL, json={'message': MESSAGE})

    assert r.status_code == 200, r.get_json()
    assert calls['n'] == 1, 'a configured endpoint must still be called exactly once'
    assert 'Products screen' in r.get_json()['data']['reply']
