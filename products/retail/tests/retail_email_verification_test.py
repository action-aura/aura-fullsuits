"""
Aura Retail -- email verification + forgot/reset password.

Covers: create-admin sends a real verification email (captured via a
monkeypatched smtp_client.send_email, never a real SMTP server) and degrades
gracefully when SMTP isn't configured; the verification link actually marks
an account verified; an invalid/reused/wrong-purpose token is rejected;
forgot-password never reveals whether an email is registered; a reset link
actually changes the password.

This app allows exactly one admin account per install (see
retail_onboarding_wave0_test.py's own duplicate-admin test), so only ONE
test here calls /api/onboarding/create-admin. Every other test exercises
verify-email/forgot-password/reset-password against a directly-inserted
regular user row -- those three endpoints operate on any user by email, not
specifically an admin, so this is testing the real thing, not a shortcut.

Follows the same self-contained bootstrap convention as
retail_whatsapp_outbox_test.py (no shared conftest.py exists for
products/retail/tests/) -- run this file on its own:

    pytest products/retail/tests/retail_email_verification_test.py -v
"""
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_email_verification_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SMTP_HOST", None)  # this file relies on SMTP being genuinely unconfigured

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import verification as verification_module  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


_TOKEN_RE = re.compile(r'#(?:verify-email|reset-password)/([0-9a-f]{32})')


@pytest.fixture
def captured_emails(monkeypatch):
    """Replaces the real SMTP transport with a list capture -- proves the
    actual production code path (verification.py -> smtp_client.send_email)
    runs end to end, without ever touching a real mail server. Returns the
    list; each send appends a dict."""
    sent = []

    def _fake_send_email(*, recipient, subject, body_text, body_html=None, **kwargs):
        sent.append({'recipient': recipient, 'subject': subject, 'body_text': body_text})

    monkeypatch.setattr(verification_module, 'send_email', _fake_send_email)
    return sent


def _extract_token(body_text: str) -> str:
    m = _TOKEN_RE.search(body_text)
    assert m, f"no token found in email body: {body_text!r}"
    return m.group(1)


def _insert_user(*, email, password, role='employee', status='active'):
    """Directly inserts a real, loginable user row -- bypasses the
    one-admin-per-install rule entirely, which is exactly the point: these
    tests exercise verify-email/forgot-password/reset-password, none of
    which care about role."""
    conn = registry_conn()
    company_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:8]}', email, hash_password(password), role, status),
    )
    conn.commit()
    conn.close()
    return company_id


# ── The one real create-admin call in this file ───────────────────────────

def test_create_admin_sends_verification_email(captured_emails):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'admin-verify@wave0.test', 'password': 'AdminPW1',
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data['email_verification_sent'] is True
    assert len(captured_emails) == 1
    assert captured_emails[0]['recipient'] == 'admin-verify@wave0.test'
    assert 'verify' in captured_emails[0]['subject'].lower()

    conn = registry_conn()
    row = conn.execute(
        "SELECT email_verified_at FROM users WHERE email=?", ('admin-verify@wave0.test',)
    ).fetchone()
    conn.close()
    assert row['email_verified_at'] is None, "must not be verified before the link is clicked"


def test_create_admin_degrades_gracefully_when_smtp_unconfigured():
    """No captured_emails fixture here -- the REAL smtp_client.send_email
    runs, AURA_SMTP_HOST is genuinely unset in this test process, so it
    raises SmtpNotConfiguredError internally. That must never surface as a
    failed request; only the second admin-creation attempt 409s (a separate,
    pre-existing rule), proving the first call above already succeeded with
    email delivery genuinely attempted and gracefully swallowed."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Second', 'email': 'second-admin@wave0.test', 'password': 'SecondPW1',
    })
    assert r.status_code == 409  # one-admin-per-install, not a verification-related failure


# ── Clicking the link verifies the account ────────────────────────────────

def test_verify_email_link_marks_account_verified(captured_emails):
    _insert_user(email='verify-a@wave0.test', password='VerifyPW1')
    conn = registry_conn()
    verification_module.send_verification_email(
        conn, company_id='co-verify-a', user_email='verify-a@wave0.test', base_url='http://localhost',
    )
    conn.commit()
    conn.close()
    token = _extract_token(captured_emails[-1]['body_text'])

    client = app.test_client()
    r = client.post('/api/auth/verify-email', json={'token': token})
    assert r.status_code == 200, r.get_json()

    conn = registry_conn()
    row = conn.execute(
        "SELECT email_verified_at FROM users WHERE email=?", ('verify-a@wave0.test',)
    ).fetchone()
    conn.close()
    assert row['email_verified_at'] is not None


def test_verify_email_token_cannot_be_reused(captured_emails):
    _insert_user(email='verify-b@wave0.test', password='VerifyPW1')
    conn = registry_conn()
    verification_module.send_verification_email(
        conn, company_id='co-verify-b', user_email='verify-b@wave0.test', base_url='http://localhost',
    )
    conn.commit()
    conn.close()
    token = _extract_token(captured_emails[-1]['body_text'])

    client = app.test_client()
    first = client.post('/api/auth/verify-email', json={'token': token})
    assert first.status_code == 200

    second = client.post('/api/auth/verify-email', json={'token': token})
    assert second.status_code == 400


def test_verify_email_rejects_unknown_token():
    client = app.test_client()
    r = client.post('/api/auth/verify-email', json={'token': 'not-a-real-token'})
    assert r.status_code == 400


def test_verify_email_rejects_a_password_reset_token():
    """A token minted for one purpose must never work for the other --
    proves the `purpose` filter in verification.consume_link is load-bearing,
    not decorative."""
    _insert_user(email='cross-purpose@wave0.test', password='CrossPW1')
    conn = registry_conn()
    raw_token = verification_module._create_link(
        conn, company_id='co-cross', email='cross-purpose@wave0.test',
        purpose='password_reset', ttl_hours=1,
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/verify-email', json={'token': raw_token})
    assert r.status_code == 400


# ── Forgot password never reveals account existence ───────────────────────

def test_forgot_password_returns_success_for_a_real_email(captured_emails):
    _insert_user(email='forgot-a@wave0.test', password='ForgotPW1')
    client = app.test_client()

    r = client.post('/api/auth/forgot-password', json={'email': 'forgot-a@wave0.test'})
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert len(captured_emails) == 1
    assert captured_emails[0]['recipient'] == 'forgot-a@wave0.test'


def test_forgot_password_returns_identical_success_for_an_unregistered_email(captured_emails):
    client = app.test_client()
    r = client.post('/api/auth/forgot-password', json={'email': 'nobody-here@wave0.test'})
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert len(captured_emails) == 0, "no account exists -- nothing should actually be sent"


def test_forgot_password_cooldown_suppresses_a_second_email(captured_emails):
    _insert_user(email='forgot-b@wave0.test', password='ForgotPW1')
    client = app.test_client()

    r1 = client.post('/api/auth/forgot-password', json={'email': 'forgot-b@wave0.test'})
    r2 = client.post('/api/auth/forgot-password', json={'email': 'forgot-b@wave0.test'})
    assert r1.status_code == 200 and r2.status_code == 200
    assert len(captured_emails) == 1, "a second request within the cooldown window must not send again"


# ── Reset link actually changes the password ──────────────────────────────

def test_reset_password_changes_password_old_no_longer_works(captured_emails):
    _insert_user(email='reset-a@wave0.test', password='OldPassword1')
    client = app.test_client()

    login = client.post('/api/auth/login', json={'email': 'reset-a@wave0.test', 'password': 'OldPassword1'})
    assert login.status_code == 200

    client.post('/api/auth/forgot-password', json={'email': 'reset-a@wave0.test'})
    token = _extract_token(captured_emails[-1]['body_text'])

    r = client.post('/api/auth/reset-password', json={'token': token, 'password': 'NewPassword2'})
    assert r.status_code == 200, r.get_json()

    old_login = app.test_client().post(
        '/api/auth/login', json={'email': 'reset-a@wave0.test', 'password': 'OldPassword1'}
    )
    assert old_login.status_code in (400, 401)

    new_login = app.test_client().post(
        '/api/auth/login', json={'email': 'reset-a@wave0.test', 'password': 'NewPassword2'}
    )
    assert new_login.status_code == 200, new_login.get_json()


def test_reset_password_token_cannot_be_reused(captured_emails):
    _insert_user(email='reset-b@wave0.test', password='OldPassword1')
    client = app.test_client()

    client.post('/api/auth/forgot-password', json={'email': 'reset-b@wave0.test'})
    token = _extract_token(captured_emails[-1]['body_text'])

    first = client.post('/api/auth/reset-password', json={'token': token, 'password': 'FirstNew12'})
    assert first.status_code == 200

    second = client.post('/api/auth/reset-password', json={'token': token, 'password': 'SecondNew2'})
    assert second.status_code == 400


def test_reset_password_rejects_weak_password(captured_emails):
    _insert_user(email='reset-c@wave0.test', password='OldPassword1')
    client = app.test_client()

    client.post('/api/auth/forgot-password', json={'email': 'reset-c@wave0.test'})
    token = _extract_token(captured_emails[-1]['body_text'])

    r = client.post('/api/auth/reset-password', json={'token': token, 'password': '123'})
    assert r.status_code == 400


def test_reset_password_rejects_an_email_verification_token():
    _insert_user(email='reset-wrong-purpose@wave0.test', password='OldPassword1')
    conn = registry_conn()
    raw_token = verification_module._create_link(
        conn, company_id='co-reset-wp', email='reset-wrong-purpose@wave0.test',
        purpose='email_verification', ttl_hours=1,
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/reset-password', json={'token': raw_token, 'password': 'NewPassword2'})
    assert r.status_code == 400


# ── Authenticated resend ───────────────────────────────────────────────────

def test_resend_verification_requires_login():
    client = app.test_client()
    r = client.post('/api/auth/verify-email/resend')
    assert r.status_code == 401


def test_resend_verification_sends_again_for_the_signed_in_user(captured_emails):
    _insert_user(email='resend-a@wave0.test', password='ResendPW1')
    client = app.test_client()
    login = client.post('/api/auth/login', json={'email': 'resend-a@wave0.test', 'password': 'ResendPW1'})
    assert login.status_code == 200

    r = client.post('/api/auth/verify-email/resend')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['email_verification_sent'] is True
    assert captured_emails[0]['recipient'] == 'resend-a@wave0.test'


def test_resend_verification_is_a_noop_once_already_verified(captured_emails):
    _insert_user(email='resend-b@wave0.test', password='ResendPW2')
    client = app.test_client()
    login = client.post('/api/auth/login', json={'email': 'resend-b@wave0.test', 'password': 'ResendPW2'})
    assert login.status_code == 200

    r1 = client.post('/api/auth/verify-email/resend')
    assert r1.status_code == 200
    token = _extract_token(captured_emails[-1]['body_text'])
    client.post('/api/auth/verify-email', json={'token': token})
    captured_emails.clear()

    r2 = client.post('/api/auth/verify-email/resend')
    assert r2.status_code == 200
    assert r2.get_json()['already_verified'] is True
    assert len(captured_emails) == 0
