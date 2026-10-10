"""
Aura Retail -- real-email wave: account mail is QUEUED (never sent inline), the
responses say plainly whether mail can leave this computer, the verification
resend has a 5 minute cooldown, and forgot-password reveals nothing about
whether an account exists.

Complements retail_email_verification_test.py (which drives the worker by hand
and checks the links work) and commercial_runtime/notifications/tests/
test_transport.py (the transport chooser, the relay payload, the toggle). What
only the real retail app can show: that the wiring in app.py registered the mail
sink, and that what a request returns is decided by configuration alone.

Self-contained bootstrap like retail_email_verification_test.py -- run on its own:

    pytest products/retail/tests/retail_real_email_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_real_email_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SMTP_HOST", None)
os.environ.pop("AURA_OWNER_LICENSING_URL", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.notifications import settings as settings_module  # noqa: E402
from commercial_runtime.notifications import smtp_client as smtp_client_module  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def no_transport(monkeypatch):
    """No SMTP host and no Owner URL: mail cannot leave this computer."""
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    monkeypatch.setattr(settings_module, '_transport_probe', None)
    monkeypatch.setattr(_app_module, '_start_notifications_worker_once', lambda *a, **k: None)


@pytest.fixture
def owner_relay_configured(monkeypatch):
    """An Owner URL is configured (a relay is possible) but this install is not
    activated, which is exactly the desktop state during sign-up."""
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    monkeypatch.setattr(settings_module, '_transport_probe', lambda: True)
    monkeypatch.setattr(_app_module, '_start_notifications_worker_once', lambda *a, **k: None)
    inline = []
    monkeypatch.setattr(smtp_client_module, 'send_email', lambda **kw: inline.append(kw))
    return inline


def _insert_user(*, email, password='RealMail1', role='employee'):
    conn = registry_conn()
    company_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,'active',0)",
        (str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:8]}', email, hash_password(password), role),
    )
    conn.commit()
    conn.close()
    return company_id


def _outbox_rows():
    conn = _app_module.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT company_id, email_type, recipient, status FROM email_outbox ORDER BY id").fetchall()]
    finally:
        conn.close()


def _signed_in(email, password='RealMail1'):
    client = app.test_client()
    assert client.post('/api/auth/login', json={'email': email, 'password': password}).status_code == 200
    return client


# -- the one real create-admin: before activation, with an Owner URL ----------

def test_create_admin_before_activation_queues_the_verification_mail_and_sends_nothing(owner_relay_configured):
    r = app.test_client().post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'first-admin@real-email.test', 'password': 'AdminPW1',
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['email'] == 'queued' and data['email_verification_sent'] is True
    rows = _outbox_rows()
    assert [(x['email_type'], x['recipient'], x['status']) for x in rows] == [
        ('verify_email', 'first-admin@real-email.test', 'QUEUED')]
    assert owner_relay_configured == [], "nothing may be sent inline"

    # The worker for that company ticks, finds no transport (no Owner client is
    # built in this test process and the install is not activated) and leaves
    # the row exactly where it was.
    worker = _app_module._get_or_create_notifications_worker(rows[0]['company_id'])
    assert worker.run_once() == {'ran': False, 'reason': 'no_transport'}
    assert _outbox_rows()[0]['status'] == 'QUEUED' and owner_relay_configured == []


# -- plain statements about whether mail can leave -----------------------------

def test_resend_says_not_configured_and_queues_nothing_when_no_mail_route_exists(no_transport):
    _insert_user(email='nomail@real-email.test')
    before = len(_outbox_rows())
    r = _signed_in('nomail@real-email.test').post('/api/auth/verify-email/resend')
    assert r.status_code == 200
    assert r.get_json()['email'] == 'not_configured' and r.get_json()['email_verification_sent'] is False
    assert len(_outbox_rows()) == before


def test_forgot_password_response_is_identical_for_a_real_and_an_unknown_address(owner_relay_configured):
    _insert_user(email='real-forgot@real-email.test')
    before = len(_outbox_rows())
    client = app.test_client()
    real = client.post('/api/auth/forgot-password', json={'email': 'real-forgot@real-email.test'})
    unknown = client.post('/api/auth/forgot-password', json={'email': 'nobody-at-all@real-email.test'})
    assert (real.status_code, real.get_json()) == (unknown.status_code, unknown.get_json())
    assert real.get_json() == {'success': True, 'email': 'queued'}
    new = _outbox_rows()[before:]
    assert [(x['email_type'], x['recipient']) for x in new] == [('password_reset', 'real-forgot@real-email.test')]


def test_forgot_password_says_not_configured_the_same_way_for_everyone(no_transport):
    _insert_user(email='real-forgot2@real-email.test')
    client = app.test_client()
    a = client.post('/api/auth/forgot-password', json={'email': 'real-forgot2@real-email.test'})
    b = client.post('/api/auth/forgot-password', json={'email': 'ghost@real-email.test'})
    assert a.get_json() == b.get_json() == {'success': True, 'email': 'not_configured'}


# -- the 5 minute resend cooldown ----------------------------------------------

def test_verification_resend_has_a_five_minute_cooldown(owner_relay_configured):
    _insert_user(email='cooldown@real-email.test')
    client = _signed_in('cooldown@real-email.test')
    before = len(_outbox_rows())

    first = client.post('/api/auth/verify-email/resend')
    assert first.get_json()['email'] == 'queued'
    second = client.post('/api/auth/verify-email/resend')
    assert second.get_json()['email'] == 'wait' and second.get_json()['email_verification_sent'] is False
    assert len(_outbox_rows()) - before == 1, "the second click within 5 minutes must queue nothing"

    # Age the first link past the window: the same click now goes through.
    conn = registry_conn()
    old = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    conn.execute("UPDATE secure_links SET created_at=? WHERE email_target=? AND purpose='email_verification'",
                 (old, 'cooldown@real-email.test'))
    conn.commit()
    conn.close()
    third = client.post('/api/auth/verify-email/resend')
    assert third.get_json()['email'] == 'queued'
    assert len(_outbox_rows()) - before == 2
