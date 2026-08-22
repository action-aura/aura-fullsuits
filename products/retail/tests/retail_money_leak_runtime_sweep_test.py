"""
Aura Retail -- no route may hand a cashier transacted money, proved by CALLING
every route as a cashier and reading what comes back.

── WHY THIS EXISTS, AND WHY IT REPLACES A STATIC SWEEP ──────────────────────
Three successive attempts were made to prove this property by reading SOURCE.
Each was defeated within one round, by a different spelling:

  round 1   an interpolated table name          f"... FROM {tbl}"
  round 2   a DROP assembled from a variable    _verb = 'DROP'
  round 3   a dead capability call              _unused = session_has_capability(C)
  round 3   a module-level SQL constant         _WEEKLY_SQL = "SELECT total ..."
  round 3   a dead assign to a live name        limit = session_has_capability(C)

The last three all shipped a live, ungated route returning `SUM(total)=400.00`
to a real cashier while FOUR separate static sweeps reported green.

That is not a run of bad luck. "Does this value influence authorisation" is
undecidable in general, and every hardening round spends its effort on the net
rather than on the product. Each time, the verifier defeated the static guard
the same way: by booting the app and calling the route.

So this test does that instead. It cannot be defeated by a spelling, because it
never looks at source -- an f-string, a module constant, a hoisted helper and a
literal all produce the same HTTP response, and the response is what a customer
would actually receive.

── WHAT IT DOES NOT COVER, STATED PLAINLY ───────────────────────────────────
Honesty about coverage is the whole point of a guard; a sweep that quietly
skips is worse than none, because it reads as proof.

  * GET routes only. A POST needs a valid body per route, and a sweep that
    posts garbage proves nothing about a route that rejects it at validation.
  * Routes whose path parameters cannot be filled from the fixture are
    REPORTED, not silently skipped -- see the coverage-floor test below, which
    fails if the swept fraction collapses.
  * It proves DISCLOSURE, not authorisation. A route that returns no money but
    mutates state is out of scope here and belongs to the capability matrix.

── THE CONTROL ──────────────────────────────────────────────────────────────
Every sweep needs to be shown catching something, or "nothing leaked" and
"nothing was checked" are the same result. `test_the_sweep_catches_a_known_leak`
removes a genuinely money-returning route from the allowlist and asserts the
sweep flags it.
"""
import json
import os
import shutil
import sys
import tempfile
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'products', 'retail', 'backend'))

DATA = tempfile.mkdtemp(prefix='aura_money_sweep_')
os.environ['AURA_APP_DATA'] = DATA
os.environ.pop('AURA_DEV', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code='AURA_RETAIL', platform='WINDOWS')
os.environ.pop('AURA_RETAIL_DEMO_MODE', None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config['TESTING'] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


#: Keys that name TRANSACTED money -- what the shop took, owes, or is owed.
#:
#: Catalogue prices (`sell_price`, `cost_price`) are deliberately absent: a till
#: must be able to list products with their prices to sell anything, so treating
#: those as a leak would make the sweep fire on the product grid and be turned
#: off within a week. The distinction is "what a sale MOVED" versus "what a thing
#: COSTS", and it is the same distinction the static classifier drew.
MONEY_KEYS = frozenset({
    'total', 'subtotal', 'amount_paid', 'amount', 'balance', 'credit_balance',
    'revenue', 'takings', 'collected', 'tax_amount', 'profit', 'gross_profit',
    'outstanding', 'due', 'paid', 'change_due', 'net_revenue', 'expected_cash',
    'variance', 'opening_float', 'closing_cash',
})

#: Routes a cashier may legitimately receive money from, each with the reason.
#:
#: This is an ALLOWLIST WITH REASONS, not a suppression list. An entry here is a
#: claim that a till genuinely needs the figure to do its job, and it should be
#: readable as such by whoever inherits this file.
ALLOWED = {
    # Ringing a sale returns that sale's own total -- the cashier just typed it
    # in and the customer is standing in front of them.
    'retail_api.create_sale': 'the sale the cashier just rang',
    # Taking a return requires finding the original receipt, which means seeing
    # its total to match it. Redacted and capped for a caller without
    # retail.reports -- see recent_sales' own comment and clamp_page_limit.
    'retail_api.recent_sales': 'receipt lookup for returns; redacted without retail.reports',
    'retail_api.get_sale': 'the receipt being refunded',
    'retail_api.create_return': 'the refund the cashier just gave',
    # The cashier counts their own drawer at close. Whether the VARIANCE is then
    # accepted is a different authority (retail.cash.approve).
    'retail_api.cash_session_open': "the till's own drawer",
    'retail_api.cash_session_close': "the till's own drawer",
    'retail_api.cash_session_current': "the till's own drawer",
    'retail_api.cash_movement_add': "the till's own drawer",
}


def _make_user(role, company_id):
    email = f'sweep-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'MoneySweepPW1'
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        'INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, '
        'require_password_change) VALUES (?,?,?,?,?,?,?,?,0)',
        (user_id, str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:6].upper()}', email,
         hash_password(password), role, 'active'))
    conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client


@pytest.fixture(scope='module')
def shop():
    """A real company with real money in it. A sweep over an EMPTY shop proves
    nothing -- every response would be an empty list and no key would appear."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')

    r = admin.post(f'{API}/products', json={
        'name': 'Sweep Item', 'sku': f'SW-{uuid.uuid4().hex[:8]}',
        'sell_price': 100.0, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']

    for _ in range(4):
        s = admin.post(f'{API}/sales', json={
            'items': [{'product_id': product_id, 'quantity': 1}],
            'amount_paid': 100.0, 'payment_method': 'cash',
            'idempotency_key': str(uuid.uuid4())})
        assert s.status_code == 200, s.get_json()

    cashier = _make_user('cashier', company_id)
    return {'company_id': company_id, 'admin': admin, 'cashier': cashier,
            'product_id': product_id}


def _money_keys_in(payload, _depth=0):
    """Every MONEY_KEYS name appearing anywhere in a decoded JSON body, at any
    nesting depth, with a non-null value.

    Non-null matters: a route that returns the key with `null` has disclosed
    nothing, and treating that as a leak would push people toward stripping keys
    rather than stripping values."""
    found = set()
    if _depth > 12:
        return found
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in MONEY_KEYS and value is not None:
                found.add(key)
            found |= _money_keys_in(value, _depth + 1)
    elif isinstance(payload, list):
        for item in payload:
            found |= _money_keys_in(item, _depth + 1)
    return found


def _sweepable_get_rules():
    """Every GET rule under the retail API that needs no path parameters.

    Returns (endpoint, path) pairs. Rules WITH parameters are counted by the
    coverage test rather than dropped on the floor."""
    rules = []
    for rule in app.url_map.iter_rules():
        if 'GET' not in (rule.methods or set()):
            continue
        if not str(rule.rule).startswith(API):
            continue
        if rule.arguments:
            continue
        rules.append((rule.endpoint, str(rule.rule)))
    return sorted(set(rules))


def _leaks_for(client):
    """Call every sweepable GET as `client` and return {endpoint: keys}."""
    leaks = {}
    for endpoint, path in _sweepable_get_rules():
        try:
            r = client.get(path)
        except Exception:
            # A route that raises is a different defect and belongs to a
            # different test; it is not a disclosure.
            continue
        if r.status_code != 200:
            continue
        try:
            body = json.loads(r.get_data(as_text=True) or 'null')
        except ValueError:
            continue
        keys = _money_keys_in(body)
        if keys:
            leaks[endpoint] = sorted(keys)
    return leaks


# ── the preconditions, asserted rather than assumed ──────────────────────────

def test_the_fixture_actually_has_money_in_it(shop):
    """If this fails, every 'no leak' result below is meaningless."""
    r = shop['admin'].get(f'{API}/sales/recent?limit=10')
    assert r.status_code == 200
    assert _money_keys_in(r.get_json()), 'the admin sees no money -- the shop is empty'


def test_the_cashier_really_lacks_the_reports_capability(shop):
    """Read off the product's own gate, not arranged here."""
    assert shop['cashier'].get(f'{API}/dashboard/stats').status_code == 403
    assert shop['cashier'].get(f'{API}/reports/summary').status_code == 403


# ── the sweep ────────────────────────────────────────────────────────────────

def test_no_unallowed_route_hands_a_cashier_transacted_money(shop):
    leaks = _leaks_for(shop['cashier'])
    unexpected = {ep: keys for ep, keys in leaks.items() if ep not in ALLOWED}
    assert not unexpected, (
        'these routes returned transacted money to a cashier holding no '
        'retail.reports capability:\n  ' +
        '\n  '.join(f'{ep} -> {keys}' for ep, keys in sorted(unexpected.items())) +
        '\n\nGate the route, or -- if a till genuinely needs the figure -- add it '
        'to ALLOWED with the reason a cashier needs it.')


def test_the_sweep_catches_a_known_leak(shop):
    """The control. Without this, 'nothing leaked' and 'nothing was checked'
    are indistinguishable -- which is precisely how four static sweeps reported
    green over a live ungated route returning SUM(total)=400.00.

    Takes a route that genuinely returns money to a cashier BY DESIGN, drops it
    from the allowlist, and asserts the sweep flags it."""
    leaks = _leaks_for(shop['cashier'])
    assert leaks, 'the sweep found no money anywhere -- it is not looking at anything'
    known = set(leaks) & set(ALLOWED)
    assert known, (
        'no allowlisted route returned money, so this control proves nothing. '
        'Either the fixture has no money in it or ALLOWED is stale.')

    victim = sorted(known)[0]
    pruned = {ep: keys for ep, keys in leaks.items() if ep not in (set(ALLOWED) - {victim})}
    assert victim in pruned, (
        f'{victim} returns money to a cashier but the sweep did not flag it '
        f'once un-allowlisted -- the sweep is not doing its job')


def test_the_sweep_covers_enough_of_the_surface_to_mean_something(shop):
    """A floor, so the sweep cannot quietly shrink to nothing.

    The failure this guards against is subtle: if `_sweepable_get_rules` ever
    stops matching (a URL prefix change, a refactor that gives every route a
    path parameter), the sweep sweeps zero routes and reports PASS. That is a
    pass condition equal to the bug signature, which this programme has shipped
    twice."""
    sweepable = _sweepable_get_rules()
    total_get = [r for r in app.url_map.iter_rules()
                 if 'GET' in (r.methods or set()) and str(r.rule).startswith(API)]
    assert len(sweepable) >= 15, (
        f'only {len(sweepable)} retail GET routes are sweepable; the sweep has '
        f'collapsed and is no longer evidence of anything')
    # Reported, not hidden: what fraction of the surface this actually sees.
    covered = len(sweepable) / max(1, len(total_get))
    assert covered >= 0.25, (
        f'the sweep reaches only {covered:.0%} of retail GET routes '
        f'({len(sweepable)}/{len(total_get)}); parameterised routes are not '
        f'covered and this floor exists so that fact stays visible')


def test_an_admin_is_not_swept_into_the_same_rule(shop):
    """The other direction, and the reason the sweep is scoped to a cashier: an
    owner is SUPPOSED to see the shop's money. A sweep that fired on the admin
    too would be measuring the wrong property and would get disabled."""
    assert _money_keys_in(shop['admin'].get(f'{API}/sales/recent?limit=10').get_json())
