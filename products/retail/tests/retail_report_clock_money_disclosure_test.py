"""
Aura Retail -- GET /sales/recent must not hand a cashier the book the report
routes refuse them, and the sweep that is supposed to notice must stop reading
PATHS and start reading RESPONSES.

(FILENAME. The `retail_report_clock_` prefix is this wave's file-OWNERSHIP
convention, not the subject -- exactly as retail_report_clock_agreement_test.py
says of itself. The subject here is money disclosure. §2 belongs, on merit, in
retail_route_capability_matrix_test.py beside the sweep it widens; that file is
another agent's this wave, so the machinery lands here and the hand-over is
written out in this module's closing note.)

── THE DEFECT ───────────────────────────────────────────────────────────────
A cashier holds retail.sell, retail.refund, retail.cash.close and NOT
retail.reports (user_accounts.ROLE_CAPABILITIES). So:

    GET /dashboard/stats    -> 403
    GET /reports/summary    -> 403
    GET /sales/recent?limit=100000&date_from=2020-01-01&date_to=2030-01-01
                            -> 200, every sale the shop has ever rung,
                               `SELECT s.*` -- total, amount_paid, and
                               `actor_user_uid`, a registry identifier.

The gated routes hand back an AGGREGATE of that same money. Refusing the
summary while serving the rows it is computed from is not a policy, it is an
oversight: anyone refused `/reports/summary` can sum the book themselves.

── WHY THE STRUCTURAL SWEEP MISSED IT ───────────────────────────────────────
`retail_route_capability_matrix_test.py` classifies a route as money-disclosing
by looking for one of twelve words in its PATH -- 'report', 'dashboard',
'aging', 'statement'... `/sales/recent` contains none of them, so the sweep
that exists precisely to make this class of miss impossible never looked at it.

A path-vocabulary classifier can only catch routes somebody already named like
a report. §2 replaces the question "does the PATH say money?" with "does the
HANDLER return money?", read off the SQL each handler actually executes.

── WHAT THE FIX IS, AND WHY IT IS NOT "GATE THE ROUTE" ──────────────────────
`@mt_require_capability(CAP_REPORTS)` on this route would break the refund
counter. `subsystem-retail.js::_findSaleForReturn` -- the cashier's returns
flow -- fetches `/sales/recent?limit=200` and finds the receipt client-side,
and retail.refund is a CASHIER DEFAULT. Gating the whole route would refuse a
cashier the lookup for a refund the product explicitly grants them.

So the split follows the one this codebase already made for
`customer_statement` ("one customer's balance is a till fact, the list of
everyone who owes the shop money is a report"):

    a till may LOOK UP a sale;  the sales BOOK is a report.

Concretely, for a caller without retail.reports:
  * `date_from`/`date_to` -- an arbitrary historical range, which is the only
    way to reach sales older than the current page -- are REFUSED;
  * `limit` is capped at the till's own page size, so one request can no
    longer pull the entire book;
  * the registry identity columns (`actor_user_uid`, `uid`) are dropped, since
    no till surface reads them and they name a PERSON, not a sale.
A caller WITH retail.reports is unchanged in every respect.

Run:
    pytest products/retail/tests/retail_report_clock_money_disclosure_test.py -v
"""
import ast
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
# TESTS_DIR is inserted EXPLICITLY rather than relying on pytest's rootdir
# sys.path insertion: `retail_capability_ratchet_ast` is imported below, and an
# import that works only under one pytest invocation style is an import that
# breaks the day somebody runs the file directly.
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The capability-consumption analysis, kept in its own import-light module so
# it can be tested at the granularity of a single spelling without standing up
# a Flask app first -- see retail_capability_ratchet_consumption_test.py. It
# replaced a presence match in this file that an adversarial verifier defeated
# with one dead line; the full account is in that module's docstring.
import retail_capability_ratchet_ast as _ratchet  # noqa: E402

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_moneydisc_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.mt_auth import CAPABILITY_DENIED_MESSAGE  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity.user_accounts import CAP_REPORTS  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_user(role, company_id):
    """A real account seeded through the PRODUCTION capability helper, so the
    cashier below lacks retail.reports because the product says so and not
    because this file arranged it."""
    email = f"md-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "MoneyDiscPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, str(uuid.uuid4()), company_id, f"EMP-{uuid.uuid4().hex[:6].upper()}", email,
         hash_password(password), role, "active"))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client


@pytest.fixture(scope='module')
def shop():
    """One company; an admin who rings the sales, and a cashier and a manager
    arguing about the SAME rows."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')          # creates retail_settings / doc_sequences

    import random
    dconn = get_retail_conn()
    dconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
                  (company_id, random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()

    r = admin.post(f'{API}/products', json={
        'name': 'Disclosure Test Item', 'sku': f'MD-{uuid.uuid4().hex[:8]}',
        'sell_price': 100.0, 'tax_rate': 0, 'initial_stock': 500})
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']

    sale_numbers = []
    for _ in range(3):
        s = admin.post(f'{API}/sales', json={
            'items': [{'product_id': product_id, 'quantity': 1}],
            'amount_paid': 100.0, 'payment_method': 'cash',
            'idempotency_key': str(uuid.uuid4())})
        assert s.status_code == 200, s.get_json()
        sale_numbers.append(s.get_json()['data']['sale_number'])

    return {'company_id': company_id, 'admin': admin, 'product_id': product_id,
            'sale_numbers': sale_numbers,
            'cashier': _make_user('cashier', company_id),
            'manager': _make_user('manager', company_id)}


# ── 0. the preconditions, asserted rather than assumed ───────────────────────
#
# Every assertion in §1 is about the DIFFERENCE between a caller who holds
# retail.reports and one who does not. If the two turned out to hold the same
# codes -- or if the fixture's "cashier" were quietly an admin -- the whole
# section would pass while proving nothing.

def test_the_cashier_really_lacks_reports_and_the_manager_really_has_it(shop):
    """Read off the PRODUCT's own role defaults, not off this file."""
    from commercial_runtime.identity.user_accounts import capabilities_for_role
    assert CAP_REPORTS not in capabilities_for_role('cashier')
    assert CAP_REPORTS in capabilities_for_role('manager')


def test_the_cashier_is_genuinely_refused_the_report_surfaces(shop):
    """THE asymmetry this file exists to close, established first. If a cashier
    could read /reports/summary anyway, /sales/recent would be disclosing
    nothing the product had refused and there would be no defect here."""
    for path in ('/dashboard/stats', '/reports/summary?days=30'):
        r = shop['cashier'].get(f'{API}{path}')
        assert r.status_code == 403, (path, r.status_code, r.get_data(as_text=True))


def test_the_fixture_really_rang_sales_that_a_book_read_would_return(shop):
    """A shop with no sales returns `[]` from every query below, and `[]`
    satisfies "no money disclosed" for the worst possible reason."""
    rows = shop['admin'].get(f'{API}/sales/recent?limit=50').get_json()['data']
    assert len(rows) >= 3, rows
    assert all(float(r['total']) == 100.0 for r in rows[:3]), rows[:3]


# ── 1. the leak, closed -- and the till, still working ───────────────────────

def test_a_cashier_cannot_range_over_the_sales_book(shop):
    """The browse. `date_from`/`date_to` are the only way to reach sales older
    than the current page, which is what turns this route from a lookup into
    the ledger `/reports/summary` refuses."""
    r = shop['cashier'].get(f'{API}/sales/recent?date_from=2020-01-01&date_to=2030-01-01&limit=300')
    assert r.status_code == 403, (
        f"a cashier is refused /reports/summary but was served the sales book it is "
        f"computed from: {r.status_code} with "
        f"{len((r.get_json() or {}).get('data') or [])} rows")
    body = r.get_json()
    assert body.get('message') == CAPABILITY_DENIED_MESSAGE, (
        f"the refusal must be the catalogued capability message the frontend can "
        f"translate, not a bespoke string: {body!r}")


def test_each_date_bound_is_refused_on_its_own(shop):
    """Half a range is still a range: `date_from` alone reaches the whole book
    forwards, `date_to` alone reaches all of it backwards. A guard that only
    fired when BOTH were present would be trivially walked around."""
    for qs in ('date_from=2020-01-01', 'date_to=2030-01-01'):
        r = shop['cashier'].get(f'{API}/sales/recent?{qs}')
        assert r.status_code == 403, (qs, r.status_code, r.get_data(as_text=True))


#: Comfortably more sales than the till's page size, so "the cap held" and
#: "the shop is small" cannot look the same. Its OWN company, because the
#: module-scoped `shop` above is shared and burying its three known receipts
#: under 250 newer ones would break the refund-lookup test for a reason that
#: has nothing to do with what either test is about.
CROWDED_SHOP_SALES = 250


@pytest.fixture(scope='module')
def crowded_shop():
    """A company whose book is LONGER than the cap. Rows are written straight
    to SQLite rather than through 250 POSTs -- this fixture is about how many
    rows come BACK, and `create_sale`'s own behaviour is covered elsewhere."""
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')

    conn = get_retail_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = cur.lastrowid
    for n in range(CROWDED_SHOP_SALES):
        cur.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,subtotal,"
            "discount_amount,tax_amount,total,amount_paid,payment_method,status,created_at,"
            "actor_user_uid,uid) VALUES (?,?,?,NULL,'POS',100,0,0,100,100,'cash','completed',?,?,?)",
            (company_id, f'CROWD-{n:05d}', branch_id, f'2026-08-19 {n // 60:02d}:{n % 60:02d}:00',
             str(uuid.uuid4()), str(uuid.uuid4())))
    conn.commit()
    conn.close()
    return {'company_id': company_id, 'admin': admin,
            'cashier': _make_user('cashier', company_id),
            'manager': _make_user('manager', company_id)}


def test_the_crowded_shop_really_holds_more_sales_than_the_cap(crowded_shop):
    """Without this, the cap test below passes on a shop that simply has fewer
    sales than the cap -- which is the fixture manufacturing the exact state
    that hides the bug. Read back through a caller allowed the whole book."""
    rows = crowded_shop['manager'].get(f'{API}/sales/recent?limit=100000').get_json()['data']
    assert len(rows) == CROWDED_SHOP_SALES > retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT, (
        f"the book is {len(rows)} sales and the cap is "
        f"{retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT}; the cap test cannot tell "
        f"a working cap from a short book")


def test_a_cashier_cannot_pull_the_whole_book_with_a_huge_limit(crowded_shop):
    """The other way to the same place. `limit` was uncapped, so one request
    with a big enough number returned every sale in the company -- no date
    range needed, so refusing the range alone would not have closed it."""
    r = crowded_shop['cashier'].get(f'{API}/sales/recent?limit=100000')
    assert r.status_code == 200, r.get_data(as_text=True)
    rows = r.get_json()['data']
    assert len(rows) == retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT, (
        f"a cashier pulled {len(rows)} of {CROWDED_SHOP_SALES} sales in one request; "
        f"the till's page size is {retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT}")


def test_the_cap_is_keyed_on_the_capability_and_not_applied_to_everyone(crowded_shop):
    """The cap must be a REFUSAL of the book, not a new global page size --
    otherwise the Sales History screen and every reporting surface silently
    lose rows and this file has broken the product to protect it."""
    rows = crowded_shop['manager'].get(f'{API}/sales/recent?limit=100000').get_json()['data']
    assert len(rows) > retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT, len(rows)


def test_the_cashier_is_not_handed_the_registry_uid_of_whoever_rang_the_sale(shop):
    """`SELECT s.*` carries `actor_user_uid` -- a registry `users.uid`, i.e. an
    identifier for a PERSON in another database -- and `uid`, the sync
    identity. No till surface reads either (the desktop history table renders
    receipt/date/customer/items/payment/total/status; Android resolves actors
    through the by-employee report, which is gated). They are dropped rather
    than left lying in a payload a cashier can read."""
    rows = shop['cashier'].get(f'{API}/sales/recent?limit=10').get_json()['data']
    assert rows, "no rows came back, so this test would pass having checked nothing"
    leaked = sorted({k for row in rows for k in ('actor_user_uid', 'uid') if k in row})
    assert not leaked, f"a cashier was handed {leaked} on every sale row"


def test_a_cashier_can_still_find_the_sale_they_need_to_refund(shop):
    """THE regression guard on the fix, and the reason the route was not simply
    gated. This is the EXACT request the returns flow makes
    (`_findSaleForReturn`: `/sales/recent?limit=200`, receipt matched
    client-side), and retail.refund is a cashier default -- if this 403s, the
    product has refused a cashier a refund it grants them."""
    r = shop['cashier'].get(f'{API}/sales/recent?limit=200')
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True))
    numbers = {row['sale_number'] for row in r.get_json()['data']}
    assert set(shop['sale_numbers']) <= numbers, (
        f"the refund lookup can no longer see the sales it must refund: "
        f"wanted {shop['sale_numbers']}, got {sorted(numbers)[:5]}")


def test_a_cashier_can_still_look_one_receipt_up_by_number(shop):
    """The targeted lookup -- the shape the returns flow SHOULD be using and
    the one that survives any future tightening of the page size. `q` is a
    single-receipt question, not a browse, so it stays open."""
    wanted = shop['sale_numbers'][0]
    r = shop['cashier'].get(f'{API}/sales/recent?q={wanted}')
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [row['sale_number'] for row in r.get_json()['data']] == [wanted]


def test_the_residual_substring_walk_is_bounded_even_though_it_is_not_closed(crowded_shop):
    """A KNOWN, DELIBERATELY-LEFT GAP, pinned here so it is in a diff rather
    than in nobody's head.

    `q` is a LIKE on `sale_number`, and `_next_ref` numbers receipts
    sequentially and zero-padded ('SALE-000042'). So a caller without
    retail.reports can still shift WHICH sales they see by guessing a common
    prefix -- `q=SALE-000` matches most of the book -- and walk it a page at a
    time. Closing that properly means giving the returns counter a
    single-receipt lookup of its own (`GET /sales/by-number/<n>`) and dropping
    `q` to retail.reports; that spans frontend/subsystem-retail.js, which this
    pass does not own, so it is reported rather than half-done here.

    What IS closed is the unbounded pull: every response is capped, so the
    walk costs one request per page instead of returning the whole history at
    once, and no date range can be used to jump. This test pins the bound --
    if the cap ever stops applying to `q`, the residual stops being bounded
    and becomes the original defect again."""
    walked = crowded_shop['cashier'].get(f'{API}/sales/recent?q=CROWD-&limit=100000')
    assert walked.status_code == 200, walked.get_data(as_text=True)
    assert len(walked.get_json()['data']) <= retail_api_module.TILL_SALES_LOOKUP_MAX_LIMIT, (
        "the page cap is not applied when `q` is present, so the substring walk "
        "above returns the whole book in one request")


def test_a_reports_holder_is_unchanged_in_every_respect(shop):
    """The other direction, which is what stops the fix above from being
    "break the route for everybody". A manager holds retail.reports, so the
    range, the big limit and the identity columns all still work -- otherwise
    the Sales History screen and the desktop's own reporting would be
    collateral damage and §1 would be measuring that instead."""
    r = shop['manager'].get(
        f'{API}/sales/recent?date_from=2020-01-01&date_to=2030-01-01&limit=300')
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True))
    rows = r.get_json()['data']
    assert len(rows) >= 3, rows
    assert 'actor_user_uid' in rows[0], (
        "the identity columns were dropped for a caller who IS allowed the book; "
        "the redaction is keyed on the wrong thing")


# ── 2. the classifier, widened from PATHS to RESPONSES ───────────────────────
#
# The sweep in retail_route_capability_matrix_test.py asks whether a route's
# PATH contains one of twelve report-ish words. That can only ever catch a
# route somebody already named like a report, which is why `/sales/recent`
# -- `SELECT s.* FROM sales`, no report word anywhere -- went unseen.
#
# What follows asks the question that actually matters: does the handler
# EXECUTE SQL that returns the shop's transacted money? It is deliberately
# derived from the handler body rather than from a list, so a new route is
# classified the day it is written and nobody has to remember anything.

#: Columns that are TRANSACTED money -- what the shop took, owes, or is owed.
#: Catalogue prices (`sell_price`, `cost_price`) are deliberately absent: a
#: till has to be able to list products, and the existing sweep pins that
#: `/products` and `/categories` must NOT be swept in.
MONEY_COLUMNS = frozenset({
    'total', 'amount_paid', 'subtotal', 'discount_amount', 'tax_amount',
    'line_total', 'credit_balance', 'revenue', 'expected_cash', 'counted_cash',
    'variance', 'opening_float', 'closing_float', 'cash_in', 'cash_out',
})

#: Tables whose rows ARE money movements. Matched only against a `SELECT *`
#: (or `SELECT alias.*`), which is the shape that silently ships every column
#: a migration ever adds -- exactly how `actor_user_uid` reached the till.
MONEY_TABLES = frozenset({
    'sales', 'sale_items', 'payments', 'purchase_orders', 'po_items',
    'returns', 'return_items', 'cash_sessions', 'cash_movements',
})

SCANNED_ROUTE_FILES = ('api/retail_api.py', 'api/import_api.py')


def _handler_sql(fn):
    """Every SELECT statement literal inside one handler, f-strings included
    (this file's routes build WHERE clauses with them)."""
    out = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append(''.join(v.value for v in node.values
                               if isinstance(v, ast.Constant) and isinstance(v.value, str)))
    return [s for s in out if re.search(r'\bSELECT\b', s, re.I)]


def _money_disclosed_by(fn):
    """What money this handler's own SQL returns -- the evidence, not a bool,
    so a failure names the column or table that made the call."""
    found = set()
    for sql in _handler_sql(fn):
        low = sql.lower()
        for col in MONEY_COLUMNS:
            if re.search(r'(?:^|[\s,.(])' + col + r'\b', low):
                found.add(col)
        if re.search(r'select\s+(?:distinct\s+)?[a-z_]*\.?\*', low):
            for tbl in MONEY_TABLES:
                if re.search(r'(?:from|join)\s+' + tbl + r'\b', low):
                    found.add(tbl + '.*')
    return found


def _capability_check_in(fn):
    """The capability governing this handler, in EITHER of the two forms this
    codebase actually uses -- and it must recognise both, or the sweep is
    wrong about routes that are correctly gated.

    `@mt_require_capability(CODE)` is the whole-route form. The in-handler
    `session_has_capability(CODE)` form is not a lesser one: mt_auth's own
    docstring explains it exists for "the checks a decorator cannot express",
    and `recent_sales` is exactly that case -- the route serves a till lookup
    to everyone and refuses only the BOOK, so a decorator would refuse a
    cashier the refund lookup the product grants them.

    An earlier draft of this file accepted only the decorator, and duly
    reported the one route this wave had just fixed as ungated. That is worth
    leaving on the record: a classifier that only knows one spelling of
    "gated" manufactures false positives, and false positives are how a sweep
    gets an exemption list bolted onto it until it means nothing.

    ── AND THEN IT WAS DEFEATED THE OTHER WAY ──────────────────────────────
    Correcting that false positive introduced a far worse false NEGATIVE. The
    in-handler form was recognised by PRESENCE: any `session_has_capability`
    call anywhere in the handler counted, with its result unread. An
    adversarial verifier injected an ungated `/sales/weekly-takings` returning
    `SELECT total, amount_paid FROM sales` plus one dead line --

        _unused = session_has_capability(CAP_REPORTS)

    -- and all nineteen tests in this file stayed green. Remove the dead line
    and the ratchet correctly went red. So the pass condition was "a call is
    present", not "a check ran": an outcome-shaped guard inside the machinery
    built to prevent outcome-shaped guards.

    The question is now a data-flow one, asked of the AST rather than of the
    source text (three source-text guards in this repo have each been defeated
    by a spelling): does the VALUE the call returns reach a branch, a return
    or a raise? A verdict computed and dropped on the floor gates nothing.

    The analysis, its two consumption paths and -- importantly -- its SIX
    documented limits live in `retail_capability_ratchet_ast`, and are proved
    in both directions, against synthetic spellings AND against the shipped
    route files, by retail_capability_ratchet_consumption_test.py. Read the
    limits before trusting a green: this proves the value is USED, not that
    the code RUNS, and the behavioural half is asserted live in
    retail_route_capability_matrix_test.py."""
    return _ratchet.capability_check_in(fn)


def _route_handlers():
    """(module-relative file, handler name, capability or None, money found,
    diagnosis).

    `diagnosis` distinguishes the two ways `capability` comes back None --
    'no capability check of any kind' versus 'a verdict was computed and then
    never acted on'. They are different bugs and want different fixes, and a
    ratchet failure that cannot tell them apart tells whoever has to fix it
    nothing."""
    for rel in SCANNED_ROUTE_FILES:
        path = BACKEND_DIR / rel
        for name, fn in _ratchet.route_handlers(path):
            _kind, detail = _ratchet.capability_check_detail(fn)
            yield rel, name, _capability_check_in(fn), _money_disclosed_by(fn), detail


#: Routes that returned transacted money and carried no capability on the day
#: this classifier was widened. This is a FROZEN BASELINE, not an approval:
#: each was already live and none was reviewed by the pass that wrote this
#: file. Its whole job is to stop the set GROWING -- a new money-returning
#: route is a failure here on the day it is written. Entries are checked from
#: both ends below, so one that is later gated or deleted fails too.
#:
#: `recent_sales` is deliberately NOT here: it is the route §1 just fixed, and
#: leaving it in the baseline would have made this whole section a no-op on
#: the one case it was built for.
PRE_EXISTING_UNGATED_MONEY_READS = frozenset({
    'list_customers', 'customer_sales', 'customer_statement',
    'list_suppliers', 'list_purchase_orders', 'get_purchase_order',
    'get_sale', 'list_held_sales', 'list_returns',
    # REMOVED BY PHASE 4, which is what this baseline is for.
    #
    #   current_cash_session, list_cash_sessions, get_cash_session
    #
    # All three sat here because they were genuinely ungated and genuinely
    # money-returning: a cash_sessions row carries `opening_float` and
    # `variance`. `list_cash_sessions` in particular handed every drawer in
    # the company, with its float and its shortfall on each row, to anybody
    # who could log in. They now carry retail.cash.close, and WHOSE drawer
    # you may read is a second question the capability does not answer --
    # own terminal is yours, another terminal is a report. The gate is
    # pinned in retail_route_capability_matrix_test.py's
    # EXPECTED_READ_CAPABILITIES and the terminal scope in
    # retail_drawer_terminal_scope_test.py.
    #
    # Deleted rather than left in place: `_stale_baseline_entries` fails on
    # an entry for a route that has since been gated, precisely so that an
    # exemption cannot go on excusing a route that no longer needs excusing
    # and quietly cover the day somebody un-gates it again.
})


def test_the_classifier_actually_read_the_route_files():
    """A scan that parsed nothing passes every assertion below. First guard,
    same reason the live-url_map sweep has one."""
    handlers = list(_route_handlers())
    assert len(handlers) >= 70, len(handlers)
    disclosing = [h for h in handlers if h[3]]
    assert len(disclosing) >= 15, (
        f"the response-shape classifier matched almost nothing -- it is not reading "
        f"the SQL it thinks it is: {[h[1] for h in disclosing]}")


def test_the_widened_classifier_sees_the_route_the_path_classifier_missed():
    """THE point of §2, asserted as a PAIR so it cannot rot into a tautology:
    the same route is INVISIBLE to the path vocabulary and VISIBLE to the
    response shape. If a later edit made the path classifier catch
    `/sales/recent` too, this fails and says so rather than quietly agreeing."""
    path_words = {'report', 'reports', 'x-report', 'z-report', 'dashboard',
                  'receivables', 'payables', 'statement', 'aging', 'daily-cash',
                  'audit-log', 'reconciliation'}
    segments = {seg for seg in '/api/sub/retail/sales/recent'.split('/') if seg}
    assert not (segments & path_words), (
        "the PATH classifier now matches /sales/recent, so it is no longer the "
        "example of what a path-vocabulary sweep cannot see -- pick another")

    found = dict((name, money) for _f, name, _c, money, _d in _route_handlers())
    assert found.get('recent_sales'), (
        "the response-shape classifier does not see `recent_sales` returning money, "
        "so it would not have caught the defect this file was written for")
    assert 'sales.*' in found['recent_sales'], found['recent_sales']


def test_both_spellings_of_gated_are_recognised_and_ungated_still_reads_as_none():
    """Ground truth on `_capability_check_in`, pinned in all THREE directions.
    Accepting the in-handler form is the loosening that makes the ratchet
    correct, so it needs its own proof that it did not simply start saying
    yes to everything."""
    found = {name: capability for _f, name, capability, _m, _d in _route_handlers()}

    assert found.get('report_summary', '').startswith('decorator'), found.get('report_summary')
    assert found.get('recent_sales', '').startswith('in-handler'), found.get('recent_sales')
    assert 'CAP_REPORTS' in found['recent_sales'], (
        f"recent_sales reads SOME capability but not retail.reports: {found['recent_sales']}")
    # A route that checks nothing must still read as nothing, or the ratchet
    # below is a rubber stamp. `list_products` is the catalogue read the
    # existing path sweep also pins as deliberately open to a till.
    assert found.get('list_products') is None, found.get('list_products')


def test_a_capability_call_whose_result_is_never_read_does_not_count_as_gated():
    """THE fourth direction, and the one this classifier shipped without.

    `_capability_check_in` used to accept any `session_has_capability` call in
    the handler with its result unread, so one dead line bought a route a pass
    from the ratchet below. Pinned here, in the file that DEPENDS on the
    answer, as well as in retail_capability_ratchet_consumption_test.py where
    the analysis itself is proved -- because this is the assumption every
    assertion in §2 rests on, and an assumption tested only in another file is
    an assumption this file cannot see break."""
    dead = ast.parse('''
def weekly_takings():
    _unused = session_has_capability(CAP_REPORTS)
    rows = conn.execute("SELECT total, amount_paid FROM sales").fetchall()
    return jsonify({'data': [dict(r) for r in rows]})
''')
    fn = next(n for n in ast.walk(dead) if isinstance(n, ast.FunctionDef))
    assert _capability_check_in(fn) is None, (
        "a capability verdict that is computed and never acted on is being read "
        "as a gate -- the ratchet below is defeated by one dead line")
    # …and the same handler really does trip the money classifier, so the pair
    # above is the ONLY thing that would have stood between it and a green run.
    assert _money_disclosed_by(fn) == {'total', 'amount_paid'}, _money_disclosed_by(fn)


def test_no_new_money_returning_route_is_ungated():
    """THE ratchet. Every handler whose own SQL returns transacted money must
    carry a capability, or be one of the entries frozen above. A route added
    tomorrow that selects `total` and forgets the decorator fails HERE, on the
    day it is written -- which is the whole difference between machinery and
    somebody noticing.

    "Carries a capability" means the verdict is ACTED ON -- see
    `_capability_check_in`. A route that reads a capability and ignores the
    answer fails this test and is told exactly that."""
    unaccounted = sorted(
        f"{rel}::{name} returns {sorted(money)}"
        + (f" -- it DOES read a capability, but the result is never acted on: {diagnosis}"
           if diagnosis else "")
        for rel, name, capability, money, diagnosis in _route_handlers()
        if money and capability is None and name not in PRE_EXISTING_UNGATED_MONEY_READS
    )
    assert not unaccounted, (
        "these handlers execute SQL that returns the shop's transacted money and "
        "carry no @mt_require_capability. Gate them, or -- if a till genuinely "
        "needs the read -- say so on the route and add the name to "
        "PRE_EXISTING_UNGATED_MONEY_READS with the reason:\n  "
        + "\n  ".join(unaccounted))


def _stale_baseline_entries(baseline, live):
    """The three ways a frozen-baseline entry stops meaning what it says.

    Lifted out of the test below and given `live` as an ARGUMENT rather than
    reading the route files itself, so each arm can be driven with a synthetic
    world and proved to fire. A both-ends check that has never been watched
    firing is exactly the kind of claim this wave keeps finding to be false:
    the file said "checked from both ends", and nothing had ever confirmed the
    other end was wired up at all."""
    stale = []
    for name in sorted(baseline):
        if name not in live:
            stale.append(f"{name}: no longer a route -- delete the entry")
            continue
        capability, money = live[name]
        if capability is not None:
            stale.append(f"{name}: now carries {capability} -- delete the entry, it is gated")
        elif not money:
            stale.append(f"{name}: no longer returns money -- delete the entry")
    return stale


def _live_baseline_facts():
    return {name: (capability, money)
            for _f, name, capability, money, _d in _route_handlers()}


def test_the_frozen_baseline_is_still_accurate_from_both_ends():
    """A baseline nobody rechecks is a rug. An entry for a route that has since
    been gated, renamed or deleted keeps excusing nothing and hides the next
    regression behind a stale name.

    THE AUDIT: as of this pass all twelve entries were re-derived from the
    route files and every one still belongs -- still a live handler, still
    carrying no capability in either recognised spelling, still returning
    transacted money. None has been gated or deleted since the baseline was
    frozen, so nothing is dropped here. That is the finding, not an absence
    of one; the three entries flagged in the hand-over note below are still
    unadjudicated and still real."""
    stale = _stale_baseline_entries(PRE_EXISTING_UNGATED_MONEY_READS, _live_baseline_facts())
    assert not stale, "\n  ".join(stale)


def test_the_baseline_audit_really_fires_on_each_way_an_entry_goes_stale():
    """The both-ends claim, driven rather than asserted about.

    `test_the_frozen_baseline_is_still_accurate_from_both_ends` passes today
    because every entry is genuinely still accurate -- which is indistinguishable
    from it passing because the check does nothing. These three synthetic worlds
    are the difference: each breaks one entry one way, and each must be caught
    and NAMED. A fourth case pins that an accurate entry stays silent, so this
    cannot pass by simply always complaining."""
    accurate = {'list_customers': (None, {'credit_balance'})}
    assert _stale_baseline_entries({'list_customers'}, accurate) == []

    # 1. the route was deleted or renamed
    deleted = _stale_baseline_entries({'list_customers'}, {})
    assert len(deleted) == 1 and 'no longer a route' in deleted[0], deleted

    # 2. the route has since been GATED -- the entry now excuses a route that
    #    does not need excusing, and hides the day it is un-gated again
    gated = _stale_baseline_entries(
        {'list_customers'}, {'list_customers': ('decorator @mt_require_capability(CAP_REPORTS)',
                                                {'credit_balance'})})
    assert len(gated) == 1 and 'it is gated' in gated[0], gated

    # 3. the route stopped returning money -- the exemption is now load-bearing
    #    for nothing, and the next money column added to it is unwatched
    demonetised = _stale_baseline_entries({'list_customers'}, {'list_customers': (None, set())})
    assert len(demonetised) == 1 and 'no longer returns money' in demonetised[0], demonetised

    # every arm names the entry it rejected, or a failure is unactionable
    for arm in (deleted, gated, demonetised):
        assert arm[0].startswith('list_customers:'), arm


def test_the_baseline_is_not_quietly_excusing_routes_that_do_not_exist():
    """The stale-entry arm again, this time against the REAL route files rather
    than a synthetic world -- because arm 1 above proves the FUNCTION works,
    and this proves it is being pointed at something real. A baseline whose
    names had all drifted would make `_stale_baseline_entries` fire twelve
    times; a baseline read from an empty scan would make it fire twelve times
    too, and only this catches the second."""
    live = _live_baseline_facts()
    assert len(live) >= 70, len(live)
    missing = sorted(PRE_EXISTING_UNGATED_MONEY_READS - set(live))
    assert not missing, missing
    # …and every entry is genuinely money-returning and genuinely ungated,
    # asserted positively rather than only via the absence of a complaint.
    for name in sorted(PRE_EXISTING_UNGATED_MONEY_READS):
        capability, money = live[name]
        assert capability is None, (name, capability)
        assert money, name


def test_the_route_this_file_fixed_is_gated_by_behaviour_not_by_a_list():
    """`recent_sales` is absent from the baseline above, so the ratchet would
    fail on it unless the handler really does consult the capability. This
    pins the mechanism rather than the outcome: the fix must be an actual
    `session_has_capability(CAP_REPORTS)` read in that handler, not a name
    quietly added to the exemption set."""
    source = Path(retail_api_module.__file__).read_text(encoding='utf-8')
    tree = ast.parse(source)
    handler = next(fn for fn in ast.walk(tree)
                   if isinstance(fn, ast.FunctionDef) and fn.name == 'recent_sales')
    body = ast.unparse(handler)
    assert 'session_has_capability' in body and 'CAP_REPORTS' in body, (
        "recent_sales no longer consults retail.reports; the §1 behaviour tests "
        "would be the only thing holding the book shut")
    assert 'recent_sales' not in PRE_EXISTING_UNGATED_MONEY_READS


# ── HAND-OVER ────────────────────────────────────────────────────────────────
#
# TO THE OWNER OF retail_route_capability_matrix_test.py:
#
# §2 above is a strictly wider replacement for `_discloses_money` /
# MONEY_DISCLOSING_SEGMENTS in that file. Folding it in is a move, not a
# rewrite: `_money_disclosed_by(fn)` replaces `_discloses_money(path)`, and
# `test_every_live_route_that_discloses_the_shops_money_is_gated_or_excused`
# then reads the handler rather than the URL. Keep the path classifier as
# well -- the two are complementary. A route can be named like a report and
# execute no SQL at all (an aggregate assembled in Python from a helper, which
# is what `report_summary` does through `_compute_report_summary`), and the
# path vocabulary is what catches those.
#
# The twelve names in PRE_EXISTING_UNGATED_MONEY_READS are the real finding
# here and are NOT adjudicated -- this pass owned `recent_sales` and nothing
# else on that list. Each needs the same till-fact-vs-report question §1
# asked, and at least these three look like the same defect again:
#
#   * list_customers        -- `SELECT *` over `customers` carries
#                              `credit_balance`, so `/customers` hands back the
#                              whole debtor book that `customers_receivables`
#                              gates on retail.reports. Same shape as this
#                              file's defect, different table.
#   * list_suppliers        -- the mirror image, against `suppliers`.
#   * list_cash_sessions    -- every drawer's expected/counted/variance for the
#                              whole company; `cash_session_x_report` gates ONE
#                              session's equivalent on retail.cash.close.
