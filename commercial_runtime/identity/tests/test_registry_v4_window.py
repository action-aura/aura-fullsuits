"""Registry v4 -- THE WINDOW TEST (design item (e), the one the task ordering
this file exists for calls "the one that matters most and is easiest to
forget"). See docs/launch-readiness/phase5-prerequisites.md §1 and
commercial_runtime/identity/company_rebind.py's module docstring.

The scenario: identity has already adopted the Owner-issued `company_id` (a
PRIOR process's registry-v4 migration or activation-time hook already
committed that change), but retail.db has not followed -- because that
prior process crashed, or was killed, in the gap between identity's commit
and retail's own convergence. This is exactly the state `products/retail/
backend/database/schema.py`'s v14 migration alone CANNOT self-heal from: v14
is version-gated (`PRAGMA user_version`), and once retail.db is already at
its target schema version -- which it will be, for any install that has run
even once since v14 landed -- the migration step that performs the
convergence never re-enters. Left alone, this install would boot forever
with `session['company_id']` (from registry.db) pointing at one tenant key
and every `retail_api._cid()`-filtered row pointing at another: the shop's
entire history silently invisible, permanently, with no exception anywhere.

This test proves `products/retail/backend/app.py::init_app()`'s
`_converge_and_refuse_to_serve_if_stuck()` closes that gap -- not by reading
the code, but by actually driving the scenario through the real app: real
onboarding, a real product, a hand-simulated crash-recovery state (the same
"mutate the database directly to the state a hard kill would leave" technique
products/retail/tests/retail_v14_company_rebind_migration_test.py and
retail_v16_terminal_cash_drawer_test.py already use), a SECOND real
`init_app()` boot (standing in for "the process restarts"), and then a real
HTTP login + a real HTTP GET against `/api/sub/retail/products` -- proving the shop's
history is still there, not merely that the code which is supposed to keep
it there was never deleted.

A companion test proves the other side of the same guard: when identity has
adopted the Owner id and retail genuinely CANNOT follow (a fabricated,
irreconcilable multi-tenant retail.db -- an anomaly, not a realistic day-two
state, but the one this guard exists for), `init_app()` refuses to finish
booting rather than let the process start serving a shop that would look
empty.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/identity/tests/test_registry_v4_window.py -v
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

PRODUCT_DIR = Path(__file__).resolve().parents[3] / 'products' / 'retail'
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_registry_v4_window_"))
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

OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'
SECOND_TENANT_ID = 'ffffffffffffffffffffffffffffffff'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _write_licence_state(license_public_id):
    """Same helper/shape as commercial_runtime/identity/tests/
    test_registry_v4_company_rebind.py -- writes the licensing_state row
    owner_issued_company_id() reads, with plain sqlite3/json."""
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


def _onboard_admin(email, password='WindowTestPW1', company_name='Window Co'):
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


def _insert_product(company_id, sku, name='Window Test Widget'):
    conn = sqlite3.connect(str(RETAIL_DB))
    conn.execute(
        "INSERT INTO products (company_id, sku, name, sell_price) VALUES (?,?,?,?)",
        (company_id, sku, name, 9.99),
    )
    conn.commit()
    conn.close()


def _rebind_registry_company_id(old_id, new_id):
    """Directly moves registry.db's tenant-scoped rows onto `new_id`,
    standing in for "identity's own rebind already ran and committed in a
    prior process" -- deliberately NOT calling company_rebind.rebind_
    company_id() here, so this fixture does not depend on the very function
    the rest of this test suite already exercises directly; it has to build
    the crash-recovery STATE, not re-prove the rebind mechanism itself.

    The table list is still discovered here rather than hardcoded, but it is
    discovered with RAW SQL rather than via `company_scoped_tables`, and the
    result is asserted on. Sourcing this fixture's table list from the module
    under test is the classic "fixture that manufactures the state which
    hides the bug": a `company_scoped_tables` that returned `()` would move
    nothing, the window would never open, retail would correctly defer, and
    every test below would pass while proving nothing at all."""
    conn = sqlite3.connect(str(REGISTRY_DB))
    tables = []
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if 'company_id' in cols:
            tables.append(name)
    assert tables, 'fixture bug: no company_id-bearing table found in registry.db at all'
    for table in tables:
        conn.execute(f'UPDATE "{table}" SET company_id=? WHERE company_id=?', (new_id, old_id))
    conn.commit()
    moved = conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?', (new_id,)).fetchone()[0]
    stranded = conn.execute('SELECT COUNT(*) FROM users WHERE company_id=?', (old_id,)).fetchone()[0]
    conn.close()
    assert moved and not stranded, (
        f'fixture bug: identity was not actually moved onto {new_id!r} '
        f'({moved} moved, {stranded} still on the old key) -- the window this '
        f'test exists to open was never opened'
    )


def _login(client, email, password='WindowTestPW1'):
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return r


def _reset_databases():
    """Each test function needs its own clean install: `create-admin` is
    gated on "no valid admin exists yet anywhere in this registry.db" (see
    onboarding_routes.py::create_admin), a first-run route, not a
    per-test-case reset. `app` (the Flask object, with its blueprints
    already registered exactly once at import time) has to stay the SAME
    object across every test in this file -- Flask does not support
    re-registering a blueprint -- so this clears the state underneath it
    instead of re-importing `app`. Deleting the on-disk files and calling
    `init_app()` again exercises the exact same "process restart against a
    clean install" path the real launcher takes, just compressed into one
    call between tests."""
    for db_path in (REGISTRY_DB, RETAIL_DB, LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    _app_module.init_app()


# ── the window itself ───────────────────────────────────────────────────────

def test_a_crash_recovered_install_converges_and_serves_real_data_not_an_empty_shop():
    _reset_databases()
    email = 'owner-window-1@test.local'
    _onboard_admin(email)
    legacy_company_id = _registry_company_id_for(email)

    _insert_product(legacy_company_id, sku='WIDGET-WINDOW-1')
    with app.test_client() as c:
        _login(c, email)
        before = c.get('/api/sub/retail/products').get_json()
    assert before['status'] == 'success'
    assert len(before['data']) == 1, 'fixture did not actually create a visible product'

    # ── simulate the crash: identity has ALREADY moved (a prior process's
    # registry-v4 migration/activation committed this), retail has NOT --
    # both databases are, at this instant, already at their full target
    # schema version, so NEITHER migration's own version gate will ever
    # re-enter on a plain restart. This is precisely the state a hard kill
    # between the two rebinds leaves behind.
    _write_licence_state(OWNER_COMPANY_ID)
    _rebind_registry_company_id(legacy_company_id, OWNER_COMPANY_ID)

    # Confirm the window really is open right now, before the fix gets a
    # chance to close it -- otherwise a broken fixture would make this test
    # pass for the wrong reason.
    conn = sqlite3.connect(str(RETAIL_DB))
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 0, 'fixture bug: retail already agrees with identity before the boot under test'
    conn.close()

    # ── the next launch. This is the exact call products/retail/backend/
    # app.py makes at real process startup (`if __name__ == '__main__':
    # init_app()`) -- BEFORE the server ever starts accepting a connection.
    _app_module.init_app()

    # ── drive a REAL request through the REAL app, proving the shop's
    # history is actually there -- not reading the code, not reading the
    # database directly, a live HTTP round trip exactly as a till would make
    # it. A fresh client: the OLD session cookie was minted under the
    # legacy company_id and would carry stale session state regardless of
    # what this fix does or does not do, so re-using it would not isolate
    # anything.
    with app.test_client() as c:
        _login(c, email)
        after = c.get('/api/sub/retail/products')

    assert after.status_code == 200
    after_data = after.get_json()
    assert after_data['status'] == 'success'
    assert [p['sku'] for p in after_data['data']] == ['WIDGET-WINDOW-1'], (
        'the shop\'s product silently disappeared across the simulated restart -- '
        'this is the exact catastrophe docs/launch-readiness/phase5-prerequisites.md '
        '§1 describes: identity moved to the Owner-issued company_id, retail.db did '
        'not follow, and every WHERE company_id=? query in retail_api.py matched '
        'zero rows'
    )

    # And the database itself agrees -- the product's row genuinely moved,
    # this was not the old company_id coincidentally still being read from
    # somewhere.
    conn = sqlite3.connect(str(RETAIL_DB))
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 1
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (legacy_company_id,)
    ).fetchone()[0] == 0
    conn.close()


def test_boot_catches_up_identitys_own_half_not_only_retails():
    """The OTHER un-revisitable state, found in verification after the two
    tests above were written: neither database was ever rebound, even though
    a licence is present.

    It is reachable the moment an activation's identity half returns
    'failed' (a locked registry.db is the ordinary cause). Retail's half then
    correctly defers, both databases stay consistently on the legacy key, and
    NOTHING looks broken -- which is why it needs a test rather than a
    refusal. But registry v4's migration step is version-gated and can never
    re-enter, and the activation hook only fires from /activate and
    /_internal/sync-activation -- never from /check-in. So without a
    boot-time catch-up for IDENTITY's half specifically, this install holds a
    licence and stays on md5(admin_email) permanently: exactly the state
    Phase 5 must not open on top of, because rows pushed under that key
    arrive at Owner scoped to a tenant it does not recognise.

    The retail-half catch-up alone cannot fix it -- retail only ever
    CONVERGES onto a key identity has already adopted, so with identity
    stuck it deferring forever is the correct behaviour, not the bug."""
    _reset_databases()
    email = 'owner-window-4@test.local'
    _onboard_admin(email, company_name='Window Co 4')
    legacy_company_id = _registry_company_id_for(email)
    _insert_product(legacy_company_id, sku='WIDGET-WINDOW-4')

    conn = sqlite3.connect(str(REGISTRY_DB))
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    conn.close()
    assert version >= 4, (
        'registry.db is not at v4, so its migration gate is not actually closed and '
        'this test would pass for the wrong reason'
    )

    # A licence, and neither database rebound.
    _write_licence_state(OWNER_COMPANY_ID)
    assert legacy_company_id != OWNER_COMPANY_ID

    _app_module.init_app()  # a plain restart

    conn = sqlite3.connect(str(REGISTRY_DB))
    rebound_to = conn.execute(
        'SELECT company_id FROM users WHERE LOWER(email)=?', (email.lower(),)
    ).fetchone()[0]
    conn.close()
    assert rebound_to == OWNER_COMPANY_ID, (
        'registry.db never adopted the Owner-issued tenant key: the boot-time catch-up '
        'runs retail\'s half of the rebind but not identity\'s, and identity has no '
        'other seam left that can ever fire again for this install'
    )

    conn = sqlite3.connect(str(RETAIL_DB))
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 1, 'retail did not follow identity within the same boot'
    conn.close()

    with app.test_client() as c:
        _login(c, email)
        r = c.get('/api/sub/retail/products')
    assert [p['sku'] for p in r.get_json()['data']] == ['WIDGET-WINDOW-4']


def test_when_retail_genuinely_cannot_converge_the_app_refuses_to_boot_rather_than_serve_empty():
    """The other side of the same guard: identity has UNAMBIGUOUSLY adopted
    the Owner-issued id (single tenant, matches the licence exactly), but
    retail.db is fabricated into an irreconcilable two-tenant state --
    `rebind_company_id` correctly refuses to guess which tenant to move, so
    `rebind_company_id_after_activation()` reports 'failed', and
    `_converge_and_refuse_to_serve_if_stuck()` must not let the process boot
    anyway."""
    _reset_databases()
    email = 'owner-window-2@test.local'
    _onboard_admin(email, company_name='Window Co 2')
    legacy_company_id = _registry_company_id_for(email)
    _insert_product(legacy_company_id, sku='WIDGET-WINDOW-2')

    # A second, unrelated tenant's data in retail.db -- CLAUDE.md's "one
    # install can host more than one company" made real, but one identity
    # never adopted and never will.
    _insert_product(SECOND_TENANT_ID, sku='WIDGET-WINDOW-2-OTHER')

    _write_licence_state(OWNER_COMPANY_ID)
    _rebind_registry_company_id(legacy_company_id, OWNER_COMPANY_ID)

    with pytest.raises(RuntimeError, match='REFUSING TO SERVE'):
        _app_module.init_app()

    # Nothing was touched by the refusal -- both tenants' rows are exactly
    # where they were, on the OLD keys, not merged and not moved.
    conn = sqlite3.connect(str(RETAIL_DB))
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (legacy_company_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (SECOND_TENANT_ID,)
    ).fetchone()[0] == 1
    assert conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 0
    conn.close()


def _insert_second_admin(company_id, email, password='WindowTestPW2'):
    """Onboarding's own `create-admin` route is gated on "no valid admin
    exists yet in this whole registry.db" (see onboarding_routes.py::
    create_admin) -- it is a first-run route, not a multi-company signup
    flow, so a SECOND real onboarding call is not how a genuinely
    multi-tenant install (CLAUDE.md: "one install *can* host more than one
    company") actually comes to hold two companies day-to-day (that happens
    through data restore/merge, not through this route twice). This inserts
    the second company's admin row directly, with a REAL working password
    hash from the same hasher `create_admin` itself uses, so the login below
    is still a genuine `authenticate_registry_user` round trip -- only the
    row's origin is synthetic, not the authentication path that reads it."""
    from commercial_runtime.security.passwords import hash_password
    conn = sqlite3.connect(str(REGISTRY_DB))
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), company_id, 'OWNER', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()


def test_a_genuinely_multi_tenant_registry_is_never_mistaken_for_the_stuck_case():
    """The false-positive guard: `_converge_and_refuse_to_serve_if_stuck`
    must NEVER refuse to boot a legitimate multi-tenant install just because
    `rebind_company_id_after_activation()` can't single out one tenant to
    move -- that is CLAUDE.md's supported configuration, not a catastrophe.
    Two genuinely separate companies sharing one registry.db, no licence at
    all (the common case -- licensing is OFF by default): init_app() must
    complete without raising."""
    _reset_databases()
    email_a = 'owner-window-3a@test.local'
    email_b = 'owner-window-3b@test.local'
    _onboard_admin(email_a, company_name='Window Co 3A')
    company_a = _registry_company_id_for(email_a)

    company_b = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
    _insert_second_admin(company_b, email_b)
    assert company_a != company_b, 'fixture bug: the two admins share a tenant'

    _insert_product(company_a, sku='WIDGET-WINDOW-3A')
    _insert_product(company_b, sku='WIDGET-WINDOW-3B')

    # No licence written at all here -- deliberately: this is what proves
    # the guard is not merely "no licence means skip", it is "read the
    # actual multi-tenant shape of registry.db and recognise it".
    _app_module.init_app()  # must not raise

    with app.test_client() as c:
        _login(c, email_a, password='WindowTestPW1')
        r = c.get('/api/sub/retail/products')
    assert [p['sku'] for p in r.get_json()['data']] == ['WIDGET-WINDOW-3A']

    with app.test_client() as c:
        _login(c, email_b, password='WindowTestPW2')
        r = c.get('/api/sub/retail/products')
    assert [p['sku'] for p in r.get_json()['data']] == ['WIDGET-WINDOW-3B']


# ── focused unit proof of the guard's one decision branch ──────────────────
#
# Getting rebind_company_id_after_activation() to organically report
# 'failed' against a REAL multi-tenant registry, without the retail-side
# rebind ALSO quietly merging the two tenants' data as an unrelated side
# effect (it would -- see this test file's own history), is its own small
# essay. The two tests above already prove the organic end-to-end shape for
# "no licence" and "single tenant, genuinely stuck". These two isolate the
# exact branch that separates them: given retail reports 'failed' and a real
# Owner-issued id exists, does the registry hold ONLY that one tenant or
# more than one? `_converge_and_refuse_to_serve_if_stuck` reads its
# collaborators as module globals (not as parameters), so they can be
# monkeypatched directly on `_app_module` without needing a real registry.db
# read for this specific decision.

class _DummyConn:
    def close(self):
        pass


def test_the_guard_refuses_when_the_registry_is_unambiguously_the_owner_id_and_retail_failed(monkeypatch):
    monkeypatch.setattr(_app_module, 'rebind_company_id_after_activation',
                         lambda: {'status': 'failed', 'reason': 'simulated'})
    monkeypatch.setattr(_app_module, 'registry_owner_issued_company_id', lambda: OWNER_COMPANY_ID)
    monkeypatch.setattr(_app_module, 'registry_company_tenant_ids', lambda conn: {OWNER_COMPANY_ID})
    monkeypatch.setattr(_app_module, 'get_registry_conn', lambda: _DummyConn())

    with pytest.raises(RuntimeError, match='REFUSING TO SERVE'):
        _app_module._converge_and_refuse_to_serve_if_stuck()


def test_the_guard_never_refuses_for_a_genuinely_multi_tenant_registry_even_if_retail_failed(monkeypatch):
    monkeypatch.setattr(_app_module, 'rebind_company_id_after_activation',
                         lambda: {'status': 'failed', 'reason': 'simulated'})
    monkeypatch.setattr(_app_module, 'registry_owner_issued_company_id', lambda: OWNER_COMPANY_ID)
    monkeypatch.setattr(_app_module, 'registry_company_tenant_ids',
                         lambda conn: {OWNER_COMPANY_ID, SECOND_TENANT_ID})
    monkeypatch.setattr(_app_module, 'get_registry_conn', lambda: _DummyConn())

    _app_module._converge_and_refuse_to_serve_if_stuck()  # must not raise
