"""
Aura Retail -- authentication hardening (multi-device Phase 1, Part A).

Two live holes in the shared decorator every protected route in this product
hangs off, `commercial_runtime/identity/mt_auth.py::mt_login_required`. Both
are named in docs/launch-readiness/multi-device-design.md §3.

A1 -- FAIL OPEN. The decorator's account-status lookup was wrapped in
`except Exception: pass`, commented "Fail-open only for a transient
local-SQLite read error, not for a missing account." A transient local-SQLite
read error is not a rare event on a till: `database is locked` is the normal
outcome of a write holding the lock while a report query runs. For as long as
one lasted, every protected route in Retail served every request that carried
any session cookie at all -- including a session belonging to an account that
had since been disabled. "Transient" describes how long the fault lasts, not
how much access it hands out.

A2 -- session_version WAS NEVER COMPARED. `create_session` has stored
`mt_session_version` since it was written (mt_auth.py:118), and
onboarding_routes.py bumps `users.session_version` in four places -- password
reset (:299), enable/disable (:437), clinic-role change (:462), permission
change (:490) -- each with a comment saying the bump invalidates every
session issued before it. Nothing ever read the two values back and compared
them, so all four bumps revoked precisely nothing: a cookie captured before a
password reset kept working after it.

The tests below deliberately drive the REAL decorator through the REAL
routes. `GET /api/devices` is the workhorse because it carries
`@mt_login_required` and nothing else, so a refusal there can only have come
from the decorator under test rather than from the licence gate or the
subsystem gate stacked above the retail routes; one test uses a real retail
route as well, to prove the fix reaches the surface customers actually touch.

Run:
    pytest products/retail/tests/retail_auth_hardening_test.py -v
"""
import json
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_auth_hardening_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import mt_auth  # noqa: E402
from commercial_runtime.identity import verification as verification_module  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

#: `@mt_login_required` and nothing else -- see the module docstring.
AUTH_ONLY_ROUTE = '/api/devices'
#: A real retail route: `@mt_login_required` + `@mt_require_subsystem('retail')`.
RETAIL_ROUTE = '/api/sub/retail/dashboard/stats'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user(email, password, *, role='admin', status='active', company_id=None):
    """A real, loginable registry row. Inserted directly rather than through
    /api/onboarding/create-admin because this install allows exactly one admin
    account (onboarding_routes.py:120-125) and several tests here need two
    accounts at once."""
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:8]}', email, hash_password(password), role, status),
    )
    conn.commit()
    conn.close()
    return user_id, company_id


def _logged_in_client(email, password, **kwargs):
    _make_user(email, password, **kwargs)
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


def _stored_session_version(user_id):
    conn = registry_conn()
    try:
        return conn.execute("SELECT session_version FROM users WHERE id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# A1 -- the decorator must fail CLOSED when the registry read raises
# ═════════════════════════════════════════════════════════════════════════════

class _ExplodingRegistry:
    """Stands in for `mt_auth._get_registry_conn` and raises the single most
    likely real fault -- `sqlite3.OperationalError('database is locked')` --
    instead of returning a connection.

    Counts its calls on purpose. Asserting the counter is what stops these
    tests from passing for the wrong reason: a 401 could equally well mean
    "the session was never established", and only a call recorded here proves
    the request really did reach the decorator's `except` branch.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise sqlite3.OperationalError('database is locked')


def test_protected_route_fails_closed_when_the_registry_read_raises(monkeypatch):
    c = _logged_in_client(f'failclosed-{uuid.uuid4().hex[:8]}@test.local', 'FailClosedPW1')
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200, "precondition: this session works while the DB is healthy"

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    r = c.get(AUTH_ONLY_ROUTE)

    assert boom.calls >= 1, "the decorator's DB read never ran -- this test would be asserting nothing"
    assert r.status_code == 401, "a DB error must refuse the request, never wave it through"


def test_real_retail_route_also_fails_closed(monkeypatch):
    """The same fault, through the full decorator stack a customer-facing
    route actually carries."""
    c = _logged_in_client(f'failclosed-retail-{uuid.uuid4().hex[:8]}@test.local', 'FailClosedPW2')
    assert c.get(RETAIL_ROUTE).status_code == 200

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    r = c.get(RETAIL_ROUTE)
    assert boom.calls >= 1
    assert r.status_code == 401


def test_fail_closed_response_is_shaped_exactly_like_an_unauthenticated_one(monkeypatch):
    """A caller must not be able to tell a refused-because-broken request from
    a refused-because-not-logged-in one. Same status, same body -- otherwise
    the error message itself becomes a probe for whether the shop's registry
    database is currently unhealthy."""
    anonymous = app.test_client().get(AUTH_ONLY_ROUTE)
    assert anonymous.status_code == 401

    c = _logged_in_client(f'failclosed-shape-{uuid.uuid4().hex[:8]}@test.local', 'FailClosedPW3')
    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)

    r = c.get(AUTH_ONLY_ROUTE)
    assert boom.calls >= 1
    assert r.status_code == anonymous.status_code
    assert r.get_json() == anonymous.get_json()


def test_a_transient_failure_refuses_the_request_without_destroying_the_session(monkeypatch):
    """Fail closed, not fail destructive.

    The decorator re-checks on every single request, so refusing THIS request
    is the whole of the security requirement -- clearing the session as well
    would convert a two-second lock contention into "every till in the shop
    got logged out mid-sale", which is a self-inflicted outage with no
    security benefit whatsoever.
    """
    c = _logged_in_client(f'failclosed-recover-{uuid.uuid4().hex[:8]}@test.local', 'FailClosedPW4')
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200

    boom = _ExplodingRegistry()
    monkeypatch.setattr(mt_auth, '_get_registry_conn', boom)
    assert c.get(AUTH_ONLY_ROUTE).status_code == 401
    assert boom.calls >= 1

    monkeypatch.undo()  # the lock clears, as a transient lock does
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200, \
        "a recovered database must serve the same session again, not demand a fresh login"


def test_a_missing_account_still_fails_closed_and_clears_the_session():
    """The pre-existing "row is gone" branch, guarded so the A1 rewrite did
    not weaken it. A deleted account is permanent, so here clearing the
    session IS correct -- the opposite call from the transient case above."""
    email = f'deleted-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'DeletedPW1')
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'DeletedPW1'}).status_code == 200
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200

    conn = registry_conn()
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    assert c.get(AUTH_ONLY_ROUTE).status_code == 401
    with c.session_transaction() as s:
        assert 'mt_user_id' not in s


def test_a_disabled_account_is_refused_on_its_next_request():
    """Also pre-existing behaviour, re-asserted as a guard: the A1 fix must
    not have turned the status check into part of the swallowed path."""
    email = f'disabled-mid-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'DisabledMidPW1')
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'DisabledMidPW1'}).status_code == 200
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200

    conn = registry_conn()
    conn.execute("UPDATE users SET status='disabled' WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    assert c.get(AUTH_ONLY_ROUTE).status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# A2 -- a session_version bump must actually revoke the session
# ═════════════════════════════════════════════════════════════════════════════

def test_session_version_bump_revokes_an_existing_session():
    """The mechanism in isolation: bump the column, the already-issued session
    stops working on its very next request."""
    email = f'revoke-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'RevokePW1')
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'RevokePW1'}).status_code == 200
    assert c.get(AUTH_ONLY_ROUTE).status_code == 200

    before = _stored_session_version(user_id)
    conn = registry_conn()
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    assert _stored_session_version(user_id) == before + 1, "precondition: the bump actually landed"

    assert c.get(AUTH_ONLY_ROUTE).status_code == 401, \
        "a session older than the account's session_version must be rejected"


def test_a_revoked_session_is_cleared_so_the_cookie_cannot_be_replayed():
    email = f'revoke-clear-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'RevokePW2')
    c = app.test_client()
    c.post('/api/auth/login', json={'email': email, 'password': 'RevokePW2'})

    conn = registry_conn()
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

    assert c.get(AUTH_ONLY_ROUTE).status_code == 401
    with c.session_transaction() as s:
        assert 'mt_user_id' not in s, "a revoked session must be torn down, not merely refused once"


def test_logging_in_again_after_a_bump_works():
    """Revocation must not lock the real user out of their own account -- the
    new login reads the new session_version and matches it."""
    email = f'revoke-relogin-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'RevokePW3')
    c = app.test_client()
    c.post('/api/auth/login', json={'email': email, 'password': 'RevokePW3'})

    conn = registry_conn()
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    assert c.get(AUTH_ONLY_ROUTE).status_code == 401

    fresh = app.test_client()
    assert fresh.post('/api/auth/login', json={'email': email, 'password': 'RevokePW3'}).status_code == 200
    assert fresh.get(AUTH_ONLY_ROUTE).status_code == 200


def test_password_reset_revokes_the_session_it_replaces():
    """The production path, end to end: onboarding_routes.reset_password's
    bump (:299) is the one whose comment claims it "invalidates every session
    issued before this reset". This is that claim, tested.

    The link is minted directly through verification._create_link rather than
    through /api/auth/forgot-password so this file needs no SMTP capture --
    the token, the consume path and the route are all still the real ones.
    """
    email = f'reset-revoke-{uuid.uuid4().hex[:8]}@test.local'
    user_id, company_id = _make_user(email, 'OldSecret11')

    stolen = app.test_client()
    assert stolen.post('/api/auth/login', json={'email': email, 'password': 'OldSecret11'}).status_code == 200
    assert stolen.get(AUTH_ONLY_ROUTE).status_code == 200

    conn = registry_conn()
    raw_token = verification_module._create_link(
        conn, company_id=company_id, email=email, purpose='password_reset', ttl_hours=1,
    )
    conn.commit()
    conn.close()

    r = app.test_client().post('/api/auth/reset-password', json={'token': raw_token, 'password': 'NewSecret22'})
    assert r.status_code == 200, r.get_json()

    assert stolen.get(AUTH_ONLY_ROUTE).status_code == 401, \
        "a session captured before a password reset must not survive it"


def test_admin_permission_change_revokes_the_employees_session():
    """onboarding_routes.update_perms' bump (:490). An access-level change
    that only takes effect at next login is not an access-level change."""
    company_id = str(uuid.uuid4())
    admin_email = f'perm-admin-{uuid.uuid4().hex[:8]}@test.local'
    staff_email = f'perm-staff-{uuid.uuid4().hex[:8]}@test.local'
    _make_user(admin_email, 'PermAdminPW1', role='admin', company_id=company_id)
    staff_id, _ = _make_user(staff_email, 'PermStaffPW1', role='employee', company_id=company_id)

    admin = app.test_client()
    assert admin.post('/api/auth/login', json={'email': admin_email, 'password': 'PermAdminPW1'}).status_code == 200
    staff = app.test_client()
    assert staff.post('/api/auth/login', json={'email': staff_email, 'password': 'PermStaffPW1'}).status_code == 200
    assert staff.get(AUTH_ONLY_ROUTE).status_code == 200

    r = admin.post(f'/api/admin/employees/{staff_id}/permissions',
                   json={'subsystem': 'retail', 'access_level': 'none'})
    assert r.status_code == 200, r.get_json()

    assert staff.get(AUTH_ONLY_ROUTE).status_code == 401


def test_admin_disabling_an_account_revokes_its_session():
    """onboarding_routes.update_status' bump (:437), through the real route.
    Status alone would already have caught this one; the point is that the
    session_version check does not get in the way of it."""
    company_id = str(uuid.uuid4())
    admin_email = f'status-admin-{uuid.uuid4().hex[:8]}@test.local'
    staff_email = f'status-staff-{uuid.uuid4().hex[:8]}@test.local'
    _make_user(admin_email, 'StatusAdminPW1', role='admin', company_id=company_id)
    staff_id, _ = _make_user(staff_email, 'StatusStaffPW1', role='employee', company_id=company_id)

    admin = app.test_client()
    admin.post('/api/auth/login', json={'email': admin_email, 'password': 'StatusAdminPW1'})
    staff = app.test_client()
    staff.post('/api/auth/login', json={'email': staff_email, 'password': 'StatusStaffPW1'})
    assert staff.get(AUTH_ONLY_ROUTE).status_code == 200

    r = admin.put(f'/api/admin/employees/{staff_id}/status', json={'status': 'disabled'})
    assert r.status_code == 200, r.get_json()

    assert staff.get(AUTH_ONLY_ROUTE).status_code == 401


def test_an_untouched_session_keeps_working():
    """The guard against 'fixed' by refusing everything: a session whose
    account nobody has touched must sail straight through, repeatedly."""
    c = _logged_in_client(f'stable-{uuid.uuid4().hex[:8]}@test.local', 'StablePW1')
    for _ in range(3):
        assert c.get(AUTH_ONLY_ROUTE).status_code == 200
    assert c.get(RETAIL_ROUTE).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# Localization -- this product ships Arabic and is RTL
# ═════════════════════════════════════════════════════════════════════════════

LOCALES_DIR = PRODUCT_DIR / 'frontend' / 'locales'


def test_both_refusal_messages_are_translatable_and_actually_translated():
    """Every refusal the hardened decorator can put in front of a user has to
    exist in BOTH catalogs. i18n.js translates by matching the FULL English
    sentence against the dictionary (see its t()), so a message that is not a
    key renders in English on an Arabic till."""
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((LOCALES_DIR / 'ar.json').read_text(encoding='utf-8'))
    for text in ('Authentication required',
                 'Session expired or revoked. Please log in again.'):
        assert text in en, f"missing from en.json: {text!r}"
        assert text in ar, f"missing from ar.json: {text!r}"
        assert ar[text].strip() and ar[text] != en[text], f"not actually translated: {text!r}"


def test_the_messages_the_routes_really_emit_are_the_catalogued_ones(monkeypatch):
    """Binds the catalog to the code rather than trusting they agree. Reword
    a refusal in mt_auth.py without updating the locale files and this fails
    -- which is the only thing that keeps the two from drifting apart."""
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))

    anonymous = app.test_client().get(AUTH_ONLY_ROUTE)
    assert anonymous.get_json()['error'] in en

    email = f'i18n-{uuid.uuid4().hex[:8]}@test.local'
    user_id, _ = _make_user(email, 'I18nPW1')
    c = app.test_client()
    c.post('/api/auth/login', json={'email': email, 'password': 'I18nPW1'})
    conn = registry_conn()
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    revoked = c.get(AUTH_ONLY_ROUTE)
    assert revoked.status_code == 401
    assert revoked.get_json()['error'] in en

    broken = _logged_in_client(f'i18n-db-{uuid.uuid4().hex[:8]}@test.local', 'I18nPW2')
    monkeypatch.setattr(mt_auth, '_get_registry_conn', _ExplodingRegistry())
    assert broken.get(AUTH_ONLY_ROUTE).get_json()['error'] in en


def test_one_users_bump_does_not_revoke_another_users_session():
    """Revocation is per account. A shop that disables one cashier must not
    log out the till next to them."""
    company_id = str(uuid.uuid4())
    a_email = f'iso-a-{uuid.uuid4().hex[:8]}@test.local'
    b_email = f'iso-b-{uuid.uuid4().hex[:8]}@test.local'
    a_id, _ = _make_user(a_email, 'IsoAPW1', company_id=company_id)
    _make_user(b_email, 'IsoBPW1', company_id=company_id)

    a = app.test_client()
    a.post('/api/auth/login', json={'email': a_email, 'password': 'IsoAPW1'})
    b = app.test_client()
    b.post('/api/auth/login', json={'email': b_email, 'password': 'IsoBPW1'})

    conn = registry_conn()
    conn.execute("UPDATE users SET session_version=session_version+1 WHERE id=?", (a_id,))
    conn.commit()
    conn.close()

    assert a.get(AUTH_ONLY_ROUTE).status_code == 401
    assert b.get(AUTH_ONLY_ROUTE).status_code == 200
