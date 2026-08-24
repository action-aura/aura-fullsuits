"""Registry v4 -- Defect 1 (launch-readiness Phase 5 verification, HIGH):
every activation-time `company_id` rebind used to blank the shop for anyone
who was ALREADY logged in when the rebind landed. See
commercial_runtime/identity/company_rebind.py::rebind_company_id's inline
comment at the `session_version` bump for the full mechanism, and
docs/launch-readiness/phase5-prerequisites.md §1 for why the rebind exists
at all.

THE MEASURED DEFECT (before this fix), reproduced over real HTTP, not
reasoned about: on a fully successful activation-time rebind performed
against a RUNNING process --

    ALREADY-LOGGED-IN SESSION SEES AFTER ACTIVATION: []
    A FRESH LOGIN SEES:                              ['SKU-TILL']

Root cause: `mt_auth.create_session` stamps `session['company_id']` into the
browser's session cookie at LOGIN time; `retail_api._cid()` filters
essentially every retail query on that cached value; and
`mt_login_required` re-checks `session_version` on every request but never
re-reads `company_id` itself. So the moment a rebind lands `users.
company_id` on the Owner-issued key, every session that logged in under the
OLD key keeps filtering on a tenant that owns zero rows -- deterministically,
on 100% of activations performed against a process someone is using, not a
race and not sub-millisecond.

THE FIX bumps `users.session_version` for every moved account inside the
SAME transaction that moves the rows (see `rebind_company_id`). `mt_auth.
mt_login_required`'s existing `_session_version_is_stale` check then revokes
every one of those sessions -- honestly, with a 401 and a real "log in
again" message -- on its very next request, rather than silently handing
back `200 OK` with an empty products list. This is the honest trade the
design calls for: "the tenant key changed, so every session's cached
identity IS stale, not just the activating admin's."

This file drives the real production seam end to end: real onboarding, a
real product, two real logins under the SAME tenant (proving the fix is not
scoped to the account that happened to trigger the activation), the REAL
`app.py::_on_licence_activated()` (the exact `on_activation_success` hook
`make_licensing_blueprint` wires into `/activate`), and real HTTP requests
against `/api/sub/retail/products` before and after.

MUTATION PROOF (see this module's own history / the task that added it):
deleting the `session_version` bump in `rebind_company_id` makes
`test_already_logged_in_sessions_are_revoked_not_silently_emptied_by_
activation` fail on its "neither stale session silently sees an empty shop"
assertion -- the exact `r.status_code == 401` check below flips to 200 with
an empty `data` list, reproducing the measured defect verbatim.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v4_session_invalidation.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[3] / 'products' / 'retail'
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_registry_v4_sessioninval_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

REGISTRY_DB = DATA / 'database' / 'registry.db'
RETAIL_DB = DATA / 'database' / 'subsystems' / 'retail.db'
LICENSING_DB = DATA / 'database' / 'subsystems' / 'licensing.db'

OWNER_COMPANY_ID = '11111111-2222-3333-4444-555555555555'

REVOKED_MESSAGE = 'Session expired or revoked. Please log in again.'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _write_licence_state(license_public_id):
    """Same helper/shape as commercial_runtime/identity/tests/
    test_registry_v4_company_rebind.py and test_registry_v4_window.py."""
    os.makedirs(LICENSING_DB.parent, exist_ok=True)
    conn = sqlite3.connect(str(LICENSING_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            current_state TEXT NOT NULL,
            owner_installation_id TEXT,
            assertion_envelope_json TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    payload = {
        'assertion_id': str(uuid.uuid4()),
        'product_code': 'AURA_RETAIL',
        'license_public_id': license_public_id,
        'installation_public_id': str(uuid.uuid4()),
    }
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', payload['installation_public_id'],
         json.dumps({'payload': payload}), '2026-08-24T00:00:00'),
    )
    conn.commit()
    conn.close()


def _onboard_admin(email, password='SessionInvalPW1', company_name='Session Co'):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': email, 'password': password, 'company_name': company_name,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _registry_company_id_for(email):
    conn = sqlite3.connect(str(REGISTRY_DB))
    row = conn.execute('SELECT company_id FROM users WHERE LOWER(email)=?', (email.lower(),)).fetchone()
    conn.close()
    assert row, f'onboarding did not create a users row for {email!r}'
    return row[0]


def _insert_product(company_id, sku, name='Session Test Widget'):
    conn = sqlite3.connect(str(RETAIL_DB))
    conn.execute(
        "INSERT INTO products (company_id, sku, name, sell_price) VALUES (?,?,?,?)",
        (company_id, sku, name, 9.99),
    )
    conn.commit()
    conn.close()


def _insert_second_admin_same_tenant(company_id, email, password='SessionInvalPW2'):
    """A SECOND real account under the SAME company_id onboarding already
    created -- unlike test_registry_v4_window.py's `_insert_second_admin`
    (a DIFFERENT company, used there to prove the multi-tenant guard),
    this one proves the rebind's `session_version` bump reaches EVERY
    account it just moved, not merely the one whose activation call
    triggered it. `role='admin'` sidesteps `mt_require_subsystem`'s
    per-user `user_permissions` lookup (this fixture seeds none) --
    the point under test is session revocation, not the separate
    per-role capability matrix retail_route_capability_matrix_test.py
    already owns."""
    from commercial_runtime.security.passwords import hash_password
    conn = sqlite3.connect(str(REGISTRY_DB))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, 'SECOND-ADMIN', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()


def _login(client, email, password):
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return r


def _reset_databases():
    """See test_registry_v4_window.py::_reset_databases for why this clears
    on-disk state and re-runs init_app() rather than re-importing `app`."""
    for db_path in (REGISTRY_DB, RETAIL_DB, LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    _app_module.init_app()


def test_already_logged_in_sessions_are_revoked_not_silently_emptied_by_activation():
    """The full defect-1 reproduction, driven over real HTTP.

    1. Onboard an admin, insert a product, log in TWO real sessions under
       the SAME tenant -- both genuinely see the product before anything
       happens.
    2. Fire the REAL activation hook (`_on_licence_activated`), which
       rebinds BOTH registry.db and retail.db onto the Owner-issued
       company_id while both sessions are still alive -- exactly what a
       customer activating their licence from a running till does.
    3. Neither stale session may silently see an empty shop. Both must be
       told, honestly, that their session was revoked (401, the EXACT
       `mt_auth._revoked_response()` text) instead of `200 OK` with zero
       rows -- the difference between "something happened, sign in again"
       and "this shop apparently has no products".
    4. Recovery: logging in again on the SAME client (the ordinary next
       step after a 401) picks up the new company_id and sees the real
       product again.
    """
    _reset_databases()
    admin_email = 'owner-sessioninval-1@test.local'
    second_email = 'second-sessioninval-1@test.local'
    _onboard_admin(admin_email)
    legacy_company_id = _registry_company_id_for(admin_email)
    _insert_second_admin_same_tenant(legacy_company_id, second_email)
    _insert_product(legacy_company_id, sku='WIDGET-SESSIONINVAL-1')

    admin_client = app.test_client()
    _login(admin_client, admin_email, 'SessionInvalPW1')
    second_client = app.test_client()
    _login(second_client, second_email, 'SessionInvalPW2')

    # Both sessions genuinely work before activation -- otherwise a failure
    # below would prove nothing about the defect.
    for client, label in ((admin_client, 'admin'), (second_client, 'second')):
        before = client.get('/api/sub/retail/products').get_json()
        assert before['status'] == 'success', (label, before)
        assert len(before['data']) == 1, f'{label}: fixture did not create a visible product'

    _write_licence_state(OWNER_COMPANY_ID)
    _app_module._on_licence_activated()  # the REAL on_activation_success hook

    # Sanity: the rebind actually happened for real, moving BOTH accounts
    # and the product -- otherwise this test would not exercise the defect
    # at all.
    reg_conn = sqlite3.connect(str(REGISTRY_DB))
    assert reg_conn.execute(
        'SELECT COUNT(*) FROM users WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 2, 'fixture bug: activation did not move both accounts onto the Owner-issued id'
    reg_conn.close()
    retail_conn = sqlite3.connect(str(RETAIL_DB))
    assert retail_conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 1, 'fixture bug: retail.db did not converge -- this test would not exercise the defect'
    retail_conn.close()

    # ── THE ASSERTION ───────────────────────────────────────────────────
    for client, label in ((admin_client, 'admin'), (second_client, 'second')):
        r = client.get('/api/sub/retail/products')
        body = r.get_json()
        assert r.status_code == 401, (
            f'{label} session was NOT revoked by the rebind -- status={r.status_code} '
            f'body={body!r}. A 200 here (of any shape) after a company_id rebind means '
            f'this session is still filtering every query on a tenant key that no longer '
            f'has any matching rows -- the exact silent-empty-shop catastrophe this fix '
            f'exists to prevent.'
        )
        assert body['error'] == REVOKED_MESSAGE, (label, body)

    # ── RECOVERY, for BOTH sessions ─────────────────────────────────────
    for client, email, pw, label in (
        (admin_client, admin_email, 'SessionInvalPW1', 'admin'),
        (second_client, second_email, 'SessionInvalPW2', 'second'),
    ):
        _login(client, email, pw)
        after = client.get('/api/sub/retail/products').get_json()
        assert after['status'] == 'success', (label, after)
        assert [p['sku'] for p in after['data']] == ['WIDGET-SESSIONINVAL-1'], (
            f'{label}: a fresh login after the forced revocation did not see the real shop data'
        )
