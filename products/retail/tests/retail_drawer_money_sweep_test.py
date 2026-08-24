"""Aura Retail -- no route may hand anybody ANOTHER TILL's drawer money,
proved by calling every drawer route against a shop whose drawers are full.

── WHY THIS FILE EXISTS ALONGSIDE THE EXISTING RUNTIME SWEEP ───────────────
retail_money_leak_runtime_sweep_test.py already boots the app and calls every
parameterless GET as a cashier, looking for transacted money. It is the right
technique and it could not see this hole, for a reason worth writing down
because it is one of the failure shapes this programme keeps shipping:

    ITS FIXTURE NEVER OPENS A CASH DRAWER.

`GET /cash-sessions` and `GET /cash-sessions/current` carried no capability
gate at all, and both return rows carrying `opening_float` and `variance` --
two of that sweep's own MONEY_KEYS. Every response it ever measured was
`null` or `[]`, no money key was there to find, and four green sweeps meant
"there was nothing in the drawer", not "the drawer is not disclosed". A
fixture that manufactured the state that hid the bug.

So this file sweeps the same surface with the drawers FULL, and with the
extra axis the original sweep has no concept of: WHICH TERMINAL is asking.
The parameterised routes (`/cash-sessions/<id>/...`) are swept too, with the
id filled in from the fixture, because those are precisely the ones a
parameterless sweep drops on the floor.

── THE CONTROL ─────────────────────────────────────────────────────────────
A sweep that has never been shown catching anything cannot distinguish
"nothing leaked" from "nothing was checked".
`test_the_sweep_catches_a_deliberately_reopened_leak` disables the terminal
scope in the product and asserts this file goes red.

Run:
    pytest products/retail/tests/retail_drawer_money_sweep_test.py -v
"""
import json
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

DATA = Path(tempfile.mkdtemp(prefix='aura_retail_drawer_sweep_'))
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
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

TILL_A = '11110000-1111-4111-8111-1111aaaa0001'
TILL_B = '22220000-2222-4222-8222-2222bbbb0002'

#: Every key on a cash_sessions row or an X/Z report that names money the
#: drawer MOVED. A superset of the original sweep's drawer-relevant keys,
#: because this file can see report bodies the original never reaches.
DRAWER_MONEY_KEYS = frozenset({
    'opening_float', 'variance', 'expected_cash', 'cash_sales', 'cash_refunds',
    'closing_float_counted', 'closing_float_expected', 'amount',
})


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


class at_terminal:
    """Run as though this process were `terminal_id` -- see
    retail_drawer_terminal_scope_test.py for the full rationale."""

    def __init__(self, terminal_id):
        self.terminal_id = terminal_id
        self.previous = None

    def __enter__(self):
        self.previous = retail_api.local_terminal_id
        retail_api.local_terminal_id = lambda: self.terminal_id
        return self

    def __exit__(self, *exc):
        retail_api.local_terminal_id = self.previous
        return False


def _make_user(role, company_id, capabilities=None):
    """Create a logged-in test account, optionally with individual capability
    codes overridden away from the role default.

    FIXED (real AUDIT gap, reproduced): this used to `UPDATE user_capabilities
    SET access_level=? WHERE user_id=? AND capability_code=?` -- a table that
    exists NOWHERE in this repo. The real store is `user_permissions(id,
    user_id, subsystem, access_level)` (commercial_runtime/identity/
    registry_db.py), and despite the column being named `subsystem` it is
    where `user_accounts.seed_capabilities_for_user` writes one row PER
    CAPABILITY CODE (e.g. 'retail.cash.close') -- `user_has_capability` /
    `session_has_capability` read it back the same way. Because the broken
    line only ran when `capabilities` was non-empty, it fired on exactly the
    tests meant to prove a cashier CANNOT read another till's takings, so
    those cases raised `sqlite3.OperationalError: no such table:
    user_capabilities` instead of testing anything -- and because `conn.close()`
    below was not in a `finally`, that raise leaked the registry connection
    while it still held the write lock, so the next three tests in the file
    each blocked for the registry's write-lock timeout and died with
    "database is locked" -- including the one test whose entire job is to
    prove the sweep can catch a leak.
    """
    email = f'sweep-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'DrawerSweepPW1'
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
    return client


def _apply_terminal_index():
    """Install the v16 open-drawer index shape so two terminals can hold two
    drawers at once. Scaffolding for a migration owned elsewhere -- the same
    stand-in retail_drawer_terminal_scope_test.py documents in full."""
    conn = get_retail_conn()
    try:
        names = {row[1] for row in conn.execute("PRAGMA index_list('cash_sessions')").fetchall()}
        if 'idx_cash_sessions_one_open_per_terminal' in names:
            return
        conn.execute('DROP INDEX IF EXISTS idx_cash_sessions_one_open_per_branch')
        conn.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_cash_sessions_one_open_per_terminal '
            "ON cash_sessions(company_id, terminal_id) WHERE status='open'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope='module')
def shop():
    """A shop whose drawers HAVE MONEY IN THEM -- the precondition the
    original sweep's fixture is missing, and the whole reason for this file."""
    _apply_terminal_index()
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    r = admin.post(f'{API}/products', json={
        'name': 'Sweep Widget', 'sku': f'SW-{uuid.uuid4().hex[:8]}',
        'sell_price': 100.0, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']

    def sell(client, amount):
        return client.post(f'{API}/sales', json={
            'items': [{'product_id': pid, 'quantity': amount / 100.0}],
            'amount_paid': amount, 'payment_method': 'cash',
            'idempotency_key': str(uuid.uuid4())})

    with at_terminal(TILL_A):
        a_sid = admin.post(f'{API}/cash-sessions/open',
                           json={'opening_float': 150.0}).get_json()['data']['id']
        assert sell(admin, 400.0).status_code == 200
        assert admin.post(f'{API}/cash-sessions/{a_sid}/movements',
                          json={'type': 'paid_out', 'amount': 25.0}).status_code == 200

    with at_terminal(TILL_B):
        b_sid = admin.post(f'{API}/cash-sessions/open',
                           json={'opening_float': 75.0}).get_json()['data']['id']
        assert sell(admin, 300.0).status_code == 200
        # An ENDED drawer with an unverified shortfall -- the row an owner is
        # meant to look at, and exactly the row a cashier must not read off
        # another till.
        assert admin.post(f'{API}/cash-sessions/{b_sid}/close',
                          json={'closing_float_counted': 300.0}).status_code == 200

    return {'company_id': company_id, 'admin': admin, 'a_sid': a_sid, 'b_sid': b_sid, 'pid': pid}


def _entitled_session_ids(company_id):
    """Every session id the CURRENT caller is entitled to see IN FULL, right
    now -- derived from the caller's own terminal, not from whichever single
    session happens to be under probe.

    This is `_session_read_scope`'s own-terminal branch, replayed here rather
    than re-imagined: "your OWN till's drawer is yours" is not "the one
    session id you asked about is yours" -- `list_cash_sessions` widens an
    own-terminal read to that terminal's WHOLE history, past shifts included,
    whoever staffed them (see test_the_session_list_does_not_hand_a_cashier_
    every_tills_takings and the terminal-scope file's own version of the same
    assertion). A checker keyed only on the probed session id cannot tell
    that legitimate widening apart from a DIFFERENT till's money leaking in
    beside it -- which is exactly the gap this function closes: querying the
    real table for the real terminal, instead of trusting a single id.

    Reads `retail_api._this_terminal()` -- not a terminal id passed in -- so
    this keeps working under `at_terminal`, which patches
    `retail_api.local_terminal_id` and is read by `_this_terminal()` at call
    time.

    Deliberately NOT widened for retail.reports / retail.cash.approve (the
    other half of `_session_read_scope`'s rule). Every test in this file that
    holds one of those capabilities already probes a session that belongs to
    its own terminal, so own-terminal-only entitlement is never over-strict
    for what this file actually exercises; adding capability-awareness here
    would be modelling a rule this sweep does not yet have a case that needs.
    """
    terminal = retail_api._this_terminal()
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM cash_sessions WHERE company_id=? AND terminal_id IS ?",
            (company_id, terminal)
        ).fetchall()
    finally:
        conn.close()
    return frozenset(row['id'] for row in rows)


def _money_keys_in(payload, session_id, entitled_ids, keys=DRAWER_MONEY_KEYS,
                   _depth=0, _owner=None):
    """Every money key present with a NON-NULL value, at any depth, that is
    either the session under probe or NOT some other session the caller is
    legitimately entitled to see.

    Non-null matters for the same reason the original sweep says it does: a
    route that returns the key as `null` has disclosed nothing, and treating
    that as a leak pushes people toward stripping keys rather than values.

    Two things now decide whether a subtree counts, not one:

      `session_id`     the ONE session this probe is about. Its own money
                        always counts -- that is the whole point of probing
                        it, own-drawer or deliberately-injected-foreign alike
                        (`test_the_fixture_really_has_money_in_the_drawers`
                        and the reopened-leak control both rely on this).

      `entitled_ids`    (see `_entitled_session_ids` above) every OTHER
                        session id the caller's own terminal legitimately
                        owns -- e.g. `list_cash_sessions`'s own-terminal
                        history, which surfaces past shifts on THIS till
                        that are not the session under probe and are not a
                        leak of anything.

    A subtree is skipped (excluded from `found`) only when its owner is
    SOME OTHER session (`owner != session_id`) that IS in `entitled_ids` --
    the caller's own legitimate history. FIXED: this used to skip on any
    owner mismatch against `session_id` alone, which happened to also
    exclude legitimate own-terminal history (Path 2: `2_owned_by_probed`
    stayed correct) but, for exactly the same reason, ALSO excluded money
    nested under a genuinely FOREIGN till's session id (Paths 3-5:
    `3_owned_by_other_till`, `4_list_of_other_tills`,
    `5_other_till_nested_under_current`) -- a foreign session id is,
    definitionally, "a string other than the probed session", so the old
    rule could never tell "your own other shift" apart from "someone else's
    drawer". Checking `entitled_ids` is what tells those apart: a foreign
    till's session id is not in that set, so its subtree is NOT skipped and
    its money is flagged; the caller's own other shifts ARE in that set, so
    they stay excluded exactly as before. Money with NO owning id at all is
    unaffected either way -- which is deliberately what keeps this the same
    sweep the module docstring's two injections (a bare `expected_cash`, and
    the SUM of other terminals' `opening_float`, both added straight onto
    `/cash-sessions/current`'s UNOWNED top-level body) still have to be
    caught by. `session_id` is checked before `id` on purpose: a
    `cash_movements` row carries BOTH its own `id` (the movement's, never
    equal to any session id) and the `session_id` it belongs to, and keying
    off `id` first would misread every movement as ownerless and flag it
    regardless of whose drawer it is in.
    """
    found = set()
    if _depth > 12:
        return found
    if isinstance(payload, dict):
        raw_owner = payload.get('session_id', payload.get('id'))
        owner = raw_owner if isinstance(raw_owner, str) else _owner
        if owner is not None and owner != session_id and owner in entitled_ids:
            return found
        for key, value in payload.items():
            if key in keys and value is not None:
                found.add(key)
            found |= _money_keys_in(value, session_id, entitled_ids, keys, _depth + 1, owner)
    elif isinstance(payload, list):
        for item in payload:
            found |= _money_keys_in(item, session_id, entitled_ids, keys, _depth + 1, _owner)
    return found


def _drawer_get_rules(session_id):
    """Every GET Flask will serve under /cash-sessions, with `<session_id>`
    filled in from the fixture.

    The parameterised ones are the point. A sweep that skips any rule with an
    argument skips `/cash-sessions/<id>/x-report`, which is the single richest
    money disclosure in this route family."""
    rules = []
    for rule in app.url_map.iter_rules():
        if 'GET' not in (rule.methods or set()):
            continue
        path = str(rule.rule)
        if '/cash-sessions' not in path:
            continue
        if set(rule.arguments) - {'session_id'}:
            # Reported rather than silently skipped -- see the coverage test.
            rules.append((rule.endpoint.rsplit('.', 1)[-1], path, None))
            continue
        rules.append((rule.endpoint.rsplit('.', 1)[-1], path,
                      path.replace('<session_id>', session_id)))
    return sorted(rules)


def _leaks_for(client, session_id, company_id):
    """{endpoint: [money keys]} for every drawer GET this client can reach.

    `company_id` is new: it is how `_entitled_session_ids` looks up the
    caller's own-terminal history from the real table, rather than trusting
    the single `session_id` under probe to stand in for everything the
    caller may legitimately see."""
    entitled_ids = _entitled_session_ids(company_id)
    leaks = {}
    for endpoint, _pattern, url in _drawer_get_rules(session_id):
        if url is None:
            continue
        r = client.get(url)
        if r.status_code != 200:
            continue
        try:
            body = json.loads(r.get_data(as_text=True) or 'null')
        except ValueError:
            continue
        keys = _money_keys_in(body, session_id, entitled_ids)
        if keys:
            leaks[endpoint] = sorted(keys)
    return leaks


# ── The preconditions, asserted rather than assumed ──────────────────────────

def test_the_sweep_actually_found_the_drawer_routes(shop):
    rules = _drawer_get_rules(shop['a_sid'])
    fillable = [r for r in rules if r[2] is not None]
    assert len(rules) >= 4, rules
    assert len(fillable) == len(rules), (
        'a drawer GET has a path parameter this sweep cannot fill, so it is being '
        f'skipped rather than checked: {[r for r in rules if r[2] is None]}')
    paths = {r[1] for r in rules}
    assert f'{API}/cash-sessions/<session_id>/x-report' in paths, paths


def test_the_fixture_really_has_money_in_the_drawers(shop):
    """If this fails, every "no leak" result below means nothing -- which is
    precisely what went wrong with the sweep this file supplements."""
    with at_terminal(TILL_A):
        owner_view = _leaks_for(shop['admin'], shop['a_sid'], shop['company_id'])
    assert owner_view, 'the owner sees no drawer money -- the drawers are empty'
    assert 'cash_session_x_report' in owner_view, owner_view
    assert 'expected_cash' in owner_view['cash_session_x_report'], owner_view


# ── The sweeps ───────────────────────────────────────────────────────────────

def test_an_account_without_the_drawer_capability_reads_no_drawer_money(shop):
    """The gate the drawer GETs did not have.

    `retail.cash.close` switched off, `retail.reports` switched off: this
    account can still sell, and must see nothing of any drawer."""
    stripped = _make_user('cashier', shop['company_id'],
                          capabilities={'retail.cash.close': 'none'})
    with at_terminal(TILL_A):
        leaks = _leaks_for(stripped, shop['a_sid'], shop['company_id'])
    assert leaks == {}, (
        'these drawer routes disclosed transacted money to an account holding '
        f'neither retail.cash.close nor retail.reports: {leaks}')


def test_a_cashier_at_one_till_reads_no_money_from_the_other_tills_drawer(shop):
    """The axis the original sweep has no concept of.

    A cashier legitimately holds retail.cash.close -- they work a drawer --
    so the capability gate lets them through the door. What must stop them is
    that the drawer they are asking about is not theirs."""
    cashier = _make_user('cashier', shop['company_id'])
    with at_terminal(TILL_A):
        # Terminal A's own drawer: this cashier is standing at it, so the
        # money is theirs to read. Asserted so the empty result below is
        # isolation and not a route that refuses everyone.
        own = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'cash_session_x_report' in own, own

    with at_terminal(TILL_B):
        # Same account, same routes, different till. Terminal A's drawer is
        # now somebody else's.
        foreign = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    # FIXED (real AUDIT gap, reproduced): this used to assert only that two
    # NAMED endpoints ('cash_session_x_report', 'get_cash_session') were
    # absent from `foreign`, instead of asserting the whole leak map was
    # empty. Proved worthless on a copy of this file with defect 1 above
    # fixed (baseline: 7 passed): injecting a bare `'expected_cash': 9999.99`
    # into GET /cash-sessions/current, and separately the SUM of every OTHER
    # terminal's opening_float under the same key, both left this file
    # reporting "7 passed" -- the sweep saw the leaked key and threw it away
    # because it was checking two endpoint NAMES instead of the map itself.
    # Asserting `foreign == {}` makes the failure name EVERY offending
    # endpoint and key, not just the two this test happened to think of.
    assert foreign == {}, (
        'these drawer routes disclosed transacted money from TILL A to a cashier '
        f'standing at TILL B: {foreign}')


def test_the_session_list_does_not_hand_a_cashier_every_tills_takings(shop):
    """`GET /cash-sessions` returned every drawer in the company, with
    `opening_float` and `variance` on each row, to anybody who could log in."""
    cashier = _make_user('cashier', shop['company_id'])
    with at_terminal(TILL_A):
        body = cashier.get(f'{API}/cash-sessions').get_json()
    ids = {s['id'] for s in body['data']}
    assert shop['b_sid'] not in ids, (
        "a cashier at till A is being handed till B's ended drawer, including its "
        f'variance: {body}')
    assert shop['a_sid'] in ids, 'the cashier cannot see their own till, so the ' \
                                 'exclusion above proves nothing'

    owner = shop['admin']
    with at_terminal(TILL_A):
        wide = owner.get(f'{API}/cash-sessions').get_json()
    assert {shop['a_sid'], shop['b_sid']} <= {s['id'] for s in wide['data']}, wide


def test_an_anonymous_caller_gets_nothing_from_any_drawer_route(shop):
    anon = app.test_client()
    for _endpoint, _pattern, url in _drawer_get_rules(shop['a_sid']):
        if url is None:
            continue
        r = anon.get(url)
        assert r.status_code in (401, 403), (url, r.status_code, r.get_data(as_text=True))


# ── The control ──────────────────────────────────────────────────────────────

def test_the_sweep_catches_a_deliberately_reopened_leak(shop, monkeypatch):
    """Shows this file catching something.

    The terminal scope is switched off in the product -- exactly the state
    before Phase 4, where any holder of retail.cash.close could read any
    till's drawer by id -- and the cross-terminal sweep above must go red. If
    it stays green, it is measuring nothing and every other result in this
    file is worthless."""
    cashier = _make_user('cashier', shop['company_id'])

    monkeypatch.setattr(retail_api, '_session_read_scope',
                        lambda sess, this_terminal=retail_api._UNSET: (False, True))

    with at_terminal(TILL_B):
        foreign = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'cash_session_x_report' in foreign, (
        'the scope was disabled and the sweep still found no cross-terminal '
        'disclosure, so it is not actually measuring one')
    assert 'expected_cash' in foreign['cash_session_x_report'], foreign

    monkeypatch.undo()
    with at_terminal(TILL_B):
        restored = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'cash_session_x_report' not in restored, restored


# ── THE FIVE-PATH MATRIX (AUDIT follow-on) ────────────────────────────────────
#
# `_money_keys_in` used to decide "is this subtree somebody else's business"
# by ONE comparison: owner != session_id. That rule is right for Path 2 (the
# caller's own probed drawer) and, by accident, right-looking for the
# caller's own OTHER shifts on the same till (2b) -- but it is ALSO the exact
# rule that silently discarded Path 3: a genuinely FOREIGN till's session id
# is, definitionally, "a string other than the probed session", so the old
# code could not tell 2b and 3 apart. Path 1 (no owner at all) and the
# control (2) already had coverage; 2b, 3, 4, and 5 did not -- probed
# directly against the real function, the way the fix's own investigation
# probed it, rather than only through the full Flask round-trip below.

#: The caller's own terminal owns exactly these two sessions -- the one
#: under probe, and one other shift on the same till. Nothing else.
_PROBED_SID = 'aaaa0000-aaaa-4aaa-8aaa-aaaaaaaa0001'
_OWN_OTHER_SID = 'bbbb0000-bbbb-4bbb-8bbb-bbbbbbbb0002'
_FOREIGN_SID = 'cccc0000-cccc-4ccc-8ccc-cccccccc0003'
_ENTITLED = frozenset({_PROBED_SID, _OWN_OTHER_SID})


def test_the_five_path_matrix_of_owner_shapes():
    """Every owner shape `_money_keys_in` has to distinguish, in one place,
    run directly against the function rather than through a live server.

    2b is the legitimate-widening control: money owned by a DIFFERENT
    session than the one under probe, but one the caller's own terminal
    genuinely owns, must NOT be flagged -- this is what proves the fix has
    not over-corrected into flagging the caller's own drawer history. 3, 4,
    and 5 are the entitlement hole this file's fix closes: a foreign till's
    session id is just as much "not the probed session" as 2b is, and only
    checking it against the caller's real entitled set (rather than the
    single probed id) tells the two apart."""
    cases = {
        '1_unowned_bare': (
            {'expected_cash': 500.0, 'opening_float': 100.0},
            {'expected_cash', 'opening_float'}),
        '2_owned_by_probed': (
            {'id': _PROBED_SID, 'opening_float': 150.0, 'variance': -5.0},
            {'opening_float', 'variance'}),
        '2b_owned_by_callers_own_other_shift': (
            {'id': _OWN_OTHER_SID, 'opening_float': 75.0, 'variance': 2.0},
            set()),
        '3_owned_by_other_till': (
            {'id': _FOREIGN_SID, 'opening_float': 75.0, 'variance': 2.0},
            {'opening_float', 'variance'}),
        '4_list_of_other_tills': (
            {'other_terminals': [{'id': _FOREIGN_SID, 'expected_cash': 999.0}]},
            {'expected_cash'}),
        '5_other_till_nested_under_current': (
            {'id': _PROBED_SID, 'opening_float': 150.0,
             'related': {'session_id': _FOREIGN_SID, 'variance': 42.0}},
            {'opening_float', 'variance'}),
    }
    for label, (payload, expected) in cases.items():
        found = _money_keys_in(payload, _PROBED_SID, _ENTITLED)
        assert found == expected, (label, found, expected)


# ── THE SAME THREE SHAPES, END TO END THROUGH THE REAL /cash-sessions/current
#    ROUTE -- proving `_leaks_for`, not just `_money_keys_in` in isolation,
#    actually catches them when the product's own JSON is what carries them.
# ────────────────────────────────────────────────────────────────────────────

def _patch_current_cash_session_response(monkeypatch, mutate):
    """Wrap the REAL `/cash-sessions/current` view so its JSON body can be
    mutated after the real handler runs -- the same technique
    `test_the_sweep_catches_a_deliberately_reopened_leak` uses on
    `_session_read_scope`, aimed at the route's OUTPUT instead of its gate.
    `mutate(body) -> body` receives the parsed response body and returns the
    (possibly mutated) body to serve in its place."""
    endpoint = 'retail_api.current_cash_session'
    original = app.view_functions[endpoint]

    def wrapper(*args, **kwargs):
        resp = original(*args, **kwargs)
        body = json.loads(resp.get_data(as_text=True))
        resp.set_data(json.dumps(mutate(body)))
        return resp

    monkeypatch.setitem(app.view_functions, endpoint, wrapper)


def test_the_sweep_catches_an_other_terminals_array_on_current(shop, monkeypatch):
    """Mutation proof (Path 4), against the real route. `other_terminals_open`
    already exists as a COUNT on this exact response; a future `other_terminals`
    array of the terminals themselves is the obvious next step, and it must
    not get a free pass just because it arrived as a list instead of a
    single field.

    Compared against a BASELINE taken before the mutation, not against an
    empty dict: the caller's own current drawer legitimately carries
    `opening_float` on this same endpoint (that is Path 2, and it is
    supposed to show up -- see test_the_fixture_really_has_money_in_the_
    drawers), so "the mutation is gone" has to mean "back to what it was",
    not "reports nothing at all"."""
    cashier = _make_user('cashier', shop['company_id'])
    with at_terminal(TILL_A):
        baseline = _leaks_for(cashier, shop['a_sid'], shop['company_id'])

    def inject(body):
        body['other_terminals'] = [{
            'id': shop['b_sid'], 'expected_cash': 9999.99, 'opening_float': 111.11,
        }]
        return body

    _patch_current_cash_session_response(monkeypatch, inject)
    with at_terminal(TILL_A):
        leaked = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'current_cash_session' in leaked, (
        f'an other_terminals array naming a foreign till went undetected: {leaked}')
    assert {'expected_cash', 'opening_float'} <= set(leaked['current_cash_session']), leaked

    monkeypatch.undo()
    with at_terminal(TILL_A):
        clean = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert clean == baseline, (
        f'the mutation is gone but the route no longer matches its own baseline: '
        f'{clean} != {baseline}')


def test_the_sweep_catches_a_foreign_session_nested_under_current(shop, monkeypatch):
    """Mutation proof (Path 5), against the real route. The caller's OWN
    current-session object is legitimate to return in full -- this proves a
    FOREIGN session smuggled in as a nested field of that same object is not
    given the same pass merely for sharing a dict with something legitimate.

    Baseline-compared for the same reason as the test above."""
    cashier = _make_user('cashier', shop['company_id'])
    with at_terminal(TILL_A):
        baseline = _leaks_for(cashier, shop['a_sid'], shop['company_id'])

    def inject(body):
        if body.get('data'):
            body['data']['related_till'] = {
                'session_id': shop['b_sid'], 'variance': 42.42,
            }
        return body

    _patch_current_cash_session_response(monkeypatch, inject)
    with at_terminal(TILL_A):
        leaked = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'current_cash_session' in leaked, (
        f'a foreign session nested under the current-session object went undetected: {leaked}')
    assert 'variance' in leaked['current_cash_session'], leaked

    monkeypatch.undo()
    with at_terminal(TILL_A):
        clean = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert clean == baseline, (
        f'the mutation is gone but the route no longer matches its own baseline: '
        f'{clean} != {baseline}')


def test_the_sweep_catches_a_bare_unowned_figure_on_current(shop, monkeypatch):
    """Mutation proof (Path 1), against the real route -- the module
    docstring's own worked example, actually run rather than only narrated:
    a bare money figure with no owning id at all, dropped straight onto the
    top level of `/cash-sessions/current`'s body.

    Baseline-compared for the same reason as the two tests above."""
    cashier = _make_user('cashier', shop['company_id'])
    with at_terminal(TILL_A):
        baseline = _leaks_for(cashier, shop['a_sid'], shop['company_id'])

    def inject(body):
        body['expected_cash'] = 12345.0
        return body

    _patch_current_cash_session_response(monkeypatch, inject)
    with at_terminal(TILL_A):
        leaked = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert 'current_cash_session' in leaked, (
        f'a bare unowned expected_cash figure went undetected: {leaked}')
    assert 'expected_cash' in leaked['current_cash_session'], leaked

    monkeypatch.undo()
    with at_terminal(TILL_A):
        clean = _leaks_for(cashier, shop['a_sid'], shop['company_id'])
    assert clean == baseline, (
        f'the mutation is gone but the route no longer matches its own baseline: '
        f'{clean} != {baseline}')
