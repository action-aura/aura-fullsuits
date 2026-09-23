"""Aura Retail -- the activate-then-create-admin identity split (the
landmine found by a live two-device licensing test).

A till can be brought up in two orders:

    UI ORDER (what the desktop wizard actually does -- app-shell.js
    ~1836-1875):      create-admin  THEN  activate
    REVERSE ORDER (what an API caller or fleet-provisioning script would
    naturally do):     activate      THEN  create-admin

In the reverse order, identity used to SPLIT. Activation seeds registry.db's
`company_settings` with the Owner-issued `license_public_id`
(`commercial_runtime/identity/company_rebind.py::
seed_company_settings_for_joining_device`, called from
`rebind_company_id_after_activation`), but `create_admin`
(`commercial_runtime/identity/onboarding_routes.py`) derived the new admin's
`company_id` from `cfg.get('company_id')` (config.json) or a fresh
`hashlib.md5(email)`, never consulting that row. Measured live, before the
fix: a till activated first ended up with `company_id`
`fa5c6b8a76ce2906a92eb7a58f77c8d2` -- exactly `md5("owner@till-a.test")` --
instead of the Owner-issued id activation had just written, and a second,
orphaned `company_settings` row appeared alongside the real one (the
`INSERT OR REPLACE` in `create_admin` inserts fresh rather than replacing
when the `company_id` it is given does not match the existing row).

THE FIX: `create_admin` now reads `company_settings` from the SAME
connection/transaction before deriving anything else, and adopts that row's
`company_id` when one is present -- identity the identity layer has already
established is authoritative and must never be silently overridden by a
locally-derived hash. See the comment block at the `company_id = ...` line
in onboarding_routes.py for the full reasoning, including why this can never
face two candidate ids to arbitrate between (`seed_company_settings_for_
joining_device` only ever seeds once, before any admin exists) and why the
"licence changed but nobody re-derived yet" conflict case is handled by
simply never looking past the row that is already there (`test_a_stale_
company_settings_row_is_not_silently_overridden_by_a_newer_licence` below).

Bootstrap mirrors commercial_runtime/identity/tests/
test_registry_v4_session_invalidation.py: boots the REAL retail app
(`app.init_app()`), drives the REAL `/api/onboarding/create-admin` route
over HTTP, and fires the REAL `on_activation_success` hook
(`app.py::_on_licence_activated`) rather than reasoning about either in
isolation.

MUTATION PROOFS (quoted verbatim in the commit/report that introduces this
file):

  1. Reverting the fix (restoring `company_id = cfg.get('company_id') or
     hashlib.md5(email.encode()).hexdigest()`) makes
     `test_activate_then_create_admin_adopts_the_owner_issued_company_id`
     fail -- the reported defect, reproduced and then re-caught.
  2. Dropping the `if _existing_company_settings else None` guard (so a
     genuinely fresh install with no `company_settings` row at all crashes
     on `None['company_id']` instead of falling through to the unchanged
     fallback chain) makes BOTH
     `test_create_admin_then_activate_still_works_exactly_as_today` and
     `test_genuinely_first_device_with_no_owner_id_still_gets_the_md5_
     fallback` fail with a 500 -- while
     `test_activate_then_create_admin_adopts_the_owner_issued_company_id`
     stays green, because that scenario's `company_settings` row is never
     `None`. This is the one that proves the guard matters for the
     everyday path every real shop already uses, not just for the reported
     bug.
  3. Reading a FRESH `company_rebind.owner_issued_company_id()` instead of
     the already-seeded `company_settings` row makes `test_a_stale_
     company_settings_row_is_not_silently_overridden_by_a_newer_licence`
     fail, while leaving test 1 green (in that scenario the fresh read and
     the seeded row agree) -- proving the conflict-case decision (defer to
     already-established local identity, never re-derive) is independently
     pinned, not incidentally satisfied by test 1.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest products/retail/tests/retail_activation_order_test.py -v
"""
import hashlib
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

DATA = Path(tempfile.mkdtemp(prefix="aura_activation_order_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.company_rebind import (  # noqa: E402
    owner_issued_company_id,
    seed_company_settings_for_joining_device,
)
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402

REGISTRY_DB = DATA / 'database' / 'registry.db'
LICENSING_DB = DATA / 'database' / 'subsystems' / 'licensing.db'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _reset_databases():
    """See test_registry_v4_session_invalidation.py::_reset_databases for
    why this clears on-disk state and re-runs init_app() rather than
    re-importing `app`, and why the split-state refusal flag (module-level,
    not on-disk) is cleared too."""
    for db_path in (REGISTRY_DB, DATA / 'database' / 'subsystems' / 'retail.db', LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    config_path = DATA / 'config.json'
    if config_path.exists():
        config_path.unlink()
    _app_module._split_state_refusal['reason'] = None
    _app_module.init_app()


def _write_licence_state(license_public_id):
    """Same helper/shape as commercial_runtime/identity/tests/
    test_registry_v4_company_rebind.py and
    test_registry_v4_session_invalidation.py -- writes licensing.db's
    `licensing_state` row directly, bypassing the signed-assertion HTTP
    dance that real activation goes through, the way this codebase's own
    activation-hook tests already do."""
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


def _activate(license_public_id):
    """Fires the REAL `on_activation_success` hook
    (`app.py::_on_licence_activated`) -- the exact function
    `make_licensing_blueprint` wires into `/api/licensing/activate` -- after
    writing the licence state that a real activation would have written."""
    _write_licence_state(license_public_id)
    _app_module._on_licence_activated()


def _create_admin(client, email, password='ActivationOrderPW1', company_name='Activation Order Co'):
    return client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': email, 'password': password, 'company_name': company_name,
    })


def _admin_row(email):
    conn = sqlite3.connect(str(REGISTRY_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, company_id, email FROM users WHERE role='admin' AND LOWER(email)=?",
        (email.lower(),),
    ).fetchone()
    conn.close()
    return row


def _company_settings_rows():
    conn = sqlite3.connect(str(REGISTRY_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, company_id FROM company_settings").fetchall()
    conn.close()
    return rows


# ── 1. the regression: activate, THEN create-admin ─────────────────────────

def test_activate_then_create_admin_adopts_the_owner_issued_company_id():
    """THE BUG. A till activated before it ever creates a local admin must
    not derive a second, unrelated tenant key for that admin -- it must
    adopt the Owner-issued id activation already seeded into
    `company_settings`.

    Also pins the second symptom measured during reproduction: before the
    fix, `create_admin`'s `INSERT OR REPLACE INTO company_settings` landed
    a SECOND row (fresh `id`, mismatched `company_id`) alongside the one
    activation seeded, because REPLACE only fires on an actual constraint
    conflict. Once `company_id` agrees, the UNIQUE constraint on
    `company_settings.company_id` makes REPLACE do what its name says, so
    exactly one row survives.
    """
    _reset_databases()
    owner_company_id = f'owner-issued-{uuid.uuid4()}'
    _activate(owner_company_id)

    # Sanity precondition: activation must have actually seeded the row this
    # test is about, or a later failure here would be the fixture's bug, not
    # create_admin's. Mirrors the "fixture manufactures the state that hides
    # the bug" caution in ENGINEERING.md.
    seeded = _company_settings_rows()
    assert len(seeded) == 1 and seeded[0]['company_id'] == owner_company_id, (
        f'fixture bug: activation did not seed company_settings with the owner id, got {seeded!r}'
    )

    client = app.test_client()
    email = 'owner-activate-first@test.local'
    r = _create_admin(client, email)
    assert r.status_code == 200, r.get_json()

    admin = _admin_row(email)
    assert admin is not None, 'create-admin reported success but wrote no admin row'
    assert admin['company_id'] == owner_company_id, (
        f"admin landed under company_id={admin['company_id']!r}, not the Owner-issued "
        f"{owner_company_id!r} -- identity split between this admin and the licence "
        f"activation already established. (md5(email) would have been "
        f"{hashlib.md5(email.encode()).hexdigest()!r}.)"
    )

    settings = _company_settings_rows()
    assert len(settings) == 1, (
        f'expected exactly one company_settings row after create-admin, found {len(settings)}: '
        f'{[dict(row) for row in settings]!r}'
    )
    assert settings[0]['company_id'] == owner_company_id


# ── 2. the no-regression half: create-admin, THEN activate (UI order) ──────

def test_create_admin_then_activate_still_works_exactly_as_today():
    """The path every real shop takes today, via the desktop wizard
    (app-shell.js ~1836-1875). `create_admin` runs with no `company_settings`
    row yet (nothing has activated), so it must fall through to its
    unchanged `md5(email)` derivation exactly as before this fix -- and the
    PRE-EXISTING rebind mechanism (unrelated to this fix,
    `rebind_company_id_after_activation`) is what later moves that admin's
    `company_id` onto the Owner-issued id once activation runs.
    """
    _reset_databases()
    client = app.test_client()
    email = 'owner-create-first@test.local'
    r = _create_admin(client, email)
    assert r.status_code == 200, r.get_json()

    admin_before = _admin_row(email)
    expected_md5 = hashlib.md5(email.encode()).hexdigest()
    assert admin_before['company_id'] == expected_md5, (
        f"create-admin-first must still derive md5(email) with no licence present -- got "
        f"{admin_before['company_id']!r}, expected {expected_md5!r}"
    )

    owner_company_id = f'owner-issued-{uuid.uuid4()}'
    _activate(owner_company_id)

    admin_after = _admin_row(email)
    assert admin_after['company_id'] == owner_company_id, (
        f"the pre-existing rebind-on-activation mechanism did not converge this admin onto "
        f"the Owner-issued id -- got {admin_after['company_id']!r}. This is not this fix's "
        f"code path, but a regression here would mean the UI-order flow -- what every real "
        f"shop uses today -- silently stopped converging."
    )


# ── 3. genuinely-first-device: no licence, ever ─────────────────────────────

def test_genuinely_first_device_with_no_owner_id_still_gets_the_md5_fallback():
    """No licence has ever been activated on this install (the overwhelming
    common case -- licensing is off by default). `company_settings` has no
    row at all, so create_admin must still fall back to `md5(email)`,
    unchanged."""
    _reset_databases()
    client = app.test_client()
    email = 'owner-no-licence-ever@test.local'
    r = _create_admin(client, email)
    assert r.status_code == 200, r.get_json()

    admin = _admin_row(email)
    expected_md5 = hashlib.md5(email.encode()).hexdigest()
    assert admin['company_id'] == expected_md5, (
        f"a genuinely first device with no Owner-issued id must still get md5(email) -- got "
        f"{admin['company_id']!r}, expected {expected_md5!r}"
    )
    assert owner_issued_company_id(str(DATA)) is None, (
        'fixture bug: a licence id is somehow present in this "no licence ever" scenario'
    )


# ── 4. the conflict case: an already-established local identity vs. a ──────
#      NEWER licence read that has not (yet) been reconciled by a rebind

def test_a_stale_company_settings_row_is_not_silently_overridden_by_a_newer_licence():
    """Decision: `company_settings` already carrying an id is authoritative
    for `create_admin`, full stop -- it never re-derives from licensing.db
    itself. So if this device's `company_settings` row was seeded under one
    licence and licensing.db has since started reporting a DIFFERENT one
    (a licence swap, a re-activation against the wrong shop -- anything
    that can land before a rebind has run again), `create_admin` must not
    try to arbitrate between the two. Silently re-parenting this device's
    (possibly already-synced) identity onto whatever licensing.db says right
    now would risk moving a shop's rows under the wrong tenant the moment an
    admin happens to be created -- worse than simply deferring to the
    identity already established. Reconciling a genuine licence change, if
    it should happen at all, is `rebind_company_id`'s job (already reachable
    from the next activation or boot), not create_admin's.

    Constructed directly rather than via two real activations, because a
    second real `_on_licence_activated()` call would use the EXISTING
    rebind machinery to converge `company_settings` onto the new id itself
    -- which would resolve the conflict rather than leave it open to
    observe. This models the narrower window: `company_settings` already
    has a row, and licensing.db independently reports something else, and
    nothing has reconciled them yet.
    """
    _reset_databases()
    old_company_id = f'old-licence-{uuid.uuid4()}'
    new_company_id = f'new-licence-{uuid.uuid4()}'

    conn = registry_conn()
    seeded = seed_company_settings_for_joining_device(conn, old_company_id)
    conn.commit()
    conn.close()
    assert seeded is True, 'fixture bug: company_settings was not seeded with the old id'

    _write_licence_state(new_company_id)  # licensing.db now disagrees; no rebind run since

    client = app.test_client()
    email = 'owner-stale-settings@test.local'
    r = _create_admin(client, email)
    assert r.status_code == 200, r.get_json()

    admin = _admin_row(email)
    assert admin['company_id'] == old_company_id, (
        f"create-admin adopted {admin['company_id']!r} instead of the already-established "
        f"{old_company_id!r} -- it re-derived from a fresher licensing.db read instead of "
        f"deferring to the identity already established locally. That is the unsafe "
        f"direction: it would silently move this device's identity onto a different "
        f"licence the moment an admin happens to be created."
    )
    assert admin['company_id'] != new_company_id


# ── 5. confirm the unaffected edge case: admin already exists ──────────────

def test_owner_id_present_and_admin_already_exists_still_409s():
    """Confirms this edge case is unaffected by the fix: the existing-admin
    409 gate runs BEFORE the company_id derivation this fix touches, in
    either activation order."""
    _reset_databases()
    owner_company_id = f'owner-issued-{uuid.uuid4()}'
    _activate(owner_company_id)

    client = app.test_client()
    email = 'owner-first-admin@test.local'
    r1 = _create_admin(client, email)
    assert r1.status_code == 200, r1.get_json()

    r2 = _create_admin(client, 'owner-second-admin@test.local')
    assert r2.status_code == 409, r2.get_json()
    assert 'already exists' in (r2.get_json() or {}).get('error', '')
