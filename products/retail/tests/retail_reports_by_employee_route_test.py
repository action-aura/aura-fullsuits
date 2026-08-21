"""
Aura Retail -- GET /api/sub/retail/reports/by-employee, and the identical
"who rang this?" resolution on the sale-detail route.

WHY THIS FILE EXISTS. Two clients shipped a by-employee panel against a route
that was never written: the desktop Reports page (frontend/subsystem-retail.js)
and the Android EmployeeSalesScreen both fetch `/reports/by-employee` and both
get a 404 from the live url_map. Separately, BOTH clients read an
`employee_name` (desktop) / `employee_id` + `email` (Android) off the
sale-detail payload -- fields `get_sale()` has never produced, because it
selects `s.*` plus the customer name and nothing else. So the Cashier cell on
every sale detail, and every row of the by-employee table, renders a truncated
uuid4 to a manager who wanted a person's name.

The hard part is not the SQL. `metrics.revenue_by_employee()` already returns
the figures and says so in its own docstring:

    NO NAMES -- `actor_user_uid` resolves through registry.db's `users`
    table, a different database on a connection this module does not hold
    (#8). The route joins it.

That cross-database join is what this file pins: it must happen ONCE per
request in bulk (not once per row), it must fail SOFT (a report of raw uids is
degraded, a report that 500s is absent), and it must NEVER invent a name --
the unattributed bucket has to stay something no client can render as a
person.

Run:
    pytest products/retail/tests/retail_reports_by_employee_route_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_byemployee_"))
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
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import metrics  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

URL = '/api/sub/retail/reports/by-employee'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── fixture ──────────────────────────────────────────────────────────────────

def _make_user(company_id, role='admin', *, email=None, employee_id=None, with_uid=True):
    """A real registry account. `uid` is written inline, exactly as
    onboarding_routes.create_admin/create_employee do in production -- the uid
    is the value under test here, so a fixture that left it NULL would make
    every name assertion below pass vacuously against a NULL == NULL."""
    email = email or f"emp-{uuid.uuid4().hex[:10]}@test.local"
    employee_id = employee_id or f"EMP-{uuid.uuid4().hex[:6].upper()}"
    password = "ByEmployeePW1"
    user_id = str(uuid.uuid4())
    user_uid = str(uuid.uuid4()) if with_uid else None

    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,?,0)",
        (user_id, user_uid, company_id, employee_id, email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return {'client': client, 'user_id': user_id, 'user_uid': user_uid,
            'email': email, 'employee_id': employee_id}


def _make_shop(stock=5000):
    company_id = str(uuid.uuid4())
    owner = _make_user(company_id, 'admin')

    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) "
        "VALUES (?,?,?,'By-Employee Item',5,100,0)",
        (product_id, company_id, f'BE-{uuid.uuid4().hex[:8]}'))
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, product_id, branch_id, stock))
    rconn.commit()
    rconn.close()

    owner['client'].get('/api/sub/retail/settings/tax')  # _ensure_credit_schema / doc_sequences

    import random as _random
    dconn = get_retail_conn()
    for doc_type in ('sale', 'return', 'po', 'receipt', 'supplier_payment'):
        dconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
            (company_id, doc_type, _random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()

    return {'company_id': company_id, 'branch_id': branch_id, 'product_id': product_id,
            'owner': owner, 'client': owner['client']}


def _sell(shop, client=None, qty=1):
    r = (client or shop['client']).post('/api/sub/retail/sales', json={
        'items': [{'product_id': shop['product_id'], 'quantity': qty}],
        'payment_method': 'cash', 'amount_paid': 100000,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


def _raw_sale(shop, actor_user_uid, total=50.0):
    """A sale written straight to retail.db, so a test can pin the shape of a
    bucket the routes cannot easily produce on demand -- the pre-v13
    unattributed row (actor_user_uid IS NULL) and the deleted-account row
    (a uid with no registry `users` row behind it)."""
    conn = get_retail_conn()
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,cashier,subtotal,total,amount_paid,"
        "payment_method,status,created_at,actor_user_uid,created_at_utc,uid) "
        "VALUES (?,?,?,'POS',?,?,?,'cash','completed',?,?,?,?)",
        (shop['company_id'], f'RAW-{uuid.uuid4().hex[:12]}', shop['branch_id'], total, total, total,
         now_local, actor_user_uid, datetime.now(timezone.utc).isoformat(), str(uuid.uuid4())))
    conn.commit()
    conn.close()


def _get(shop, client=None, **params):
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    return (client or shop['client']).get(f'{URL}?{query}' if query else URL)


# ── 1. the route exists at all ───────────────────────────────────────────────

def test_the_route_is_registered_in_the_live_url_map():
    """The headline. Both shipped clients fetch this path; until this passes
    they are fetching a 404 and rendering their own error branch."""
    rules = {str(r.rule) for r in app.url_map.iter_rules()}
    assert '/api/sub/retail/reports/by-employee' in rules, (
        "the by-employee report route is not registered -- the desktop Reports "
        "panel and the Android EmployeeSalesScreen both already fetch it")


def test_the_route_answers_200_for_a_user_who_may_read_the_numbers():
    shop = _make_shop()
    _sell(shop)
    r = _get(shop)
    assert r.status_code == 200, r.get_data(as_text=True)


# ── 2. the envelope ──────────────────────────────────────────────────────────

def test_the_envelope_carries_both_discriminators_so_neither_client_changes():
    """The five sibling report widgets on the same page return
    `{'success': True, ...}`; the rest of retail_api.py returns
    `{'status': 'success', ...}`. The desktop panel hard-gates on
    `body.status !== 'success'` and Android's ByEmployeeResponse deserializes
    `success`. Emitting BOTH is what lets two already-shipped clients read one
    payload without either of them changing -- Gson ignores the unknown key
    and the desktop ignores `success`."""
    shop = _make_shop()
    _sell(shop)
    body = _get(shop).get_json()
    assert body['status'] == 'success', body
    assert body['success'] is True, body
    assert isinstance(body['data'], list), body


def test_a_failure_is_the_error_envelope_every_route_in_this_file_agrees_on():
    shop = _make_shop()
    r = _get(shop, days='not-a-number')
    assert r.status_code == 400, r.get_data(as_text=True)
    body = r.get_json()
    assert body['status'] == 'error', body
    assert body.get('message'), body


# ── 3. the row shape both clients read ───────────────────────────────────────

_ROW_FIELDS = {'actor_user_uid', 'employee_id', 'email', 'employee_name',
               'transactions', 'gross_sales', 'refunds', 'revenue', 'avg_ticket'}


def test_every_row_carries_exactly_the_union_both_clients_read():
    """Desktop reads actor_user_uid/employee_name/transactions/revenue/
    avg_ticket. Android reads actor_user_uid/employee_id/email/transactions/
    revenue/avg_ticket. gross_sales and refunds are carried even though no
    screen draws them today: they are the audit trail that makes `revenue`
    checkable, and without them the API cannot answer "why is this negative?"."""
    shop = _make_shop()
    _sell(shop)
    rows = _get(shop).get_json()['data']
    assert rows, 'the report came back empty for a shop that just sold something'
    for row in rows:
        assert set(row) == _ROW_FIELDS, f"row keys drifted: {sorted(set(row) ^ _ROW_FIELDS)}"


def test_the_figures_are_metrics_own_and_are_not_recomputed_in_the_route():
    """The route must be a join, not a second implementation of revenue."""
    shop = _make_shop()
    _sell(shop, qty=3)
    rows = _get(shop, days=30).get_json()['data']

    conn = get_retail_conn()
    expected = metrics.revenue_by_employee(
        conn, shop['company_id'], metrics.period_days(30, datetime.now()))
    conn.close()

    assert [r['actor_user_uid'] for r in rows] == [r['actor_user_uid'] for r in expected]
    for got, want in zip(rows, expected):
        for field in ('transactions', 'gross_sales', 'refunds', 'revenue', 'avg_ticket'):
            assert got[field] == want[field], (field, got, want)


# ── 4. the cross-database name resolution ────────────────────────────────────

def test_the_employee_name_is_resolved_from_the_registry_users_row():
    """`actor_user_uid` is registry `users.uid` and registry.db is a DIFFERENT
    FILE from retail.db. Nothing in retail.db can answer this."""
    shop = _make_shop()
    _sell(shop)
    rows = _get(shop).get_json()['data']
    mine = [r for r in rows if r['actor_user_uid'] == shop['owner']['user_uid']]
    assert mine, f"the seller's own uid is not in the report: {rows}"
    row = mine[0]
    assert row['email'] == shop['owner']['email']
    assert row['employee_id'] == shop['owner']['employee_id']


def test_employee_name_prefers_the_email_and_falls_back_to_the_employee_id():
    """Android's already-shipped `attributedName` is email-first, employee_id
    second (EmployeeSalesScreen.kt, matching EmployeesScreen). The desktop has
    no established preference, so Android's order wins by default -- and
    deriving the desktop's single display string SERVER-SIDE from the same two
    columns Android receives is what guarantees the two clients can never
    disagree about who a row is."""
    shop = _make_shop()
    _sell(shop)
    row = [r for r in _get(shop).get_json()['data']
           if r['actor_user_uid'] == shop['owner']['user_uid']][0]
    assert row['employee_name'] == shop['owner']['email']

    # Blank the email and the display string must fall through to the
    # employee_id rather than to '' -- both clients treat a blank string as
    # "no identity", so an empty display name renders as a failed page.
    conn = registry_conn()
    conn.execute("UPDATE users SET email='' WHERE id=?", (shop['owner']['user_id'],))
    conn.commit()
    conn.close()
    row = [r for r in _get(shop).get_json()['data']
           if r['actor_user_uid'] == shop['owner']['user_uid']][0]
    assert row['employee_name'] == shop['owner']['employee_id'], row
    assert row['email'] is None, "a blank registry email must arrive as null, never ''"


def test_a_uid_whose_account_is_gone_keeps_the_uid_and_nulls_the_names():
    """Android's three-state classifier has an ACCOUNT_GONE state that exists
    ONLY because it receives the uid and the identity fields separately: uid
    present + names absent means the account was deleted since the sale. A
    pre-joined `employee_name` alone cannot express it, which is why the raw
    columns are on the wire too."""
    shop = _make_shop()
    ghost = str(uuid.uuid4())
    _raw_sale(shop, ghost, total=77.0)
    rows = _get(shop).get_json()['data']
    row = [r for r in rows if r['actor_user_uid'] == ghost]
    assert row, f"the unresolvable uid was dropped from the report: {rows}"
    row = row[0]
    assert row['employee_id'] is None and row['email'] is None and row['employee_name'] is None, row
    assert row['revenue'] == 77.0, row


def test_the_registry_is_read_once_per_request_in_bulk_not_once_per_row():
    """_stamp()'s own rule, stated on the write side and equally binding here:
    "resolve once per request, not per row". A per-row lookup on a shop with
    forty staff is forty opens of a second SQLite file inside one report."""
    shop = _make_shop()
    for _ in range(6):
        _raw_sale(shop, str(uuid.uuid4()))
    _sell(shop)

    opens = {'n': 0}
    real = retail_api_module._registry_conn

    def counting(*a, **kw):
        opens['n'] += 1
        return real(*a, **kw)

    retail_api_module._registry_conn = counting
    try:
        rows = _get(shop).get_json()['data']
    finally:
        retail_api_module._registry_conn = real

    assert len(rows) >= 7, rows
    assert opens['n'] == 1, (
        f"the route opened registry.db {opens['n']} times for {len(rows)} rows -- "
        f"the lookup must be ONE batched query")


def test_a_registry_that_cannot_be_read_degrades_it_does_not_500():
    """A report showing raw uids is degraded. A report that 500s is absent.
    Same posture `_actor_user_uid` takes on the write side ("Never raises.
    Attribution is bookkeeping and a sale is the business")."""
    shop = _make_shop()
    _sell(shop)

    def exploding(*a, **kw):
        raise sqlite3.OperationalError('database is locked')

    real = retail_api_module._registry_conn
    retail_api_module._registry_conn = exploding
    try:
        r = _get(shop)
    finally:
        retail_api_module._registry_conn = real

    assert r.status_code == 200, r.get_data(as_text=True)
    rows = r.get_json()['data']
    assert rows, 'the figures were lost along with the names'
    for row in rows:
        assert row['employee_name'] is None and row['email'] is None and row['employee_id'] is None
        assert row['actor_user_uid'] is not None or row['revenue'] is not None


# ── 5. the unattributed bucket ───────────────────────────────────────────────

def test_the_unattributed_bucket_nulls_all_four_identity_fields_together():
    """It must be impossible for a client to render this row as a person.
    Never '', never 'POS', never 'Unknown', never a server-side translated
    word -- an English word inside an RTL Arabic layout, and it would defeat
    both clients' own three-state classifiers. The words are the client's
    job."""
    shop = _make_shop()
    _raw_sale(shop, None, total=42.0)
    rows = _get(shop).get_json()['data']
    unattributed = [r for r in rows if r['actor_user_uid'] is None]
    assert unattributed, f"the unattributed bucket was filtered out: {rows}"
    row = unattributed[0]
    assert row['actor_user_uid'] is None
    assert row['employee_id'] is None
    assert row['email'] is None
    assert row['employee_name'] is None
    assert row['revenue'] == 42.0, row


def test_never_falls_back_to_the_free_text_cashier_column():
    """`sales.cashier` is whatever string the client posted, defaulting to the
    literal 'POS'. v13's migration docstring is explicit that it is kept, never
    read, and never used to guess an identity: money grouped by free text would
    look authoritative and be worthless."""
    shop = _make_shop()
    conn = get_retail_conn()
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO sales (company_id,sale_number,branch_id,cashier,subtotal,total,amount_paid,"
        "payment_method,status,created_at,actor_user_uid,created_at_utc,uid) "
        "VALUES (?,?,?,'Mahmoud at till 2',?,?,?,'cash','completed',?,NULL,?,?)",
        (shop['company_id'], f'RAW-{uuid.uuid4().hex[:12]}', shop['branch_id'], 31.0, 31.0, 31.0,
         now_local, datetime.now(timezone.utc).isoformat(), str(uuid.uuid4())))
    conn.commit()
    conn.close()

    rows = _get(shop).get_json()['data']
    blob = repr(rows)
    assert 'Mahmoud at till 2' not in blob, (
        "the free-text `cashier` column leaked into the report as an identity")
    assert [r for r in rows if r['actor_user_uid'] is None], rows


def test_there_is_exactly_one_unattributed_row():
    """A HARD contract term, not a nicety. Android's LazyColumn deliberately
    does not key on actor_user_uid, because two null-uid rows would crash the
    reporting screen with "Key was already used"."""
    shop = _make_shop()
    for _ in range(4):
        _raw_sale(shop, None, total=10.0)
    rows = _get(shop).get_json()['data']
    nulls = [r for r in rows if r['actor_user_uid'] is None]
    assert len(nulls) == 1, f"{len(nulls)} unattributed rows -- Android crashes on the second"
    assert nulls[0]['revenue'] == 40.0, nulls


def test_no_identity_field_is_ever_the_empty_string():
    """`null`, never `''`, never the string 'null'. Load-bearing on both
    sides: each client treats a blank as absent, and Android's response field
    is nullable specifically because Gson writes JSON null into non-null
    Kotlin fields and NPEs later, outside the try/catch."""
    shop = _make_shop()
    _sell(shop)
    _raw_sale(shop, None)
    _raw_sale(shop, str(uuid.uuid4()))
    for row in _get(shop).get_json()['data']:
        for field in ('actor_user_uid', 'employee_id', 'email', 'employee_name'):
            assert row[field] != '', (field, row)
            assert row[field] != 'null', (field, row)


# ── 6. the contract terms that are easy to quietly drop ──────────────────────

def test_no_limit_is_applied_to_a_payroll_shaped_number():
    """metrics.revenue_by_employee has no `limit` on purpose: "a payroll-shaped
    number that silently stops at ten people is worse than no number". The
    route must not add one back."""
    shop = _make_shop()
    for _ in range(14):
        _raw_sale(shop, str(uuid.uuid4()))
    rows = _get(shop).get_json()['data']
    assert len(rows) >= 14, f"only {len(rows)} rows came back -- a limit crept in"


def test_the_window_honours_a_frozen_clock():
    """Every report route in this file takes `datetime.now()` INLINE and hands
    it to metrics, because metrics imports no `datetime` at all. A route that
    let metrics read the clock would silently measure the wall clock while a
    frozen-clock test believed otherwise -- exactly the seam that produced the
    hourly-chart regression. The sibling sweep in
    retail_metrics_consistency_test.py does not know about this route yet, so
    it is pinned here."""
    shop = _make_shop()
    _sell(shop)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2019, 6, 15, 12, 0, 0)

    original = retail_api_module.datetime
    retail_api_module.datetime = _FrozenDateTime
    try:
        body = _get(shop, days=30).get_json()
    finally:
        retail_api_module.datetime = original
    assert body['data'] == [], 'report_by_employee ignored the frozen clock'


def test_the_branch_filter_is_honoured():
    shop = _make_shop()
    _sell(shop)
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Second')", (shop['company_id'],))
    conn.commit()
    other = conn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id DESC LIMIT 1",
                         (shop['company_id'],)).fetchone()[0]
    conn.close()
    assert _get(shop, branch_id=other).get_json()['data'] == []
    assert _get(shop, branch_id=shop['branch_id']).get_json()['data'] != []


def test_another_companys_staff_never_appear():
    """`company_id` scoping on the registry lookup is free defence in depth on
    a multi-company install, and the retail cid and the registry
    users.company_id are the same value by construction (schema.py's
    _rebind_to_owner_issued)."""
    shop = _make_shop()
    intruder_company = str(uuid.uuid4())
    intruder = _make_user(intruder_company, 'admin', email='intruder@other.local')
    _raw_sale(shop, intruder['user_uid'], total=13.0)

    rows = _get(shop).get_json()['data']
    row = [r for r in rows if r['actor_user_uid'] == intruder['user_uid']][0]
    assert row['email'] is None, "a user from ANOTHER company was resolved into this report"
    assert row['employee_name'] is None, row


# ── 7. the gate ──────────────────────────────────────────────────────────────

def test_the_route_is_gated_on_retail_reports_like_its_five_siblings():
    shop = _make_shop()
    _sell(shop)
    cashier = _make_user(shop['company_id'], 'cashier')
    assert user_accounts.CAP_REPORTS not in user_accounts.capabilities_for_role(
        user_accounts.ROLE_CASHIER), 'this assertion would be vacuous otherwise'
    assert _get(shop, client=cashier['client']).status_code == 403

    manager = _make_user(shop['company_id'], 'manager')
    assert user_accounts.CAP_REPORTS in user_accounts.capabilities_for_role(
        user_accounts.ROLE_MANAGER)
    assert _get(shop, client=manager['client']).status_code == 200


def test_an_anonymous_request_is_401_not_403():
    """Decorator order: login runs before capability, so an anonymous caller
    is not told the route exists or which capability guards it."""
    anon = app.test_client()
    assert anon.get(URL).status_code == 401


# ── 8. the same dead branch on the sale-detail route ─────────────────────────

def test_the_sale_detail_resolves_the_cashier_to_a_person():
    """get_sale() selects `s.*, COALESCE(c.name,'Walk-in') as customer_name`
    and nothing else, so the desktop's `sale.employee_name` and Android's
    `sale.actor_employee_id` / `sale.actor_email` have never been produced by
    any backend code. Both clients fall through to their "no resolved name"
    branch and print the raw uid."""
    shop = _make_shop()
    sale = _sell(shop)
    detail = shop['client'].get(f"/api/sub/retail/sales/{sale['id']}").get_json()['data']['sale']
    assert detail['actor_user_uid'] == shop['owner']['user_uid'], detail
    assert detail['employee_name'] == shop['owner']['email'], detail
    assert detail['actor_email'] == shop['owner']['email'], detail
    assert detail['actor_employee_id'] == shop['owner']['employee_id'], detail


def test_a_sale_with_no_actor_gets_nulls_and_never_an_invented_name():
    shop = _make_shop()
    _raw_sale(shop, None, total=9.0)
    conn = get_retail_conn()
    sale_id = conn.execute(
        "SELECT id FROM sales WHERE company_id=? AND actor_user_uid IS NULL ORDER BY id DESC LIMIT 1",
        (shop['company_id'],)).fetchone()[0]
    conn.close()
    detail = shop['client'].get(f"/api/sub/retail/sales/{sale_id}").get_json()['data']['sale']
    assert detail['actor_user_uid'] is None
    assert detail['employee_name'] is None
    assert detail['actor_email'] is None
    assert detail['actor_employee_id'] is None
    # ...and emphatically not the free-text cashier column beside it.
    assert detail['cashier'] == 'POS'


def test_the_sale_detail_name_lookup_also_degrades_instead_of_500ing():
    shop = _make_shop()
    sale = _sell(shop)

    def exploding(*a, **kw):
        raise sqlite3.OperationalError('database is locked')

    real = retail_api_module._registry_conn
    retail_api_module._registry_conn = exploding
    try:
        r = shop['client'].get(f"/api/sub/retail/sales/{sale['id']}")
    finally:
        retail_api_module._registry_conn = real
    assert r.status_code == 200, r.get_data(as_text=True)
    detail = r.get_json()['data']['sale']
    assert detail['employee_name'] is None
    assert detail['total'] is not None, 'the sale itself was lost with the name'
