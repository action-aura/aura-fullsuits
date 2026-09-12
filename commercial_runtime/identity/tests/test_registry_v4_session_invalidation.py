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

MUTATION PROOF #1 -- the defect itself. Neutralising the `session_version`
bump in `rebind_company_id` makes
`test_the_activating_admins_own_session_is_revoked_not_silently_emptied`
fail with, verbatim:

    AssertionError: admin session was NOT revoked by the rebind --
    status=200 body={'data': [], 'status': 'success'}

-- the measured defect reproduced over real HTTP, not reasoned about.

MUTATION PROOF #2 -- THE ONE THAT WAS MISSING, and the reason this file has
more than one test in it. The first version of this file asserted BOTH
sessions inside a single `for client, label in (...)` loop in a single test
function. That loop can only ever report the FIRST session that fails: a fix
that repaired only the activating admin's own session -- the shape a
"refresh the current request's session" patch naturally takes -- would have
left every OTHER till in the shop blank and the suite would still have gone
green on the admin's 401. Nobody is standing at those other tills watching.
So the two sessions now live in two SEPARATE test functions, and the
narrowing mutation

    UPDATE "users" SET session_version = session_version + 1
     WHERE company_id=? AND rowid=(SELECT MIN(rowid) FROM "users" WHERE company_id=?)

(bump ONE account instead of every moved account) is required to leave
`test_the_activating_admins_own_session_is_revoked_not_silently_emptied`
PASSING while
`test_a_second_till_that_never_touched_the_activation_is_revoked_too` fails
with, verbatim:

    AssertionError: the SECOND till's session was NOT revoked -- status=200
    body={'data': [], 'status': 'success'}. This session never touched the
    activation; it belongs to a till nobody is watching. A fix that only
    refreshes the session of whoever triggered the rebind leaves every other
    terminal in the shop staring at an empty product list with no error
    anywhere.

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
    on-disk state and re-runs init_app() rather than re-importing `app`.

    The split-state refusal flag is cleared too. It is module-level state on
    `app.py`, not on-disk state, so deleting the databases does not touch it
    -- and a leftover refusal from an earlier test would make every request
    in the NEXT test return 503, which would look exactly like the
    revocation this file is trying to measure. See
    products/retail/tests/retail_registry_v4_activation_split_state_test.py
    for the same clearing, there for the same reason."""
    for db_path in (REGISTRY_DB, RETAIL_DB, LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    _app_module._split_state_refusal['reason'] = None
    _app_module.init_app()


def _users_session_versions():
    """Every registry account's (company_id, session_version), keyed by the
    account's own id. Read with raw SQL rather than through any function in
    the module under test -- a helper that borrowed `company_scoped_tables`
    or the rebind's own bookkeeping could report whatever the bug reports."""
    conn = sqlite3.connect(str(REGISTRY_DB))
    rows = conn.execute(
        'SELECT id, company_id, COALESCE(session_version, 1) FROM users'
    ).fetchall()
    conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _stage_two_live_sessions(admin_email, second_email, sku):
    """The shared setup every activation test below starts from: one real
    onboarded admin, a SECOND real account under the SAME tenant, one real
    product, and TWO real logged-in HTTP sessions that both genuinely see
    that product right now.

    Returns `(admin_client, second_client, legacy_company_id)`.

    The pre-activation `assert len(...) == 1` is not decoration: without it a
    later "this session sees an empty shop" failure could equally mean the
    fixture never created a visible product, and the test would be reporting
    its own bug as the product's.
    """
    _reset_databases()
    _onboard_admin(admin_email)
    legacy_company_id = _registry_company_id_for(admin_email)
    _insert_second_admin_same_tenant(legacy_company_id, second_email)
    _insert_product(legacy_company_id, sku=sku)

    admin_client = app.test_client()
    _login(admin_client, admin_email, 'SessionInvalPW1')
    second_client = app.test_client()
    _login(second_client, second_email, 'SessionInvalPW2')

    for client, label in ((admin_client, 'admin'), (second_client, 'second')):
        before = client.get('/api/sub/retail/products').get_json()
        assert before['status'] == 'success', (label, before)
        assert len(before['data']) == 1, f'{label}: fixture did not create a visible product'

    return admin_client, second_client, legacy_company_id


def _activate_and_confirm_the_rebind_really_happened(expected_accounts=2, expected_products=1):
    """Fire the REAL `on_activation_success` hook and then prove, from the
    databases directly, that a genuine full rebind landed.

    Every test below needs this because the interesting assertion is about
    what a rebind DOES to live sessions -- so a rebind that silently did not
    happen would make all of them pass while proving nothing. This is the
    "fixture manufacturing the state that hides the bug" shape, checked
    against rather than assumed away."""
    _write_licence_state(OWNER_COMPANY_ID)
    _app_module._on_licence_activated()

    reg_conn = sqlite3.connect(str(REGISTRY_DB))
    moved_accounts = reg_conn.execute(
        'SELECT COUNT(*) FROM users WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0]
    reg_conn.close()
    assert moved_accounts == expected_accounts, (
        f'fixture bug: activation moved {moved_accounts} account(s) onto the Owner-issued '
        f'id, expected {expected_accounts} -- this test would not exercise the defect'
    )

    retail_conn = sqlite3.connect(str(RETAIL_DB))
    converged = retail_conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0]
    retail_conn.close()
    assert converged == expected_products, (
        f'fixture bug: retail.db has {converged} product(s) on the Owner-issued id, '
        f'expected {expected_products} -- retail did not converge, so a session that '
        f'sees an empty shop below would be seeing it for the wrong reason'
    )


# ── proof 1: the activating admin's own session ─────────────────────────────

def test_the_activating_admins_own_session_is_revoked_not_silently_emptied():
    """The defect-1 reproduction for the session that TRIGGERED the
    activation -- the admin who just paid, watching their own screen.

    Deliberately asserts on ONE session and one only. Its sibling below
    covers the second till. Two sessions asserted inside one loop in one
    test function is what hid the far more dangerous half of this defect for
    a whole verification round: the loop stops at the first failure, so a
    fix that repaired only this session would still have gone green here
    while leaving every other terminal blank.
    """
    admin_client, _second_client, _legacy = _stage_two_live_sessions(
        'owner-sessioninval-1@test.local', 'second-sessioninval-1@test.local',
        sku='WIDGET-SESSIONINVAL-1',
    )
    _activate_and_confirm_the_rebind_really_happened()

    r = admin_client.get('/api/sub/retail/products')
    body = r.get_json()
    assert r.status_code == 401, (
        f'admin session was NOT revoked by the rebind -- status={r.status_code} '
        f'body={body!r}. A 200 here (of any shape) after a company_id rebind means '
        f'this session is still filtering every query on a tenant key that no longer '
        f'has any matching rows -- the exact silent-empty-shop catastrophe this fix '
        f'exists to prevent.'
    )
    assert body['error'] == REVOKED_MESSAGE, body

    # Recovery: the ordinary next step after a 401 restores the real shop.
    _login(admin_client, 'owner-sessioninval-1@test.local', 'SessionInvalPW1')
    after = admin_client.get('/api/sub/retail/products').get_json()
    assert after['status'] == 'success', after
    assert [p['sku'] for p in after['data']] == ['WIDGET-SESSIONINVAL-1'], (
        'a fresh login after the forced revocation did not see the real shop data'
    )


# ── proof 2: EVERY OTHER till, the one nobody is watching ───────────────────

def test_a_second_till_that_never_touched_the_activation_is_revoked_too():
    """THE PROOF THAT MATTERS MOST HERE, and the one a single-session test
    cannot make.

    `_on_licence_activated` is fired by whichever terminal the licence key
    was typed into. Every OTHER till in the shop is mid-shift, logged in
    under the OLD tenant key, and has no idea anything happened. If the fix
    only reached the activating session, those tills would carry on issuing
    `200 OK` with an empty product list -- no error, no 401, nothing in a
    log -- until somebody noticed their shop had apparently lost its entire
    catalogue.

    This test therefore touches the second client ONLY. It must be capable
    of failing on its own, with the admin's own test still passing; see this
    module's docstring for the narrowing mutation that proves it is.
    """
    _admin_client, second_client, _legacy = _stage_two_live_sessions(
        'owner-sessioninval-2@test.local', 'second-sessioninval-2@test.local',
        sku='WIDGET-SESSIONINVAL-2',
    )
    _activate_and_confirm_the_rebind_really_happened()

    r = second_client.get('/api/sub/retail/products')
    body = r.get_json()
    assert r.status_code == 401, (
        f"the SECOND till's session was NOT revoked -- status={r.status_code} "
        f'body={body!r}. This session never touched the activation; it belongs to a '
        f'till nobody is watching. A fix that only refreshes the session of whoever '
        f'triggered the rebind leaves every other terminal in the shop staring at an '
        f'empty product list with no error anywhere.'
    )
    assert body['error'] == REVOKED_MESSAGE, body

    _login(second_client, 'second-sessioninval-2@test.local', 'SessionInvalPW2')
    after = second_client.get('/api/sub/retail/products').get_json()
    assert after['status'] == 'success', after
    assert [p['sku'] for p in after['data']] == ['WIDGET-SESSIONINVAL-2'], (
        "the second till's fresh login after revocation did not see the real shop data"
    )


# ── the rule, not the outcome ───────────────────────────────────────────────

def test_the_bump_reaches_every_moved_account_not_merely_some_of_them():
    """Asserts THE RULE RAN, not just that one observable outcome looked
    right.

    The two tests above assert an OUTCOME (a 401 over HTTP) for two specific
    sessions. That is the right thing to assert -- it is what a shop
    actually experiences -- but on its own it is satisfiable by accident: a
    third account with no live session, a fourth added next quarter, or an
    account whose row happened to sort differently could all be missed
    without either test noticing, because neither has a session to notice
    with.

    So this one reads the `session_version` column directly, before and
    after, and requires the set of accounts whose version STRICTLY INCREASED
    to be exactly the set of accounts the rebind moved. Not "at least one",
    not "the ones we happen to have logged in" -- exactly, both directions.
    """
    _stage_two_live_sessions(
        'owner-sessioninval-3@test.local', 'second-sessioninval-3@test.local',
        sku='WIDGET-SESSIONINVAL-3',
    )
    before = _users_session_versions()
    assert before, 'fixture bug: no registry accounts at all'
    expected_to_move = {uid for uid, (cid, _v) in before.items() if cid != OWNER_COMPANY_ID}
    assert len(expected_to_move) == 2, (
        f'fixture bug: expected exactly 2 accounts on the legacy key, found '
        f'{len(expected_to_move)} -- this test would not measure what it claims to'
    )

    _activate_and_confirm_the_rebind_really_happened()

    after = _users_session_versions()
    assert set(after) == set(before), 'the rebind added or removed an account row'
    actually_moved = {uid for uid in after if after[uid][0] == OWNER_COMPANY_ID and before[uid][0] != OWNER_COMPANY_ID}
    bumped = {uid for uid in after if after[uid][1] > before[uid][1]}

    assert actually_moved == expected_to_move, (
        f'fixture bug: the rebind moved {actually_moved!r}, expected {expected_to_move!r}'
    )
    assert bumped == actually_moved, (
        f'the session_version bump did not cover exactly the accounts the rebind moved: '
        f'moved={sorted(actually_moved)!r} bumped={sorted(bumped)!r}. Every account '
        f'missing from `bumped` is a terminal that can stay logged in under a tenant key '
        f'that owns zero rows; every extra one is a terminal logged out for no reason. '
        f'before={before!r} after={after!r}'
    )


# ── the false-positive side: do NOT log the shop out for no reason ──────────

def test_an_already_converged_install_does_not_log_the_whole_shop_out_again():
    """The bump must fire on a rebind that MOVES rows, and never on one that
    does not.

    This is not hypothetical housekeeping. `app.py::init_app()` calls
    `_converge_and_refuse_to_serve_if_stuck()` -- and therefore the whole
    rebind -- on EVERY SINGLE BOOT, by design (see that function's
    docstring: the version-gated migrations can no longer self-heal, so the
    catch-up has to be unconditional). If the `session_version` bump ran on
    that already-converged no-op path too, every restart of the app would
    revoke every session in the shop, and a fix for a once-per-lifetime
    activation glitch would have become a permanent, daily, every-launch
    logout. The `sum(before_old.values()) == 0` early return in
    `rebind_company_id` is what keeps that from happening; this test is what
    keeps that early return honest.
    """
    admin_client, second_client, _legacy = _stage_two_live_sessions(
        'owner-sessioninval-4@test.local', 'second-sessioninval-4@test.local',
        sku='WIDGET-SESSIONINVAL-4',
    )
    _activate_and_confirm_the_rebind_really_happened()

    # Both sessions are now legitimately revoked; log both back in, so the
    # cookies below carry the CURRENT session_version and any further bump
    # would be visible as a revocation.
    _login(admin_client, 'owner-sessioninval-4@test.local', 'SessionInvalPW1')
    _login(second_client, 'second-sessioninval-4@test.local', 'SessionInvalPW2')
    settled = _users_session_versions()

    # A restart. Real code path, not a simulation: exactly what the launcher
    # runs, on an install that is already fully converged.
    _app_module.init_app()
    _app_module.init_app()  # and a second one, in case the first is what converged

    after = _users_session_versions()
    assert after == settled, (
        f'a boot of an already-converged install changed session_version: '
        f'before={settled!r} after={after!r}. Every launch of the app would log the '
        f'entire shop out.'
    )

    for client, label in ((admin_client, 'admin'), (second_client, 'second')):
        r = client.get('/api/sub/retail/products')
        assert r.status_code == 200, (
            f'{label} session was revoked by an ordinary restart of an already-converged '
            f'install -- status={r.status_code} body={r.get_json()!r}'
        )
        assert [p['sku'] for p in r.get_json()['data']] == ['WIDGET-SESSIONINVAL-4'], (
            label, r.get_json()
        )


def test_another_tenants_sessions_are_not_revoked_by_this_tenants_rebind():
    """CLAUDE.md: "one install *can* host more than one company". When a
    caller passes `old_company_id` explicitly -- the only way a rebind
    proceeds at all on a multi-tenant registry -- the bump must follow the
    rows it actually moved and stop there.

    Bumping every account in the file instead would log a completely
    unrelated company's staff out because their neighbour activated a
    licence: a real, visible outage for people who were never involved, and
    exactly the kind of over-broad blast radius the `WHERE company_id=?`
    clause on the bump exists to bound. Driven through the real
    `rebind_company_id` API, not the activation hook, because the activation
    hook correctly refuses an ambiguous registry outright.
    """
    _reset_databases()
    admin_email = 'owner-sessioninval-5@test.local'
    other_email = 'other-tenant-sessioninval-5@test.local'
    _onboard_admin(admin_email)
    legacy_company_id = _registry_company_id_for(admin_email)

    other_tenant_id = 'cccccccccccccccccccccccccccccccc'
    assert other_tenant_id not in (legacy_company_id, OWNER_COMPANY_ID)
    _insert_second_admin_same_tenant(other_tenant_id, other_email)  # a DIFFERENT company

    before = _users_session_versions()
    other_ids = {uid for uid, (cid, _v) in before.items() if cid == other_tenant_id}
    assert len(other_ids) == 1, f'fixture bug: {before!r}'

    from commercial_runtime.identity.company_rebind import rebind_company_id
    conn = sqlite3.connect(str(REGISTRY_DB))
    try:
        result = rebind_company_id(conn, OWNER_COMPANY_ID, old_company_id=legacy_company_id)
    finally:
        conn.close()
    assert result['status'] == 'rebound', result

    after = _users_session_versions()
    for uid in other_ids:
        assert after[uid] == before[uid], (
            f"the other tenant's account {uid!r} was changed by a rebind that had nothing "
            f'to do with it: before={before[uid]!r} after={after[uid]!r}. Its company_id '
            f'must be untouched AND its session_version must not move -- their staff were '
            f'never part of this activation.'
        )
    moved = {uid for uid in after if after[uid][0] == OWNER_COMPANY_ID}
    assert moved and moved.isdisjoint(other_ids), (before, after)
    assert all(after[uid][1] > before[uid][1] for uid in moved), (
        f'the rebind moved accounts {sorted(moved)!r} without bumping them: '
        f'before={before!r} after={after!r}'
    )


def test_a_rebind_that_rolls_back_leaves_every_session_version_untouched():
    """The atomicity half of the claim `rebind_company_id`'s own comment
    makes: "either every session on this tenant is revoked together on its
    very next request, or none of them are -- never the split outcome."

    The bump lives inside the same `BEGIN IMMEDIATE` as the row moves, so a
    failure anywhere in that transaction must take the bump down with it. If
    it did not, a rebind that correctly rolled back would still have logged
    the whole shop out -- an outage caused by a change that was, itself,
    successfully refused.

    The failure is injected the same way
    test_registry_v4_company_rebind.py::test_rebind_rolls_back_completely_
    when_one_table_fails_mid_flight injects one: a Connection subclass that
    raises on a specific UPDATE, since `sqlite3.Connection.execute` is
    read-only on the C type and cannot be monkeypatched.
    """
    _reset_databases()
    admin_email = 'owner-sessioninval-6@test.local'
    second_email = 'second-sessioninval-6@test.local'
    _onboard_admin(admin_email)
    legacy_company_id = _registry_company_id_for(admin_email)
    _insert_second_admin_same_tenant(legacy_company_id, second_email)

    before = _users_session_versions()
    assert len(before) == 2, before

    class _FailingConnection(sqlite3.Connection):
        """Fails the LAST scoped table's UPDATE -- late enough that the
        `users` rows (and therefore the bump) have already been written
        inside the transaction, so a rollback that forgot about the bump
        would be visible."""
        def execute(self, sql, *args):
            if sql.startswith('UPDATE "') and 'SET company_id=?' in sql:
                _FailingConnection.updates_seen.append(sql)
                if len(_FailingConnection.updates_seen) == _FailingConnection.fail_on:
                    raise sqlite3.OperationalError('simulated disk failure mid-rebind')
            return sqlite3.Connection.execute(self, sql, *args)

    from commercial_runtime.identity.company_rebind import (
        CompanyRebindError, company_scoped_tables, rebind_company_id,
    )
    probe = sqlite3.connect(str(REGISTRY_DB))
    table_count = len(company_scoped_tables(probe))
    probe.close()
    assert table_count > 1, 'fixture bug: only one scoped table, nothing to fail late on'

    _FailingConnection.updates_seen = []
    _FailingConnection.fail_on = table_count  # the last one

    conn = sqlite3.connect(str(REGISTRY_DB), factory=_FailingConnection)
    try:
        raised = None
        try:
            rebind_company_id(conn, OWNER_COMPANY_ID)
        except (sqlite3.OperationalError, CompanyRebindError) as exc:
            raised = exc
    finally:
        conn.close()
    assert raised is not None, 'fixture bug: the injected failure never fired'

    after = _users_session_versions()
    assert after == before, (
        f'a rebind that FAILED and rolled back still changed session state: '
        f'before={before!r} after={after!r}. The row moves were correctly undone but '
        f'the session_version bump was not, so every till in the shop would be logged '
        f'out by a change that never actually happened.'
    )
