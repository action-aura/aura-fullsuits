"""The `employee_setup` invite-link expiry gate, and the naive/aware UTC
format migration that runs straight through it.

`create_employee()` mints a seven-day `secure_links` row and `employee_setup()`
refuses it once `expires_at` has passed. That gate is the only thing standing
between a leaked or stale setup URL and an attacker setting the password on a
pending employee account, and it had NO test coverage at all.

That mattered this wave because the comparison changed shape. Both sides used
the deprecated `datetime.utcnow()`, whose `.isoformat()` is NAIVE
('2026-08-29T10:00:00.123456'). They now use timezone-AWARE UTC
('2026-08-29T10:00:00.123456+00:00'), matching `_accounts.now_utc_iso()` and
`verification.py`'s `_now()` -- the latter already writes this exact column in
the aware format for password-reset and verification links, so the naive
writer was the odd one out in a table it shares.

The gate is a TEXT comparison, not a datetime comparison, so a format change
is a behaviour change until proven otherwise -- and a registry.db that has
been in service since before this wave holds rows in the OLD naive format that
the NEW aware comparator has to keep reading correctly. Hence the parametrised
`stored_format` below: every expiry assertion runs against both the legacy
naive spelling and the current aware one. If the two forms ever stop ordering
consistently, an expired invite starts being accepted, silently.

── Why these tests are shaped the way they are ─────────────────────────────

The expired cases deliberately do NOT assert only "status == 400". A 400 is
also what you get from a missing token, a short password, an already-used
link, or a typo'd route -- four ways to pass an expiry test without the expiry
check running. So each expired case asserts the specific expiry copy AND then
proves the account was left untouched (still `pending_setup`, password not
set), which is the thing the gate actually protects.

Run:
    pytest commercial_runtime/identity/tests/test_employee_setup_link_expiry.py -v
"""
import hashlib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from commercial_runtime.identity import (
    mt_auth,
    onboarding_routes,
    registry_db,
)
from commercial_runtime.identity.onboarding_routes import onboarding_bp

#: The two spellings of "the same instant" that can be sitting in
#: `secure_links.expires_at`. 'naive' is what every row written before this
#: wave holds; 'aware' is what `create_employee()` writes now (and what
#: verification.py has always written into this same column).
STORED_FORMATS = ['naive', 'aware']


def _stamp(moment, stored_format):
    if stored_format == 'naive':
        return moment.replace(tzinfo=None).isoformat()
    return moment.isoformat()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    # @mt_login_required resolves its OWN connection through
    # mt_auth.REGISTRY_DB, not through the get_conn patched above.
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


@pytest.fixture
def admin(app):
    """A logged-in admin, minted through the real create-admin route."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _invite(admin_client, email):
    """Mint a real invite through the real route and return its raw token.

    The token is recovered from the returned setup URL rather than
    manufactured, so these tests exercise the same row `create_employee()`
    actually writes -- including whatever format it writes `expires_at` in.
    """
    r = admin_client.post('/api/admin/employees', json={
        'email': email, 'permissions': {}, 'role': 'employee',
    })
    assert r.status_code == 200, r.get_json()
    setup_url = r.get_json()['setup_link']
    return setup_url.rsplit('/', 1)[-1]


def _force_expiry(db_path, raw_token, moment, stored_format):
    """Rewrite the minted row's `expires_at` to `moment` in `stored_format`.

    Rewriting the REAL row (located by the real token hash) rather than
    inserting a synthetic one keeps every other column -- purpose, is_used,
    company_id, email_target -- exactly as production wrote it. A hand-built
    row is how a fixture ends up manufacturing the state that hides the bug.
    """
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "UPDATE secure_links SET expires_at=? WHERE token_hash=?",
            (_stamp(moment, stored_format), token_hash),
        )
        assert cur.rowcount == 1, \
            "the invite row was not found by its token hash -- the fixture " \
            "did not touch the row the route will read, so this test proves nothing"
        conn.commit()
    finally:
        conn.close()


def _user_row(db_path, email):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT status, password_hash FROM users WHERE email=?", (email,)
        ).fetchone()
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# The precondition: a live link works, and the account really was pending
# ═════════════════════════════════════════════════════════════════════════════

def test_a_freshly_minted_invite_link_is_accepted(app, admin, db_path):
    """The baseline every refusal below is measured against. Without it, a
    route that refused EVERY token would pass all the expiry tests."""
    email = f"emp-{uuid.uuid4().hex[:8]}@test.local"
    token = _invite(admin, email)

    before = _user_row(db_path, email)
    assert before is not None, "the invite did not create a user row"
    assert before['status'] == 'pending_setup', \
        f"precondition: the invited account must start pending, got {before['status']!r}"

    r = app.test_client().post('/api/auth/employee/setup', json={
        'token': token, 'password': 'EmpPassword1',
    })

    assert r.status_code == 200, r.get_json()
    after = _user_row(db_path, email)
    assert after['status'] == 'active', \
        "a valid setup link must activate the account"
    # Changed, not merely truthy: a pending row's `password_hash` is the
    # sentinel 'PENDING', which is truthy, so a truthiness check would pass on
    # an account whose password was never actually set.
    assert after['password_hash'] != before['password_hash'], (
        f"the password_hash is unchanged ({after['password_hash']!r}) -- the "
        f"setup route reported success without setting the password"
    )
    assert after['password_hash'] != 'PENDING', \
        "the account is 'active' but still carries the pending-password sentinel"


def test_the_route_writes_expires_at_in_the_aware_utc_format(app, admin, db_path):
    """Pins the WRITE half of the format migration.

    `verification.py` already stamps this same column aware, and
    `_accounts.now_utc_iso()` is the file's canonical spelling. A silent
    regression to `datetime.utcnow()` here would put naive and aware strings
    back into one column -- the mix `now_utc_iso`'s own docstring warns sorts
    wrong the moment the two forms meet.
    """
    email = f"emp-{uuid.uuid4().hex[:8]}@test.local"
    token = _invite(admin, email)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    conn = sqlite3.connect(str(db_path))
    try:
        stored = conn.execute(
            "SELECT expires_at FROM secure_links WHERE token_hash=?", (token_hash,)
        ).fetchone()[0]
    finally:
        conn.close()

    parsed = datetime.fromisoformat(stored)
    assert parsed.tzinfo is not None, (
        f"expires_at was written NAIVE ({stored!r}). This column is compared "
        f"as TEXT against an aware 'now', and verification.py already writes "
        f"it aware -- mixing the two forms in one column is what "
        f"_accounts.now_utc_iso()'s docstring exists to prevent."
    )
    assert parsed.utcoffset() == timedelta(0), \
        f"expires_at must be UTC, got offset {parsed.utcoffset()!r} in {stored!r}"


# ═════════════════════════════════════════════════════════════════════════════
# The gate: an expired link is refused, in EITHER stored format
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('stored_format', STORED_FORMATS)
def test_an_expired_invite_link_is_refused(app, admin, db_path, stored_format):
    """The gate itself, against both spellings of a past timestamp.

    `stored_format='naive'` is not hypothetical: it is every row in a
    registry.db that has been in service since before this wave, read by the
    new aware comparator.
    """
    email = f"emp-{uuid.uuid4().hex[:8]}@test.local"
    token = _invite(admin, email)
    _force_expiry(db_path, token,
                  datetime.now(timezone.utc) - timedelta(days=1), stored_format)

    before = _user_row(db_path, email)
    assert before['status'] == 'pending_setup', \
        f"precondition: account must still be pending, got {before['status']!r}"

    r = app.test_client().post('/api/auth/employee/setup', json={
        'token': token, 'password': 'EmpPassword1',
    })

    assert r.status_code == 400, r.get_json()
    # Not just "a 400": a missing token, a short password and an already-used
    # link all return 400 too. Name the expiry specifically, or this passes
    # for three reasons that are not the one under test.
    assert 'expired' in (r.get_json().get('error') or '').lower(), (
        f"a 400 was returned but not for expiry -- got "
        f"{r.get_json().get('error')!r}, so the expiry branch may never have run"
    )

    # And the thing the gate actually protects: the account is untouched.
    after = _user_row(db_path, email)
    assert after['status'] == 'pending_setup', (
        f"an expired link still activated the account (status={after['status']!r}) "
        f"-- the refusal was cosmetic"
    )
    # Compared against the BEFORE value rather than tested for falsiness: a
    # pending account's `password_hash` is the sentinel string 'PENDING', not
    # NULL, so `not after['password_hash']` would have been false whether the
    # route wrote a real hash or not -- a guard whose pass condition cannot
    # distinguish the bug from the fix. "Unchanged" is the actual invariant.
    assert after['password_hash'] == before['password_hash'], (
        f"an expired link overwrote the account's password_hash "
        f"({before['password_hash']!r} -> {after['password_hash']!r})"
    )


@pytest.mark.parametrize('stored_format', STORED_FORMATS)
def test_a_link_expiring_in_the_future_is_still_accepted(
        app, admin, db_path, stored_format):
    """The other half, and it is not optional: refusing everything would pass
    every expired-case assertion above while breaking employee onboarding
    outright. Also the case where a legacy naive row must NOT read as expired
    against an aware 'now'."""
    email = f"emp-{uuid.uuid4().hex[:8]}@test.local"
    token = _invite(admin, email)
    _force_expiry(db_path, token,
                  datetime.now(timezone.utc) + timedelta(days=3), stored_format)

    r = app.test_client().post('/api/auth/employee/setup', json={
        'token': token, 'password': 'EmpPassword1',
    })

    assert r.status_code == 200, (
        f"a link valid for another three days was refused ({stored_format} "
        f"stored format): {r.get_json()!r}"
    )
    assert _user_row(db_path, email)['status'] == 'active'


@pytest.mark.parametrize('stored_format', STORED_FORMATS)
def test_a_link_that_expired_one_second_ago_is_refused(
        app, admin, db_path, stored_format):
    """The boundary, where a naive/aware text comparison would actually break
    if it were going to. A day either side of `now` is decided by the date
    prefix alone and would pass even if the suffix handling were wrong; one
    second forces the comparison down into the time-of-day characters, which
    is where '+00:00' sits."""
    email = f"emp-{uuid.uuid4().hex[:8]}@test.local"
    token = _invite(admin, email)
    _force_expiry(db_path, token,
                  datetime.now(timezone.utc) - timedelta(seconds=1), stored_format)

    r = app.test_client().post('/api/auth/employee/setup', json={
        'token': token, 'password': 'EmpPassword1',
    })

    assert r.status_code == 400, (
        f"a link that expired one second ago was accepted ({stored_format} "
        f"stored format) -- the naive/aware text comparison does not order "
        f"correctly at sub-day resolution: {r.get_json()!r}"
    )
    assert 'expired' in (r.get_json().get('error') or '').lower()
    assert _user_row(db_path, email)['status'] == 'pending_setup'
