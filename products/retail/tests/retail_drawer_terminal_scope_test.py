"""Aura Retail -- Phase 4: the cash drawer belongs to a TERMINAL, and every
route that reads or writes one says so.

── THE BUG THIS FILE EXISTS FOR ────────────────────────────────────────────
A shop with a desktop till and a phone had ONE drawer, because `cash_sessions`
allowed one open session per BRANCH and `_open_cash_session_id()` looked a
session up by company+branch. Every device selling into that branch resolved
to the same session id and stamped it onto its sales. By the time the desktop
cashier pressed Close, the phone's cash was already part of the desktop
drawer's `cash_sales` -- permanently, because nothing had recorded that they
were ever different. The desktop's Z report was over by exactly the phone's
takings and the phone's shift did not exist.

The fix is at the STAMP, not at the report. `_cash_session_report` has always
summed strictly by `session_id` and has always been right about the rows it
was handed; it was handed the wrong rows.

── HOW THIS FILE AVOIDS TESTING ITSELF ─────────────────────────────────────
Three failure shapes this programme keeps shipping, and what is done here
about each:

  1. ASSERTING AN OUTCOME WHERE THE CLAIM IS THAT A CHECK RAN. Every guard
     below is proven twice: once by its outcome, and once by flipping the
     input the guard reads and asserting the outcome TRACKS it. A test that
     only asserts "a cashier gets 403" would also pass against a route that
     403s everybody, including the owner.

  2. A FIXTURE THAT MANUFACTURES THE STATE THAT HIDES THE BUG. Two terminals
     here are two real terminal identities driving the SAME app against the
     SAME database through the SAME routes -- `at_terminal()` swaps what the
     device reports itself to be, exactly as two physical devices would. No
     row is hand-stamped with a terminal id to set a scene. The legacy-shop
     fixture at the bottom goes further: it is built by REPLAYING the old
     branch-wide stamp, so its contamination is the genuine article rather
     than a description of it.

  3. A GUARD WHOSE PASS CONDITION IS THE BUG SIGNATURE. `cash_sales == 0` is
     what a correctly-scoped drawer returns AND what a broken fixture that
     never sold anything returns. Every isolation claim here therefore
     asserts BOTH sides: this terminal's figure is exactly its own takings,
     the other terminal's is exactly its own, the two differ, and neither is
     zero.

── WHAT THIS FILE ASSUMES ABOUT THE SCHEMA ─────────────────────────────────
Phase 4's routes are written against the retail v16 contract (design §6, and
now `_migrate_bind_cash_drawer_to_terminal` in database/schema.py):
`terminal_id` backfilled from the device fingerprint, the one-open-per-BRANCH
partial unique index replaced by one-open-per-TERMINAL, and `ended_at` /
`ended_by` / `ended_reason` added for the ENDED/CLOSED split.

`_ensure_terminal_scoped_open_index()` reports which of those two index shapes
the database is in and installs the terminal one only if the migration has not
run -- because under the branch-wide index "two terminals, two drawers" is
physically impossible to set up, and this file could then only ever test one
till talking to itself. On a migrated database it does nothing and says so.
`test_the_open_drawer_index_is_one_of_two_known_shapes` fails if the schema is
in any shape other than those two, so a half-applied migration cannot pass
unnoticed.

Everything else is read from schema.py's own constants -- the status
vocabulary, the force-end reasons, the `System` sentinel -- rather than
re-spelled here, so this file cannot drift from the migration that writes the
rows it is asserting about.

Run:
    pytest products/retail/tests/retail_drawer_terminal_scope_test.py -v
"""
import contextlib
import os
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

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_drawer_scope_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop('AURA_DEV', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code='AURA_RETAIL', platform='WINDOWS')

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config['TESTING'] = True

from api import retail_api  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import (  # noqa: E402
    get_retail_conn,
    CASH_SESSION_STATUS_CLOSED, CASH_SESSION_STATUS_ENDED,
    V16_ENDED_BY_SYSTEM, V16_ENDED_REASON_STALE, V16_SUPERSEDED_BRANCH_INDEX,
    V16_TERMINAL_INDEX, V16_UNVERIFIED_END_REASONS,
)

API = '/api/sub/retail'

#: Two terminal identities. Real-shaped uuid4 strings, because that is what
#: `peek_local_device_uuid()` returns and `_terminal_short()` slices.
TILL_DESK = '8f0c4a2e-1111-4c3a-9d55-aaaaaaaa1234'
TILL_PHONE = '3b7e91d0-2222-4f18-8e21-bbbbbbbb5678'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# The two-terminal harness
# ─────────────────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def at_terminal(terminal_id, monkeypatch=None):
    """Run the block as though this process were the device `terminal_id`.

    Patches the ONE function the product uses to answer "which terminal am I"
    (`database.schema.local_terminal_id`, imported into retail_api). Nothing
    else is faked: the routes, the SQL, the stamping and the scoping are the
    real ones, so what the block exercises is genuinely "a second device
    talking to this backend".

    Restores the previous value on exit rather than setting a fixed default,
    so nesting works and one test cannot leak an identity into the next.
    """
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api.local_terminal_id = previous


def _ensure_terminal_scoped_open_index():
    """Apply the INDEX half of the retail v16 contract to this test's database.

    Returns 'already-migrated' when the product's own schema already carries
    the terminal-scoped index (v16 has landed and this function did nothing),
    or 'stand-in' when this function had to install it.

    See the module docstring: this is scaffolding for a migration owned
    elsewhere, and it exists because the pre-v16 index makes the two-drawer
    scenario impossible to construct at all. It touches nothing but the index.
    """
    conn = get_retail_conn()
    try:
        names = {row[1] for row in conn.execute("PRAGMA index_list('cash_sessions')").fetchall()}
        if V16_TERMINAL_INDEX in names:
            return 'already-migrated'
        conn.execute(f'DROP INDEX IF EXISTS {V16_SUPERSEDED_BRANCH_INDEX}')
        conn.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS {V16_TERMINAL_INDEX} '
            "ON cash_sessions(company_id, terminal_id) WHERE status='open'")
        conn.commit()
        return 'stand-in'
    finally:
        conn.close()


def _make_user(role, company_id, capabilities=None, email_prefix='drawer'):
    """Create a logged-in test account, optionally with individual capability
    codes overridden away from the role default.

    FIXED (real AUDIT gap, same defect reproduced in
    retail_drawer_money_sweep_test.py's copy of this helper): this used to
    `UPDATE user_capabilities SET access_level=? WHERE user_id=? AND
    capability_code=?` -- a table that exists NOWHERE in this repo. The real
    store is `user_permissions(id, user_id, subsystem, access_level)`
    (commercial_runtime/identity/registry_db.py), and despite the column
    being named `subsystem` it is where `user_accounts.
    seed_capabilities_for_user` writes one row PER CAPABILITY CODE (e.g.
    'retail.cash.close') -- `user_has_capability`/`session_has_capability`
    read it back the same way. No test in THIS file happened to pass
    `capabilities=` (dead code, never exercised here), so this file's
    baseline never hit the bug -- fixed anyway so a future `capabilities=`
    override in this file actually works instead of raising
    sqlite3.OperationalError. `conn.close()` is also moved into a `finally`
    for the same reason as the money-sweep file's copy: a raise here must
    never leak the registry connection while it holds the write lock.
    """
    email = f'{email_prefix}-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'DrawerScopePW1'
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            'INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, '
            'require_password_change) VALUES (?,?,?,?,?,?,?,?,0)',
            (user_id, str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:6].upper()}', email,
             hash_password(password), role, 'active'))
        conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                     (str(uuid.uuid4()), user_id, 'retail', 'full'))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        for code, level in (capabilities or {}).items():
            conn.execute('UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?',
                         (level, user_id, code))
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client, user_id


def _new_shop(price=100.0):
    """A company with an admin, a product and stock. Returns (admin, cid, pid)."""
    company_id = str(uuid.uuid4())
    admin, _uid = _make_user('admin', company_id)
    r = admin.post(f'{API}/products', json={
        'name': 'Drawer Widget', 'sku': f'DW-{uuid.uuid4().hex[:8]}',
        'cost_price': price / 2, 'sell_price': price, 'tax_rate': 0, 'initial_stock': 5000})
    assert r.status_code == 200, r.get_json()
    return admin, company_id, r.get_json()['data']['id']


def _open(client, opening_float=100.0):
    return client.post(f'{API}/cash-sessions/open', json={'opening_float': opening_float})


def _sell_cash(client, pid, amount):
    return client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': amount / 100.0}],
        'amount_paid': amount, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})


def _x(client, sid):
    return client.get(f'{API}/cash-sessions/{sid}/x-report')


def _end(client, sid, counted):
    return client.post(f'{API}/cash-sessions/{sid}/close', json={'closing_float_counted': counted})


def _approve(client, sid, body=None):
    return client.post(f'{API}/cash-sessions/{sid}/approve', json=body or {})


def _current(client):
    return client.get(f'{API}/cash-sessions/current')


@pytest.fixture(scope='module', autouse=True)
def _schema_mode():
    mode = _ensure_terminal_scoped_open_index()
    print(f'\n[drawer-scope] open-drawer index: {mode}')
    return mode


# ─────────────────────────────────────────────────────────────────────────────
# 0. THE HARNESS ITSELF -- because every claim below is a claim about it
# ─────────────────────────────────────────────────────────────────────────────

def test_the_open_drawer_index_is_one_of_two_known_shapes():
    """The v16 contract, or the thing it replaces. A THIRD shape means a
    half-applied migration, and every scoping claim in this file would then be
    measuring something nobody designed."""
    conn = get_retail_conn()
    try:
        names = {row[1] for row in conn.execute("PRAGMA index_list('cash_sessions')").fetchall()}
    finally:
        conn.close()
    per_terminal = V16_TERMINAL_INDEX in names
    per_branch = V16_SUPERSEDED_BRANCH_INDEX in names
    assert per_terminal != per_branch, (
        'cash_sessions must carry exactly one open-drawer uniqueness index. '
        f'per_terminal={per_terminal} per_branch={per_branch} indexes={sorted(names)}')


def test_the_two_terminal_harness_really_changes_what_the_device_reports():
    """ANTI-VACUITY FOR EVERYTHING BELOW.

    If `at_terminal()` did nothing, every isolation test in this file would be
    one terminal talking to itself and would pass for the worst possible
    reason. So: the product's own answer to "which terminal am I" is read
    through the routes, and it has to differ."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk = _current(admin).get_json()
    with at_terminal(TILL_PHONE):
        phone = _current(admin).get_json()
    assert desk['terminal_id'] == TILL_DESK, desk
    assert phone['terminal_id'] == TILL_PHONE, phone
    assert desk['terminal_id'] != phone['terminal_id']
    # And the short label is derived, not echoed: it must be a readable
    # fragment of the id rather than the whole thing.
    assert desk['terminal_short'] == '1234', desk
    assert phone['terminal_short'] == '5678', phone


# ─────────────────────────────────────────────────────────────────────────────
# FIX A: a brand-new shop must be able to close its very FIRST drawer
# ─────────────────────────────────────────────────────────────────────────────
#
# REPRODUCED: `_cash_session_report` (retail_api.py) reads `payments.
# direction` / `payments.related_type`. Those columns exist only once the
# LAZY `_ensure_credit_schema()` migration has run -- and nothing on the
# drawer read/close path used to call it. On a fresh install `payments` is
# created with id/company_id/sale_id/method/amount/reference/status/
# idempotency_key/created_at/uid -- no `direction` -- so the very first
# shift a brand-new shop ever tries to close threw
# sqlite3.OperationalError('no such column: p.direction'): an UNCAUGHT raw
# 500 from GET .../x-report (no try/except existed there at all), and a raw
# SQL string in the JSON 'message' field from POST .../close. The one
# promise Phase 4 makes -- a cashier can always end their shift -- was
# broken on day one of every install.

def test_a_brand_new_shop_can_close_its_first_drawer_with_nothing_rung():
    """MUST RUN BEFORE ANY OTHER TEST IN THIS FILE SELLS ANYTHING.

    `_ensure_credit_schema` is idempotent, gated by a process-global flag
    (`retail_api._CREDIT_SCHEMA_READY`), and its ALTER TABLEs persist in the
    ON-DISK database once they have run once. `create_sale` already calls it
    unconditionally, so the moment any OTHER test in this file rings a sale,
    `payments.direction` exists for the rest of this pytest process
    regardless of whether the fix below is present -- and this test would
    then pass even with the fix reverted, having proved nothing. It is
    placed here, immediately after the two-terminal harness and before
    section 1 (the first test to call `_sell_cash`), for exactly that
    reason -- see the module docstring's failure-shape #2: a fixture (or
    here, a test's POSITION in the file) that has already reached the state
    a bug needs is a fixture that manufactures the bug away.
    """
    assert retail_api._CREDIT_SCHEMA_READY is False, (
        'some earlier test in this file already extended the payments schema (sold '
        'something), so this test is no longer running against a genuinely fresh '
        'database and cannot prove the fix. Move it back before the first sale.')
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 50.0).get_json()['data']['id']

        # The X report: must be 200 with honest zeros, not an uncaught 500.
        report = _x(admin, sid)
        assert report.status_code == 200, report.get_data(as_text=True)
        data = report.get_json()['data']
        assert data['cash_sales'] == 0.0, data
        assert data['cash_refunds'] == 0.0, data
        assert data['expected_cash'] == 50.0, data

        # The close: must succeed, not 500 with a raw SQL string in the body.
        closed = _end(admin, sid, 50.0)
        assert closed.status_code == 200, closed.get_data(as_text=True)
        body = closed.get_json()['data']
        assert body['session']['status'] == 'closed', body     # admin holds approve
        assert body['report']['expected_cash'] == 50.0, body
        assert body['session']['variance'] == 0.0, body

    # ANTI-VACUITY: the schema really was extended by the routes above, so
    # the zeros above are "nothing was rung", not "the query silently
    # returned nothing because it errored".
    conn = get_retail_conn()
    try:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(payments)').fetchall()}
    finally:
        conn.close()
    assert {'direction', 'related_type'} <= cols, (
        'the drawer routes above never actually extended the payments schema, so this '
        f'test cannot tell "fixed" apart from "never asked": {sorted(cols)}')


# ─────────────────────────────────────────────────────────────────────────────
# 1. THE BUG: one terminal's takings never enter another's Z report
# ─────────────────────────────────────────────────────────────────────────────

def test_a_sale_rung_on_another_terminal_never_enters_this_drawers_z_report():
    """THE regression this phase exists for, end to end through the routes.

    Both halves are asserted, and that is the point: `cash_sales == 0` on the
    desktop would be satisfied by a fixture that never sold anything, so the
    claim is the exact pair of figures, that they differ, and that neither is
    zero."""
    admin, cid, pid = _new_shop()

    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 100.0).get_json()['data']['id']
        assert _sell_cash(admin, pid, 100.0).status_code == 200
    with at_terminal(TILL_PHONE):
        phone_sid = _open(admin, 20.0).get_json()['data']['id']
        assert _sell_cash(admin, pid, 250.0).status_code == 200

    assert desk_sid != phone_sid, 'the two terminals shared one drawer'

    with at_terminal(TILL_DESK):
        desk_report = _x(admin, desk_sid).get_json()['data']
    with at_terminal(TILL_PHONE):
        phone_report = _x(admin, phone_sid).get_json()['data']

    # NOT "the desktop is zero". The desktop is exactly its own takings and
    # the phone is exactly its own.
    assert desk_report['cash_sales'] == 100.0, desk_report
    assert phone_report['cash_sales'] == 250.0, phone_report
    assert desk_report['cash_sales'] != phone_report['cash_sales']
    assert desk_report['cash_sales'] > 0 and phone_report['cash_sales'] > 0

    # Expected cash follows: float + own sales, and nobody else's.
    assert desk_report['expected_cash'] == 200.0, desk_report
    assert phone_report['expected_cash'] == 270.0, phone_report

    # And neither drawer is contaminated, which is the OTHER half of the
    # disclosure being meaningful -- see the legacy shop below, where it is
    # deliberately non-zero.
    assert desk_report['foreign_terminal_sales'] == 0, desk_report
    assert phone_report['foreign_terminal_sales'] == 0, phone_report


def test_the_stamp_lookup_itself_is_terminal_scoped_not_merely_the_report():
    """THE CHECK, not the outcome.

    The route-level test above would also pass if the report filtered by
    terminal at READ time while the stamp stayed branch-wide -- a fix that
    looks identical from outside and loses the attribution forever, because a
    sale would still be carrying the wrong session id in the database. So this
    calls the stamp helper directly and asserts it resolves per terminal, with
    the OTHER terminal's session deliberately the most recently opened one
    (the old query's `ORDER BY opened_at DESC LIMIT 1` would return it)."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 10.0).get_json()['data']['id']
    with at_terminal(TILL_PHONE):
        phone_sid = _open(admin, 10.0).get_json()['data']['id']   # newest

    conn = get_retail_conn()
    try:
        bid = conn.execute('SELECT branch_id FROM cash_sessions WHERE id=?',
                           (desk_sid,)).fetchone()['branch_id']
        assert retail_api._open_cash_session_id(conn, cid, bid, terminal=TILL_DESK) == desk_sid
        assert retail_api._open_cash_session_id(conn, cid, bid, terminal=TILL_PHONE) == phone_sid
        # A terminal that has opened nothing gets None -- not "the branch's
        # drawer", which is the whole defect.
        assert retail_api._open_cash_session_id(
            conn, cid, bid, terminal='cccccccc-3333-4aaa-9999-cccccccccccc') is None
    finally:
        conn.close()


def test_a_sale_on_a_terminal_with_no_drawer_is_unattributed_not_somebody_elses():
    """The honest answer to "no drawer open here" is NULL.

    Falling back to the branch's drawer is what produced the fold, and it is
    the tempting shape: it makes every sale attributable and every attribution
    wrong."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 0.0).get_json()['data']['id']
    with at_terminal(TILL_PHONE):
        sale = _sell_cash(admin, pid, 75.0)
        assert sale.status_code == 200, sale.get_json()
        sale_id = sale.get_json()['data']['id']

    conn = get_retail_conn()
    try:
        row = conn.execute('SELECT session_id FROM sales WHERE id=?', (sale_id,)).fetchone()
    finally:
        conn.close()
    assert row['session_id'] is None, (
        'a sale rung on a till with no open drawer was attributed to another '
        f"terminal's session: {row['session_id']!r}")

    with at_terminal(TILL_DESK):
        desk_report = _x(admin, desk_sid).get_json()['data']
    assert desk_report['cash_sales'] == 0.0, desk_report
    # ANTI-VACUITY: the sale really happened and really was cash, so the zero
    # above is isolation rather than an empty shop.
    conn = get_retail_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE company_id=? AND method='cash' AND direction='in'",
            (cid,)).fetchone()[0]
    finally:
        conn.close()
    assert total >= 1, 'the fixture never recorded a cash payment at all'


def test_an_unidentified_terminal_still_finds_its_own_drawer():
    """The null-safe predicate, which is a one-character bug away.

    `terminal_id = ?` bound to None matches NOTHING in SQL, including the rows
    this very device wrote. An install with no local device identity would
    open a drawer, fail to find it on the next request, and open another --
    and `current` would report no drawer while sales stamped into one. The
    product uses `IS`, and this is what proves it."""
    admin, cid, pid = _new_shop()
    with at_terminal(None):
        opened = _open(admin, 30.0)
        assert opened.status_code == 200, opened.get_json()
        sid = opened.get_json()['data']['id']

        current = _current(admin).get_json()
        assert current['data'] is not None, 'an unidentified till lost its own drawer'
        assert current['data']['id'] == sid
        assert current['terminal_id'] is None
        assert current['terminal_short'] is None
        # It is ITS OWN drawer, so the screen may treat it as such...
        assert current['data']['is_this_terminal'] is True
        # ...and a second open on the same unidentified till is still refused.
        assert _open(admin, 40.0).status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 2. EVERY cash-session route, read off the live url_map
# ─────────────────────────────────────────────────────────────────────────────
#
# A hand-written list of routes has exactly one failure mode: the product
# grows a route and the list does not. So the list is derived from what Flask
# will actually serve, and this file declares a scoping rule for each -- a new
# cash-session route is UNACCOUNTED until somebody writes down whether it is
# terminal-scoped, and that decision lands in a diff.

#: endpoint function -> how it scopes. Exhaustive against the live url_map.
DRAWER_ROUTE_SCOPE = {
    # WRITES: own terminal only, and no capability widens that.
    'open_cash_session': 'write-own-terminal',
    'create_cash_movement': 'write-own-terminal',
    'close_cash_session': 'write-own-terminal',
    # READS: own terminal freely; another terminal needs reports/approve.
    'current_cash_session': 'read-own-terminal',
    'list_cash_sessions': 'read-own-terminal',
    'get_cash_session': 'read-widened-by-authority',
    'cash_session_x_report': 'read-widened-by-authority',
    # APPROVAL is deliberately NOT terminal-scoped: the whole point is that
    # somebody who is not standing at the till accepts what the till recorded.
    'approve_cash_variance': 'any-terminal-by-design',
}


def _live_cash_session_endpoints():
    found = {}
    for rule in app.url_map.iter_rules():
        path = str(rule.rule)
        if '/cash-sessions' not in path:
            continue
        found[rule.endpoint.rsplit('.', 1)[-1]] = path
    return found


def test_every_live_cash_session_route_has_a_declared_scope():
    live = _live_cash_session_endpoints()
    assert len(live) >= 8, f'the url_map scrape found only {live!r}; it is broken'
    undeclared = sorted(set(live) - set(DRAWER_ROUTE_SCOPE))
    stale = sorted(set(DRAWER_ROUTE_SCOPE) - set(live))
    assert not undeclared, (
        'these live cash-session routes have no declared terminal scope. Every one of '
        'them can read or write a drawer, and a route nobody scoped is a route that '
        'scopes to the branch -- which is the defect this phase exists to close:\n  '
        + '\n  '.join(f'{f} ({live[f]})' for f in undeclared))
    assert not stale, f'declared but no longer live: {stale}'


# FIX C -- REPRODUCED: replacing `_terminal_owns` with `return True` (handing
# every device the right to file a movement on, and close, any other till's
# live drawer) used to produce `1 failed, 24 passed`. Only the single test
# below noticed, and it noticed via TWO sequential asserts in one function --
# `mv.status_code == 403` then `closed.status_code == 403` -- so the moment
# the FIRST assert failed (movements wrongly allowed), pytest raised right
# there and the SECOND assert (close) never even ran. One assertion in one
# test was the entire margin between shipped behaviour and the total
# collapse of the money boundary Phase 4 exists to create, and it was not
# even proving both halves of itself.
#
# Enumerated from the LIVE url_map rather than hand-listed, so a write route
# added later is a NEW case here instead of a silent gap: every route under
# /cash-sessions that both (a) WRITES -- POST/PUT/PATCH/DELETE -- and
# (b) acts on an EXISTING session (its path carries `<session_id>`) is a
# candidate. `open_cash_session` has no `<session_id>` (it CREATES the
# session, so there is nothing existing for another terminal to own yet) and
# is correctly never a candidate. `approve_cash_variance` IS a candidate by
# that test, but `DRAWER_ROUTE_SCOPE` -- already proved EXHAUSTIVE against
# this same url_map by `test_every_live_cash_session_route_has_a_declared_
# scope` above -- declares it 'any-terminal-by-design' (see
# `test_approval_is_not_terminal_scoped_and_that_is_the_point`): accepting a
# variance is precisely the act of somebody NOT standing at the till, so it
# is excluded by reading that one declaration rather than by a second,
# independent hand-list that could drift from it.
_SESSION_WRITE_ROUTES = sorted({
    (rule.endpoint.rsplit('.', 1)[-1], str(rule.rule))
    for rule in app.url_map.iter_rules()
    if '/cash-sessions' in str(rule.rule)
    and 'session_id' in rule.arguments
    and ((rule.methods or set()) - {'HEAD', 'OPTIONS', 'GET'})
})

_MUST_REFUSE_FOREIGN_TERMINAL = sorted(
    (endpoint, path) for endpoint, path in _SESSION_WRITE_ROUTES
    if DRAWER_ROUTE_SCOPE.get(endpoint) == 'write-own-terminal')

#: How to actually CALL each own-terminal write route. A route landing in
#: _MUST_REFUSE_FOREIGN_TERMINAL with no body here fails LOUDLY at collection
#: time (the assert right below) instead of being silently skipped by the
#: parametrization -- which is exactly the "unaccounted new route" failure
#: mode `test_every_live_cash_session_route_has_a_declared_scope` already
#: guards for at the DRAWER_ROUTE_SCOPE layer; this is the same guard one
#: layer down, where the request body lives.
_WRITE_ROUTE_BODY = {
    'create_cash_movement': {'type': 'float_in', 'amount': 5.0},
    'close_cash_session': {'closing_float_counted': 55.0},
}
assert {e for e, _ in _MUST_REFUSE_FOREIGN_TERMINAL} <= set(_WRITE_ROUTE_BODY), (
    'the live url_map enumeration found an own-terminal cash-session write route this '
    'file does not know how to call: '
    f'{sorted({e for e, _ in _MUST_REFUSE_FOREIGN_TERMINAL} - set(_WRITE_ROUTE_BODY))}')


def test_the_write_route_enumeration_found_a_non_trivial_number_of_routes():
    """ANTI-VACUITY for the parametrized test below: a broken enumeration
    that found zero routes would make every one of its parametrized cases
    vacuously pass (there would be none to run), and an empty parametrize
    list reads as green in a pytest summary exactly like a real pass does."""
    assert len(_MUST_REFUSE_FOREIGN_TERMINAL) >= 2, _MUST_REFUSE_FOREIGN_TERMINAL


def test_the_only_write_route_exempt_from_the_terminal_sweep_is_approval():
    """THE ESCAPE HATCH, PINNED.

    `_MUST_REFUSE_FOREIGN_TERMINAL` keeps only the write routes DRAWER_ROUTE_
    SCOPE labels 'write-own-terminal', so any other label silently removes a
    route from the parametrized sweep above. `test_every_live_cash_session_
    route_has_a_declared_scope` does not close that: it proves a scope string
    EXISTS for every live route, never that the string is defensible. A future
    write route typed 'any-terminal-by-design' -- the one label that means
    "any device may write this drawer" -- would therefore drop out of the
    money boundary with a green suite and no diff to argue with.

    So the single exemption that exists today is named. `approve_cash_variance`
    is exempt for a stated reason (accepting a variance is precisely the act of
    somebody NOT standing at the till) and has its own proof in
    test_approval_is_not_terminal_scoped_and_that_is_the_point. A SECOND
    exemption has to edit this line and say why, which is the same discipline
    test_no_role_but_the_owner_holds_both_closing_and_approval applies to
    AUDIT-032's own one named exemption.
    """
    exempt = {e for e, _ in _SESSION_WRITE_ROUTES} - {e for e, _ in _MUST_REFUSE_FOREIGN_TERMINAL}
    assert exempt == {'approve_cash_variance'}, (
        'a cash-session WRITE route is exempt from the foreign-terminal sweep above and '
        'nobody has justified it here. Either it is terminal-scoped (declare it '
        "'write-own-terminal' in DRAWER_ROUTE_SCOPE and give it a body in "
        '_WRITE_ROUTE_BODY), or it genuinely may be written from any device -- in which '
        f'case say so on this line and prove it with its own test: {sorted(exempt)}')


@pytest.mark.parametrize('endpoint,path', _MUST_REFUSE_FOREIGN_TERMINAL,
                         ids=[e for e, _ in _MUST_REFUSE_FOREIGN_TERMINAL])
def test_the_write_routes_refuse_another_terminals_drawer(endpoint, path):
    """And they refuse it for EVERYBODY -- there is no authority that lets a
    device record cash movement on a till it is not standing at.

    PARAMETRIZED, one pytest case per live own-terminal write route, so a
    guard broken for ONE route fails on its OWN, by name, instead of one
    shared assert list silently only proving whichever route happens to sit
    first (see the FIX C note above the enumeration)."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 50.0).get_json()['data']['id']

    url = path.replace('<session_id>', desk_sid)
    with at_terminal(TILL_PHONE):
        # The admin bypass passes every capability check in the product, so
        # if anything could widen a write, this call would be it.
        refused = admin.post(url, json=_WRITE_ROUTE_BODY[endpoint])
        assert refused.status_code == 403, (endpoint, refused.get_json())

    # ...and the SAME call from the OWNING terminal succeeds, which is what
    # makes the 403 above evidence of scoping rather than of a route that
    # refuses everyone.
    with at_terminal(TILL_DESK):
        allowed = admin.post(url, json=_WRITE_ROUTE_BODY[endpoint])
        assert allowed.status_code == 200, (endpoint, allowed.get_json())


def test_reading_another_terminals_drawer_needs_the_authority_to_read_reports():
    """Your own till's drawer is yours; another till's is a report.

    Proven as a CONTRAST, because "the cashier gets 403" alone would hold for
    a route that 403s everybody."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 60.0).get_json()['data']['id']
        assert _sell_cash(admin, pid, 100.0).status_code == 200

    cashier, _ = _make_user('cashier', cid)
    manager, _ = _make_user('manager', cid)

    with at_terminal(TILL_PHONE):
        # A cashier holds retail.cash.close, so the decorator lets them in;
        # the terminal scope is what stops them.
        refused = cashier.get(f'{API}/cash-sessions/{desk_sid}/x-report')
        assert refused.status_code == 403, refused.get_json()
        assert refused.get_json()['message'] == retail_api.FOREIGN_DRAWER_MESSAGE

        refused_one = cashier.get(f'{API}/cash-sessions/{desk_sid}')
        assert refused_one.status_code == 403, refused_one.get_json()

        # A manager holds retail.reports, so another till's numbers ARE
        # readable to them -- that is what "another terminal's drawer is a
        # report" means.
        allowed = manager.get(f'{API}/cash-sessions/{desk_sid}/x-report')
        assert allowed.status_code == 200, allowed.get_json()
        assert allowed.get_json()['data']['cash_sales'] == 100.0

    # And the cashier CAN read a drawer on their own terminal, so the 403s
    # above are about whose drawer it is and not about who is asking.
    with at_terminal(TILL_PHONE):
        own_sid = _open(cashier, 5.0).get_json()['data']['id']
        own = cashier.get(f'{API}/cash-sessions/{own_sid}/x-report')
        assert own.status_code == 200, own.get_json()


def test_the_read_widening_tracks_the_live_capability_verdict(monkeypatch):
    """MUTATION PROOF for the widening itself.

    The contrast test above shows a manager reading and a cashier refused, but
    those are two different accounts and a hundred things differ between them.
    This holds the account fixed and flips ONLY what the capability check
    answers, so the outcome can be attributed to the check rather than to the
    role."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 15.0).get_json()['data']['id']
    cashier, _ = _make_user('cashier', cid)

    verdicts = {'retail.reports': False, 'retail.cash.approve': False}
    consulted = []
    real = retail_api.session_has_capability

    def fake(code):
        consulted.append(code)
        if code in verdicts:
            return verdicts[code]
        return real(code)

    monkeypatch.setattr(retail_api, 'session_has_capability', fake)

    with at_terminal(TILL_PHONE):
        assert cashier.get(f'{API}/cash-sessions/{desk_sid}/x-report').status_code == 403
        assert 'retail.reports' in consulted, (
            'the widening decision never consulted retail.reports, so the 403 above '
            'proves nothing about which authority governs it')

        verdicts['retail.reports'] = True
        assert cashier.get(f'{API}/cash-sessions/{desk_sid}/x-report').status_code == 200

        verdicts['retail.reports'] = False
        verdicts['retail.cash.approve'] = True
        assert cashier.get(f'{API}/cash-sessions/{desk_sid}/x-report').status_code == 200, (
            'somebody who may ACCEPT a variance must be able to look at it')


def test_the_session_list_scopes_to_this_terminal_until_authority_widens_it():
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        desk_sid = _open(admin, 11.0).get_json()['data']['id']
    with at_terminal(TILL_PHONE):
        phone_sid = _open(admin, 22.0).get_json()['data']['id']

    cashier, _ = _make_user('cashier', cid)
    with at_terminal(TILL_PHONE):
        listed = cashier.get(f'{API}/cash-sessions').get_json()
        ids = {s['id'] for s in listed['data']}
        assert listed['scope'] == 'this_terminal', listed
        assert phone_sid in ids, 'the cashier cannot see the drawer on their own till'
        assert desk_sid not in ids, "the cashier is reading another till's drawer history"
        assert listed['may_approve'] is False

        wide = admin.get(f'{API}/cash-sessions').get_json()
        wide_ids = {s['id'] for s in wide['data']}
        assert wide['scope'] == 'all_terminals', wide
        assert {desk_sid, phone_sid} <= wide_ids
        assert wide['may_approve'] is True

    # Every row says which till it is, in both directions, or the widened list
    # is a pile of indistinguishable drawers.
    by_id = {s['id']: s for s in wide['data']}
    assert by_id[phone_sid]['terminal_short'] == '5678'
    assert by_id[desk_sid]['terminal_short'] == '1234'
    assert by_id[desk_sid]['is_this_terminal'] is False
    assert by_id[phone_sid]['is_this_terminal'] is True


def test_opening_a_drawer_on_one_terminal_leaves_the_other_terminal_free():
    """The rule that had to change from one-per-BRANCH to one-per-TERMINAL.

    Under the old rule the second till was refused a drawer of its own, which
    is exactly why it ended up selling into the first one's."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        first = _open(admin, 10.0)
        assert first.status_code == 200, first.get_json()
        again = _open(admin, 20.0)
        assert again.status_code == 409, 'one drawer per terminal is not enforced'
        assert 'terminal' in again.get_json()['message'].lower()
    with at_terminal(TILL_PHONE):
        second = _open(admin, 30.0)
        assert second.status_code == 200, second.get_json()

    with at_terminal(TILL_DESK):
        assert _current(admin).get_json()['data']['opening_float'] == 10.0
        assert _current(admin).get_json()['other_terminals_open'] == 1
    with at_terminal(TILL_PHONE):
        assert _current(admin).get_json()['data']['opening_float'] == 30.0


def test_current_reports_no_drawer_here_while_naming_the_tills_that_have_one():
    """"No drawer on this till" and "the shop is shut" are different facts,
    and a cashier about to sell into nothing needs the difference."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        _open(admin, 10.0)
    with at_terminal(TILL_PHONE):
        body = _current(admin).get_json()
    assert body['data'] is None
    assert body['other_terminals_open'] == 1, body

    admin2, cid2, pid2 = _new_shop()
    with at_terminal(TILL_PHONE):
        quiet = _current(admin2).get_json()
    assert quiet['data'] is None
    assert quiet['other_terminals_open'] == 0, quiet


# ─────────────────────────────────────────────────────────────────────────────
# 3. The ENDED / CLOSED split, and AUDIT-032
# ─────────────────────────────────────────────────────────────────────────────

def test_a_cashier_can_always_end_a_short_shift():
    """The requirement that outranks every other rule here: counting short
    must never trap somebody at the till."""
    admin, cid, pid = _new_shop()
    cashier, cashier_uid = _make_user('cashier', cid)
    with at_terminal(TILL_PHONE):
        sid = _open(cashier, 100.0).get_json()['data']['id']
        assert _sell_cash(cashier, pid, 100.0).status_code == 200
        # Expected 200, counted 160 -- forty dollars missing.
        ended = _end(cashier, sid, 160.0)
        assert ended.status_code == 200, ended.get_json()
        body = ended.get_json()['data']
        assert body['session']['status'] == 'ended', body
        assert body['session']['variance'] == -40.0, body
        assert body['session']['variance_status'] == 'unverified', body
        assert body['report']['self_approved'] is False
        # The drawer really is out of service.
        assert _current(cashier).get_json()['data'] is None
        assert cashier.post(f'{API}/cash-sessions/{sid}/movements',
                            json={'type': 'float_in', 'amount': 1.0}).status_code == 409


def test_the_cashier_who_counted_short_cannot_sign_it_off():
    """AUDIT-032. The mechanism is the capability split, so this asserts the
    refusal AND that somebody who holds the code succeeds on the same row --
    otherwise a route that 403s everyone would pass."""
    admin, cid, pid = _new_shop()
    cashier, _ = _make_user('cashier', cid)
    manager, _ = _make_user('manager', cid)
    with at_terminal(TILL_PHONE):
        sid = _open(cashier, 100.0).get_json()['data']['id']
        assert _end(cashier, sid, 60.0).status_code == 200

        refused = _approve(cashier, sid)
        assert refused.status_code == 403, refused.get_json()
        # A MANAGER is refused too, and that is the sharp end of AUDIT-032:
        # the code is withheld from every role that can close a drawer.
        refused_mgr = _approve(manager, sid)
        assert refused_mgr.status_code == 403, refused_mgr.get_json()

        accepted = _approve(admin, sid)
        assert accepted.status_code == 200, accepted.get_json()
        assert accepted.get_json()['data']['session']['status'] == 'closed'
        assert accepted.get_json()['data']['session']['variance_status'] == 'approved'
        assert accepted.get_json()['data']['self_approved'] is False


def test_no_role_but_the_owner_holds_both_closing_and_approval():
    """Read off the product's own seeding table rather than arranged here.

    The behavioural tests above show the outcome for two roles; this shows the
    PROPERTY holds for every role the product defines, including any added
    later.

    THE OWNER IS THE ONE EXEMPTION AND IT IS NOT A LOOPHOLE. ROLE_ADMIN is the
    shop owner, who holds every code by definition; in a one-person shop they
    are the only authority there is, and a rule that refused them would leave
    that shop with a drawer it could never close. AUDIT-032 is about a role
    that can count a drawer WITHOUT being the authority over it -- a manager,
    a senior cashier -- and the exemption is named here rather than silently
    filtered so that adding a second such role has to argue with this line."""
    both = {user_accounts.CAP_CASH_CLOSE, user_accounts.CAP_CASH_APPROVE}
    holders = {role for role, caps in user_accounts.ROLE_CAPABILITIES.items()
               if both <= set(caps)}
    assert holders == {user_accounts.ROLE_ADMIN}, (
        f'{sorted(holders)} can close a drawer AND accept its variance. Only the shop '
        'owner may -- every other such role can count its own drawer and sign off its '
        'own shortfall, which is AUDIT-032')
    # ANTI-VACUITY, in both directions: a role that can close and cannot
    # approve must actually exist, or the property above is true of a world
    # where nobody closes anything.
    closers = {role for role, caps in user_accounts.ROLE_CAPABILITIES.items()
               if user_accounts.CAP_CASH_CLOSE in caps}
    approvers = {role for role, caps in user_accounts.ROLE_CAPABILITIES.items()
                 if user_accounts.CAP_CASH_APPROVE in caps}
    assert closers - approvers, 'no role can close a drawer without being able to approve it'
    assert approvers == {user_accounts.ROLE_ADMIN}, sorted(approvers)
    assert user_accounts.ROLE_MANAGER in closers - approvers, (
        'the manager -- the role AUDIT-032 is actually about -- no longer closes drawers')


def test_the_ended_or_closed_outcome_tracks_the_live_approval_verdict(monkeypatch):
    """MUTATION PROOF for the split.

    One account, one route, one row shape -- only the capability answer moves.
    If the status were decided by role, by variance size, or by nothing at all,
    these two halves could not differ."""
    admin, cid, pid = _new_shop()
    verdict = {'value': False}
    real = retail_api.session_has_capability

    def fake(code):
        if code == user_accounts.CAP_CASH_APPROVE:
            return verdict['value']
        return real(code)

    monkeypatch.setattr(retail_api, 'session_has_capability', fake)

    with at_terminal(TILL_DESK):
        sid = _open(admin, 10.0).get_json()['data']['id']
        ended = _end(admin, sid, 10.0).get_json()['data']
        assert ended['session']['status'] == 'ended', ended
        assert ended['session']['variance'] == 0.0, (
            'a zero variance must still be UNVERIFIED -- "nothing to approve" is '
            'a judgement somebody with the authority makes, not one the arithmetic '
            'makes for them')
        assert ended['session']['variance_status'] == 'unverified'

    verdict['value'] = True
    with at_terminal(TILL_PHONE):
        sid2 = _open(admin, 10.0).get_json()['data']['id']
        closed = _end(admin, sid2, 10.0).get_json()['data']
        assert closed['session']['status'] == 'closed', closed
        assert closed['session']['variance_status'] == 'approved'
        assert closed['report']['self_approved'] is True


def test_approving_an_already_closed_or_still_open_drawer_is_refused():
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 10.0).get_json()['data']['id']
        still_open = _approve(admin, sid)
        assert still_open.status_code == 409, still_open.get_json()
        _end(admin, sid, 10.0)                      # admin -> straight to closed
        twice = _approve(admin, sid)
        assert twice.status_code == 409, twice.get_json()


def test_approval_is_not_terminal_scoped_and_that_is_the_point():
    admin, cid, pid = _new_shop()
    cashier, _ = _make_user('cashier', cid)
    with at_terminal(TILL_DESK):
        sid = _open(cashier, 100.0).get_json()['data']['id']
        assert _end(cashier, sid, 90.0).status_code == 200
    # The owner is at a different device entirely -- which is the ordinary
    # case, and the reason approval must not require standing at the till.
    with at_terminal(TILL_PHONE):
        ok = _approve(admin, sid)
        assert ok.status_code == 200, ok.get_json()
    # Company scoping still applies in full.
    other_admin, other_cid, other_pid = _new_shop()
    with at_terminal(TILL_PHONE):
        cross = _approve(other_admin, sid)
        assert cross.status_code == 404, cross.get_json()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Force-closed: an UNKNOWN variance is not a variance of zero
# ─────────────────────────────────────────────────────────────────────────────

def _force_close(session_id):
    """Put a drawer into the shape v16's force-end leaves behind.

    Written through SQL because the force-end is the MIGRATION's act, not a
    route -- and every column here is copied from `_v16_force_end` in
    database/schema.py rather than invented: status ENDED, `ended_at` stamped,
    `ended_by` the System sentinel, an `unverified_*` reason on the row, and
    `closing_float_counted` / `closing_float_expected` / `variance` /
    `closed_at` / `closed_by` all left NULL. Those absences are the fixture:
    a force-ended drawer that carried a variance figure would be describing a
    count that never happened, and this test would then be asserting against a
    world the migration cannot produce.
    """
    conn = get_retail_conn()
    try:
        conn.execute(
            'UPDATE cash_sessions SET status=?, ended_at=?, ended_by=?, ended_reason=? '
            'WHERE id=?',
            (CASH_SESSION_STATUS_ENDED, '2026-01-01 23:59:59', V16_ENDED_BY_SYSTEM,
             V16_ENDED_REASON_STALE, session_id))
        conn.commit()
    finally:
        conn.close()


def test_a_force_closed_drawer_reports_no_variance_rather_than_a_variance_of_zero():
    """0.00 is the number a shop reads as "that drawer was fine". A drawer
    nobody counted must not be able to produce it."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 100.0).get_json()['data']['id']
        assert _sell_cash(admin, pid, 100.0).status_code == 200
    _force_close(sid)

    with at_terminal(TILL_DESK):
        sess = admin.get(f'{API}/cash-sessions/{sid}').get_json()['data']['session']
    assert sess['force_closed'] is True, sess
    assert sess['variance_status'] == 'not_counted', sess
    assert sess['variance'] is None, sess
    assert sess['closing_float_counted'] is None, sess
    # WHO ended it and WHY both reach the wire. `ended_by` is the migration's
    # non-person sentinel rather than a name, which is the whole reason
    # schema.py uses one: the column a reader looks at to find out who did
    # this must not name somebody who did not.
    assert sess['ended_by'] == V16_ENDED_BY_SYSTEM, sess
    assert sess['ended_reason'] in V16_UNVERIFIED_END_REASONS, sess
    # The close trail stays empty, which is what keeps ENDED distinguishable
    # from CLOSED on a row the migration touched.
    assert sess['approved_at'] is None and sess['approved_by'] is None, sess


def test_a_counted_drawer_that_balanced_is_not_confused_with_an_uncounted_one():
    """THE PAIR THAT KEEPS THE GUARD HONEST.

    "variance is falsy" is true of both `None` and `0.0`, so a guard written
    that way would call a perfectly balanced drawer uncounted and an uncounted
    drawer balanced -- and either mistake reads as the other on the screen."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 100.0).get_json()['data']['id']
        _end(admin, sid, 100.0)
        sess = admin.get(f'{API}/cash-sessions/{sid}').get_json()['data']['session']
    assert sess['variance'] == 0.0, sess
    assert sess['variance'] is not None
    assert sess['force_closed'] is False, sess
    assert sess['variance_status'] == 'approved', sess   # admin holds approve
    assert sess['closing_float_counted'] == 100.0


def test_accepting_an_uncounted_drawer_has_to_be_said_out_loud():
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 100.0).get_json()['data']['id']
    _force_close(sid)

    with at_terminal(TILL_PHONE):
        blind = _approve(admin, sid)
        assert blind.status_code == 409, blind.get_json()
        assert blind.get_json()['data']['requires'] == 'acknowledge_uncounted'
        # Still not counted, still not accepted.
        assert admin.get(f'{API}/cash-sessions/{sid}').get_json()[
            'data']['session']['status'] == 'ended'

        deliberate = _approve(admin, sid, {'acknowledge_uncounted': True})
        assert deliberate.status_code == 200, deliberate.get_json()
        after = deliberate.get_json()['data']['session']
        assert after['status'] == 'closed'
        # Accepted, and STILL not counted. Acceptance does not invent a number.
        assert after['variance'] is None, after
        assert after['variance_status'] == 'not_counted', after


# ─────────────────────────────────────────────────────────────────────────────
# 5. A SHOP THAT HAS BEEN TRADING FOR TWO YEARS
# ─────────────────────────────────────────────────────────────────────────────
#
# The Phase 3 lesson, applied here: a change is not finished when its own
# tests pass. It is finished when a database that looks like a real shop's
# goes through it and comes out with its money unchanged.
#
# Phase 4 changes a WRITE path (which session a new sale is stamped with) and
# adds READ fields. Neither may move a single historical figure. So this
# fixture replays the OLD branch-wide stamp -- two terminals' sales landing in
# one session, which is the genuine contamination, not a description of it --
# then asserts every legacy drawer's arithmetic against an independent
# calculation over the raw tables.

def _build_legacy_shop():
    """Twenty-four months of trading, stamped the way the product used to.

    Sales are rung from BOTH terminals while a single branch-wide drawer is
    open, and the session id is written the old way: whatever session was open
    on the branch. That is what every multi-device install's history actually
    looks like.
    """
    # price=100.0 so `_sell_cash`'s amount-to-quantity arithmetic makes the
    # amount paid the amount TAKEN. A cheaper unit price would leave change in
    # the drawer figures and turn every assertion below into a puzzle about
    # the fixture rather than a statement about the product.
    admin, cid, pid = _new_shop(price=100.0)
    sessions = []
    for month in range(24):
        with at_terminal(TILL_DESK):
            sid = _open(admin, 100.0 + month).get_json()['data']['id']
        # The old stamp: every sale on this branch, from either device, lands
        # in the one open session.
        for terminal, amount in ((TILL_DESK, 40.0), (TILL_PHONE, 60.0), (TILL_DESK, 30.0)):
            with at_terminal(terminal):
                sale = _sell_cash(admin, pid, amount)
                assert sale.status_code == 200, sale.get_json()
                sale_id = sale.get_json()['data']['id']
            conn = get_retail_conn()
            try:
                conn.execute('UPDATE sales SET session_id=? WHERE id=?', (sid, sale_id))
                conn.commit()
            finally:
                conn.close()
        with at_terminal(TILL_DESK):
            admin.post(f'{API}/cash-sessions/{sid}/movements',
                       json={'type': 'paid_out', 'amount': 5.0, 'reason': 'driver'})
            ended = _end(admin, sid, 100.0 + month + 125.0)
            assert ended.status_code == 200, ended.get_json()
        sessions.append(sid)
    return admin, cid, pid, sessions


def _independent_expected_cash(cid, sid):
    """The X/Z arithmetic, recomputed from the raw tables by this test.

    Deliberately NOT a call into `_cash_session_report`: comparing a function
    against itself proves that it is deterministic and nothing else."""
    conn = get_retail_conn()
    try:
        sess = conn.execute('SELECT * FROM cash_sessions WHERE id=?', (sid,)).fetchone()
        cash_sales = conn.execute("""
            SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN sales s ON p.sale_id = s.id
            WHERE p.company_id=? AND s.session_id=? AND p.direction='in'
              AND p.method='cash' AND p.related_type='sale'
              AND COALESCE(p.status,'active')='active'
        """, (cid, sid)).fetchone()[0]
        refunds = conn.execute("""
            SELECT COALESCE(SUM(refund_amount),0) FROM returns
            WHERE company_id=? AND session_id=? AND refund_method='cash' AND status='completed'
        """, (cid, sid)).fetchone()[0]
        moves = dict(conn.execute("""
            SELECT type, COALESCE(SUM(amount),0) FROM cash_movements
            WHERE session_id=? GROUP BY type
        """, (sid,)).fetchall() or [])
        expected = (float(sess['opening_float'] or 0) + cash_sales - refunds
                    + moves.get('float_in', 0) - moves.get('float_out', 0)
                    + moves.get('paid_in', 0) - moves.get('paid_out', 0))
        return {
            'cash_sales': round(cash_sales, 2),
            'expected_cash': round(expected, 2),
            'counted': sess['closing_float_counted'],
            'variance': sess['variance'],
        }
    finally:
        conn.close()


def test_two_years_of_legacy_drawers_come_through_with_their_money_unchanged():
    admin, cid, pid, sessions = _build_legacy_shop()
    assert len(sessions) == 24

    for sid in sessions:
        truth = _independent_expected_cash(cid, sid)
        with at_terminal(TILL_DESK):
            report = _x(admin, sid).get_json()['data']
            sess = admin.get(f'{API}/cash-sessions/{sid}').get_json()['data']['session']
        assert report['cash_sales'] == truth['cash_sales'], (sid, report, truth)
        assert report['expected_cash'] == truth['expected_cash'], (sid, report, truth)
        assert sess['closing_float_counted'] == truth['counted'], (sid, sess, truth)
        assert sess['variance'] == truth['variance'], (sid, sess, truth)

    # ANTI-VACUITY: the shop has real money in it, so "unchanged" is a claim
    # about figures rather than about a row of zeroes.
    first = _independent_expected_cash(cid, sessions[0])
    assert first['cash_sales'] == 130.0, first
    assert first['expected_cash'] == 225.0, first


def test_the_legacy_contamination_is_disclosed_rather_than_silently_totalled():
    """The honest half of the phase.

    The going-forward fold is fixed; the rows already stamped the old way
    cannot be unstamped, and this shop's drawers really do contain another
    till's cash. The report says so, with a count, so nobody reads a legacy Z
    report as clean because the code that produced it was patched."""
    admin, cid, pid, sessions = _build_legacy_shop()
    with at_terminal(TILL_DESK):
        report = _x(admin, sessions[0]).get_json()['data']
    # One of the three sales in each legacy session was rung on the phone.
    assert report['foreign_terminal_sales'] == 1, report
    assert report['unattributed_sales'] == 0, report
    assert report['terminal_short'] == '1234', report

    # ...and a drawer with no foreign sales reports zero, so the field is a
    # measurement rather than a constant.
    clean_admin, clean_cid, clean_pid = _new_shop()
    with at_terminal(TILL_DESK):
        clean_sid = _open(clean_admin, 10.0).get_json()['data']['id']
        _sell_cash(clean_admin, clean_pid, 50.0)
        clean = _x(clean_admin, clean_sid).get_json()['data']
    assert clean['foreign_terminal_sales'] == 0, clean


def test_a_sale_from_before_terminals_existed_is_unattributed_not_foreign():
    """A NULL terminal on an old sale is evidence of nothing.

    Counting it as another till's would put a permanent, false contamination
    warning on the drawer of every shop that has any history at all -- and a
    warning that is always on is a warning nobody reads."""
    admin, cid, pid = _new_shop()
    with at_terminal(TILL_DESK):
        sid = _open(admin, 10.0).get_json()['data']['id']
        sale_id = _sell_cash(admin, pid, 50.0).get_json()['data']['id']
    conn = get_retail_conn()
    try:
        conn.execute('UPDATE sales SET terminal_id=NULL WHERE id=?', (sale_id,))
        conn.commit()
    finally:
        conn.close()
    with at_terminal(TILL_DESK):
        report = _x(admin, sid).get_json()['data']
    assert report['unattributed_sales'] == 1, report
    assert report['foreign_terminal_sales'] == 0, report
    # The money is still counted -- disclosure never drops a row.
    assert report['cash_sales'] == 50.0, report
