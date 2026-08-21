"""
Aura Retail -- capability gating on the retail API surface (Phase 1, design
§3 "Permissions" / §7 step 1).

Until this suite existed, ~80 retail routes carried one literal
`@mt_require_subsystem('retail')` and nothing finer, so every user who could
reach the product at all could do everything in it: a cashier could adjust
stock, read the whole debtor book, change the shop's tax mode and wipe the
company. Replacing that blanket with the eight capability codes seeded into
`user_permissions` is what this file guards.

TWO HALVES, and the first is the durable one:

1. A STATIC MATRIX over the route source (AST, not imports). Every mutating
   route -- POST / PUT / PATCH / DELETE -- must carry exactly one explicit
   `@mt_require_capability(...)`, naming one of the eight seeded codes, and
   it must sit INSIDE `@mt_login_required`. The full mutating-route ->
   capability map is frozen here as data. A newly added route therefore
   cannot silently inherit blanket access: it fails this file until somebody
   states, in the test, what authority it needs. Changing an existing gate is
   equally visible -- it is a diff in a table, not a one-word edit buried in
   4,300 lines.

2. LIVE BEHAVIOUR through the real Flask app: a real cashier, a real manager
   and a real admin against real routes, plus the fail-closed path. A static
   check alone would pass just as happily if the decorator were a no-op.

Run:
    pytest products/retail/tests/retail_route_capability_matrix_test.py -v
"""
import ast
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_capmatrix_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

# The live blueprint module object app.py's init_app() actually registered
# (BACKEND_DIR is already on sys.path above, and this import returns the
# SAME cached module from sys.modules rather than a second copy) -- needed
# so the reachability tests below can monkeypatch the NAME
# `session_has_capability` as void_payment actually resolves it. retail_api.py
# does `from commercial_runtime.identity.mt_auth import session_has_capability`,
# which binds a SEPARATE reference in retail_api's own module globals; patching
# mt_auth.session_has_capability would not touch that binding at all.
import api.retail_api as retail_api_module  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


RETAIL_API = BACKEND_DIR / 'api' / 'retail_api.py'
IMPORT_API = BACKEND_DIR / 'api' / 'import_api.py'

MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

CAP_SELL = 'retail.sell'
CAP_REFUND = 'retail.refund'
CAP_DISCOUNT = 'retail.discount'
CAP_STOCK = 'retail.stock.adjust'
CAP_REPORTS = 'retail.reports'
CAP_CASH_CLOSE = 'retail.cash.close'
CAP_CASH_APPROVE = 'retail.cash.approve'
CAP_EMPLOYEES = 'retail.employees'


# ─────────────────────────────────────────────────────────────────────────────
# THE FROZEN MATRIX
#
# view function -> the capability a caller must hold to reach it. Every
# mutating route in the retail backend appears here exactly once; anything
# missing, extra, or gated differently fails the tests below.
#
# The tiers this encodes (design §3, and the product owner's own wording):
#   cashier   sells, and refunds against a sale that is already on this
#             device -- retail.sell / retail.refund
#   manager   additionally discounts, adjusts stock, closes a drawer and
#             reads the numbers -- retail.discount / retail.stock.adjust /
#             retail.cash.close / retail.reports
#   owner     additionally approves a cash variance and administers the shop
#             -- retail.cash.approve / retail.employees
# ─────────────────────────────────────────────────────────────────────────────
EXPECTED_MUTATION_CAPABILITIES = {
    # ── Catalogue and master data: retail.stock.adjust ───────────────────────
    'create_category': CAP_STOCK,
    'update_category': CAP_STOCK,
    'delete_category': CAP_STOCK,
    'create_product': CAP_STOCK,
    'update_product': CAP_STOCK,
    'delete_product': CAP_STOCK,
    'adjust_stock': CAP_STOCK,
    'create_supplier': CAP_STOCK,
    'update_supplier': CAP_STOCK,
    'delete_supplier': CAP_STOCK,
    'create_supplier_contact': CAP_STOCK,
    'update_supplier_contact': CAP_STOCK,
    'delete_supplier_contact': CAP_STOCK,
    'create_purchase_order': CAP_STOCK,
    'receive_purchase_order': CAP_STOCK,
    'preview_po_split': CAP_STOCK,
    'accept_reorder_request': CAP_STOCK,
    'decline_reorder_request': CAP_STOCK,
    'repair_inventory_reconciliation': CAP_STOCK,
    # Retiring a customer is master-data maintenance, not selling -- see the
    # comment on the route for why it splits from its create/update siblings.
    'delete_customer': CAP_STOCK,

    # ── The till: retail.sell ────────────────────────────────────────────────
    'create_sale': CAP_SELL,
    'hold_sale': CAP_SELL,
    'resume_held_sale': CAP_SELL,
    'discard_held_sale': CAP_SELL,
    'create_customer': CAP_SELL,
    'update_customer': CAP_SELL,
    'customer_payment': CAP_SELL,

    # ── Reversing money already taken: retail.refund ─────────────────────────
    'create_return': CAP_REFUND,
    'void_payment': CAP_REFUND,

    # ── This terminal's drawer: retail.cash.close ────────────────────────────
    'open_cash_session': CAP_CASH_CLOSE,
    'create_cash_movement': CAP_CASH_CLOSE,
    'close_cash_session': CAP_CASH_CLOSE,

    # ── Sending or generating the shop's numbers: retail.reports ─────────────
    'report_summary_email': CAP_REPORTS,
    'report_whatsapp': CAP_REPORTS,
    'ai_chat': CAP_REPORTS,

    # ── Owner administration of the shop itself: retail.employees ────────────
    'create_branch': CAP_EMPLOYEES,
    'credit_settings_set': CAP_EMPLOYEES,
    'tax_settings_set': CAP_EMPLOYEES,
    'payment_methods_add': CAP_EMPLOYEES,
    'supplier_payment': CAP_EMPLOYEES,
    'pay_purchase_order': CAP_EMPLOYEES,
    'demo_wipe': CAP_EMPLOYEES,
    'demo_seed': CAP_EMPLOYEES,

    # ── Bulk import (import_api.py): retail.stock.adjust ─────────────────────
    # `execute`/`smart-execute` write products, customers, suppliers, branches
    # and opening stock; `parse`/`detect`/`clean` are previews of the same
    # upload. All five are POST, so all five are gated -- a preview of an
    # import nobody may run is not a capability worth carving out, and an
    # exemption is one more place a future route could hide.
    'parse_file_endpoint': CAP_STOCK,
    'detect_entities': CAP_STOCK,
    'smart_execute': CAP_STOCK,
    'clean_preview': CAP_STOCK,
    'execute_import': CAP_STOCK,
}

#: Read routes that deliberately DO carry a capability. Reads are not required
#: to (a till has to be able to list products), but the ones that disclose the
#: shop's financial position are gated on retail.reports and that decision is
#: pinned here so it cannot be quietly dropped.
EXPECTED_READ_CAPABILITIES = {
    'dashboard_stats': CAP_REPORTS,
    'report_sales_trend': CAP_REPORTS,
    'report_top_products': CAP_REPORTS,
    'report_payment_methods': CAP_REPORTS,
    'report_summary': CAP_REPORTS,
    'report_by_branch': CAP_REPORTS,
    'daily_cash': CAP_REPORTS,
    'aging_report': CAP_REPORTS,
    'customers_receivables': CAP_REPORTS,
    'suppliers_payables': CAP_REPORTS,
    'supplier_statement': CAP_REPORTS,
    'list_audit_log': CAP_REPORTS,
    'inventory_reconciliation': CAP_REPORTS,
}


# ── AST extraction ───────────────────────────────────────────────────────────

class _Route:
    __slots__ = ('func', 'rule', 'methods', 'decorators', 'capability', 'lineno')

    def __init__(self, func, rule, methods, decorators, capability, lineno):
        self.func = func
        self.rule = rule
        self.methods = methods
        self.decorators = decorators
        self.capability = capability
        self.lineno = lineno

    def __repr__(self):  # pragma: no cover - only rendered inside a failure
        return f"<{self.func} {sorted(self.methods)} {self.rule} cap={self.capability}>"


def _decorator_name(node):
    """'a.b.c(...)' / 'a.b.c' -> 'a.b.c'."""
    target = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return '.'.join(reversed(parts))


def _literal(node):
    """The string a decorator argument names, whether it was written as a
    literal ('retail.sell') or as the imported constant (CAP_SELL). Constants
    are resolved through user_accounts so the route file can use the names
    rather than re-typing eight strings 80 times -- re-typed strings are how a
    typo becomes an ungated route."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    name = _decorator_name(node)
    short = name.rsplit('.', 1)[-1]
    value = getattr(user_accounts, short, None)
    return value if isinstance(value, str) else None


def _routes_in(path):
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    routes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        rule = None
        methods = set()
        names = []
        capability = None
        for dec in node.decorator_list:
            name = _decorator_name(dec)
            names.append(name)
            if name.endswith('.route') and isinstance(dec, ast.Call):
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    rule = dec.args[0].value
                methods = {'GET'}
                for kw in dec.keywords:
                    if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = {
                            e.value.upper() for e in kw.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
            elif name == 'mt_require_capability' and isinstance(dec, ast.Call) and dec.args:
                capability = _literal(dec.args[0])
        if rule is not None:
            routes.append(_Route(node.name, rule, methods, names, capability, node.lineno))
    return routes


ALL_ROUTES = _routes_in(RETAIL_API) + _routes_in(IMPORT_API)
MUTATING_ROUTES = [r for r in ALL_ROUTES if r.methods & MUTATING_METHODS]
READ_ROUTES = [r for r in ALL_ROUTES if not (r.methods & MUTATING_METHODS)]


# ── 1. The static guard ──────────────────────────────────────────────────────

def test_the_route_files_were_actually_parsed():
    """A regex/AST guard that silently matched nothing would pass every
    assertion below for the worst possible reason."""
    assert len(ALL_ROUTES) >= 80, ALL_ROUTES
    assert len(MUTATING_ROUTES) >= 40, MUTATING_ROUTES


def test_every_mutating_route_carries_an_explicit_capability():
    """THE guard. A new POST/PUT/PATCH/DELETE route with no
    @mt_require_capability fails here -- it cannot inherit blanket access by
    saying nothing."""
    ungated = sorted(
        f"{r.func} ({sorted(r.methods)} {r.rule}) at line {r.lineno}"
        for r in MUTATING_ROUTES if r.capability is None
    )
    assert not ungated, (
        "these mutating routes carry no @mt_require_capability and would "
        "inherit blanket retail access:\n  " + "\n  ".join(ungated)
    )


def test_every_declared_capability_is_one_of_the_eight_seeded_codes():
    """A typo'd or invented code is worse than no gate: every account is
    seeded with rows for exactly the eight codes, so 'retail.stock_adjust'
    would find no row, fail closed, and lock everyone including the people
    who should pass -- or, if the reader ever changed to fail open, gate
    nothing at all."""
    known = set(user_accounts.CAPABILITY_CODES)
    wrong = sorted(
        f"{r.func}: {r.capability!r}" for r in ALL_ROUTES
        if r.capability is not None and r.capability not in known
    )
    assert not wrong, f"not seeded capability codes (known: {sorted(known)}):\n  " + "\n  ".join(wrong)


def test_the_mutating_capability_matrix_matches_exactly():
    actual = {r.func: r.capability for r in MUTATING_ROUTES}
    assert actual == EXPECTED_MUTATION_CAPABILITIES, (
        "the route -> capability map changed. This table is the record of "
        "which authority each mutation needs; update it deliberately.\n"
        f"only in code:  {sorted(set(actual) - set(EXPECTED_MUTATION_CAPABILITIES))}\n"
        f"only in table: {sorted(set(EXPECTED_MUTATION_CAPABILITIES) - set(actual))}\n"
        f"disagree:      "
        f"{ {k: (actual[k], EXPECTED_MUTATION_CAPABILITIES[k]) for k in set(actual) & set(EXPECTED_MUTATION_CAPABILITIES) if actual[k] != EXPECTED_MUTATION_CAPABILITIES[k]} }"
    )


def test_the_gated_read_routes_match_exactly():
    actual = {r.func: r.capability for r in READ_ROUTES if r.capability is not None}
    assert actual == EXPECTED_READ_CAPABILITIES


def test_capability_is_checked_inside_the_login_check():
    """Decorator order matters. `@mt_login_required` is written above
    `@mt_require_capability`, so it runs first and an anonymous request gets
    401 rather than a 403 that would confirm the route exists and tell the
    caller which capability guards it."""
    wrong = []
    for r in ALL_ROUTES:
        if r.capability is None:
            continue
        if 'mt_login_required' not in r.decorators:
            wrong.append(f"{r.func}: capability gate with no mt_login_required")
            continue
        if r.decorators.index('mt_login_required') > r.decorators.index('mt_require_capability'):
            wrong.append(f"{r.func}: mt_require_capability is written above mt_login_required")
    assert not wrong, "\n  ".join(wrong)


def test_no_mutating_route_relies_on_the_blanket_subsystem_gate_alone():
    """The literal this whole slice replaces. `mt_require_subsystem('retail')`
    stays -- it is the LICENSE/module check -- but on a mutation it may never
    be the only authorization."""
    blanket_only = sorted(
        r.func for r in MUTATING_ROUTES
        if 'mt_require_subsystem' in r.decorators and r.capability is None
    )
    assert not blanket_only, blanket_only


# ── 2. Live behaviour ────────────────────────────────────────────────────────

def _make_user(role, *, company_id=None, capabilities=None):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    a fixture that hand-wrote its own grants could drift from what the product
    actually gives a cashier and quietly stop testing the real defaults."""
    email = f"cap-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "CapMatrixPW1"
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant, so
    # without this the request never reaches the capability check and every
    # assertion below would pass for the wrong reason.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id, user_id


def _seed_shop():
    """An admin plus one in-stock product, and a cashier and a manager in the
    SAME company so all three are arguing about the same rows."""
    admin, company_id, _ = _make_user('admin')
    admin.get('/api/sub/retail/settings/tax')  # runs _ensure_credit_schema / doc_sequences

    import random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    r = admin.post('/api/sub/retail/products', json={
        'name': 'Capability Test Product', 'sku': f'CAP-{uuid.uuid4().hex[:8]}',
        'sell_price': 100.0, 'tax_rate': 0, 'initial_stock': 100,
    })
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']
    return admin, company_id, product_id


@pytest.fixture(scope='module')
def shop():
    return _seed_shop()


def _sale(client, product_id, **extra):
    payload = {
        'items': [dict({'product_id': product_id, 'quantity': 1}, **extra)],
        'amount_paid': 1000, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    return client.post('/api/sub/retail/sales', json=payload)


# ── B1 fixtures: three ways to make a `payments` row, one per authority ──────
# void_payment's authority is decided by the FIELDS on the row it loads, not
# by the request, so these helpers create real rows through the real
# creating routes (never a raw INSERT) and hand back the numeric `payments.id`
# void_payment actually keys on -- neither customer_payment nor
# supplier_payment nor create_purchase_order's inline down-payment return
# that id in their response body, so it is read back by `reference`, the one
# value they do return and the column's own UNIQUE key.

def _payment_id(company_id, reference):
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id FROM payments WHERE company_id=? AND reference=?", (company_id, reference)
    ).fetchone()
    conn.close()
    assert row, f"no payments row for reference {reference!r}"
    return row['id']


def _payment_status(pid):
    """Read back what void_payment actually did (or refused to do) -- a 403
    or 409 that returns the right code but writes anyway is not a gate."""
    conn = get_retail_conn()
    row = conn.execute("SELECT status FROM payments WHERE id=?", (pid,)).fetchone()
    conn.close()
    return row['status'] if row else None


def _diagnose(pid, client, response):
    """Flakiness-hunt diagnostic: re-reads, at the moment of assertion, the
    exact facts the in-handler check decided on -- the payment row's real
    shape (not what the fixture believes it is) and the acting session's
    LIVE capability rows -- so a failure is diagnosed the first time it is
    caught rather than requiring reproduction. Deliberately bypasses
    session_has_capability/mt_auth entirely and re-derives the answer
    straight from user_permissions, so a divergence between what this
    prints and what the route decided is itself informative."""
    conn = get_retail_conn()
    prow = conn.execute(
        "SELECT id, company_id, party_type, party_id, related_type, status FROM payments WHERE id=?",
        (pid,)).fetchone()
    conn.close()
    with client.session_transaction() as sess:
        user_id = sess.get('mt_user_id')
        role = sess.get('mt_role')
        company_id_in_session = sess.get('company_id')
    caps = {}
    if user_id:
        rconn = registry_conn()
        caps = {
            r['subsystem']: r['access_level']
            for r in rconn.execute(
                "SELECT subsystem, access_level FROM user_permissions WHERE user_id=?", (user_id,)
            ).fetchall()
        }
        rconn.close()
    return {
        'response_status': response.status_code, 'response_body': response.get_json(),
        'payment_row': dict(prow) if prow else None,
        'session_user_id': user_id, 'session_role': role, 'session_company_id': company_id_in_session,
        'live_user_permissions': caps,
    }


class _CapabilityProbe:
    """A transparent, call-counting wrapper around the REAL
    session_has_capability -- delegates every call through unchanged, so
    swapping it in via monkeypatch cannot itself alter which requests pass
    or fail. Its only job is to answer 'was this function actually invoked,
    and with what code' -- the question a bare status-code assertion cannot
    answer, because a 403 is equally consistent with 'the check ran and
    refused' and 'the check never ran and something else refused it', and a
    200 is equally consistent with 'the check ran and allowed' and 'the
    check never ran and the request happened to be harmless anyway'.

    Same discipline as
    test_the_capability_gate_fails_closed_when_the_registry_cannot_be_read's
    call-counting stub around mt_auth._get_registry_conn -- a bare 401
    there could equally have meant the session was never established, so
    that test asserts the counter moved, not merely the status code.
    """
    def __init__(self, real):
        self._real = real
        self.calls = []

    def __call__(self, code):
        result = self._real(code)
        self.calls.append((code, result))
        return result


def _create_customer(client, **extra):
    payload = dict({'name': f'Cap Matrix Customer {uuid.uuid4().hex[:6]}'}, **extra)
    r = client.post('/api/sub/retail/customers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _create_supplier(client, **extra):
    payload = dict({'name': f'Cap Matrix Supplier {uuid.uuid4().hex[:6]}'}, **extra)
    r = client.post('/api/sub/retail/suppliers', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer_account_payment_id(admin, company_id):
    """A CUSTOMER-account receipt (party_type='customer', related_type=NULL)
    -- the authority create_customer/customer_payment already require is
    retail.sell, so voiding one is meant to stay on retail.refund."""
    cust_id = _create_customer(admin)
    r = admin.post(f'/api/sub/retail/customers/{cust_id}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    return _payment_id(company_id, r.get_json()['data']['reference'])


def _supplier_account_payment_id(admin, company_id):
    """A SUPPLIER-account payment (party_type='supplier', related_type=NULL)
    -- created behind CAP_EMPLOYEES at ~retail_api.py:3495, so voiding one
    must require the same code (B1)."""
    sup_id = _create_supplier(admin)
    r = admin.post(f'/api/sub/retail/suppliers/{sup_id}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    return _payment_id(company_id, r.get_json()['data']['reference'])


def _po_payment_id(admin, company_id, product_id):
    """A PO payment (party_type='supplier', related_type='po') -- posted
    inline by create_purchase_order's own `amount_paid` down-payment, the
    same code path pay_purchase_order uses, both behind CAP_EMPLOYEES."""
    sup_id = _create_supplier(admin)
    r = admin.post('/api/sub/retail/purchase-orders', json={
        'supplier_id': sup_id, 'amount_paid': 15,
        'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 15}],
    })
    assert r.status_code == 200, r.get_json()
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id FROM payments WHERE company_id=? AND party_id=? AND related_type='po' "
        "ORDER BY id DESC LIMIT 1", (company_id, sup_id)
    ).fetchone()
    conn.close()
    assert row, "create_purchase_order's amount_paid did not post a payments row"
    return row['id']


def _sale_receipt_payment_id(client, company_id, product_id):
    """A SALE receipt (related_type='sale') -- void_payment must go on
    refusing these (409) regardless of who is asking; see the long comment
    on the route about why a sale can only be reversed through /returns."""
    sale = _sale(client, product_id)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT id FROM payments WHERE company_id=? AND related_type='sale' AND related_id=? "
        "ORDER BY id DESC LIMIT 1", (company_id, sale_id)
    ).fetchone()
    conn.close()
    assert row, "create_sale did not post a related_type='sale' payments row"
    return row['id']


def test_a_cashier_can_ring_a_sale(shop):
    _admin, company_id, product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    assert _sale(cashier, product_id).status_code == 200


def test_a_cashier_cannot_adjust_stock(shop):
    """The headline of this whole slice: before capability gating, this
    returned 200."""
    _admin, company_id, product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    r = cashier.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                     json={'quantity': 500, 'reason': 'helping myself'})
    assert r.status_code == 403, r.get_json()

    # And it really did not happen -- a gate that returns 403 after writing
    # is not a gate.
    conn = get_retail_conn()
    on_hand = conn.execute(
        "SELECT COALESCE(SUM(quantity_on_hand),0) FROM inventory_balances WHERE company_id=? AND product_id=?",
        (company_id, product_id)).fetchone()[0]
    conn.close()
    assert on_hand <= 100


def test_a_cashier_cannot_create_or_edit_a_product(shop):
    _admin, company_id, _product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    assert cashier.post('/api/sub/retail/products', json={
        'name': 'Sneaky', 'sku': f'SNK-{uuid.uuid4().hex[:6]}', 'sell_price': 1,
    }).status_code == 403


def test_a_cashier_cannot_read_the_shops_numbers(shop):
    _admin, company_id, _product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    for url in ('/api/sub/retail/dashboard/stats',
                '/api/sub/retail/reports/summary',
                '/api/sub/retail/reports/daily-cash',
                '/api/sub/retail/customers/receivables',
                '/api/sub/retail/suppliers/payables'):
        assert cashier.get(url).status_code == 403, url


def test_a_cashier_can_still_do_the_job_the_till_exists_for(shop):
    """Least privilege must not mean a till that cannot sell. Browsing the
    catalogue, finding a customer and looking up a sale are not gated."""
    _admin, company_id, _product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    for url in ('/api/sub/retail/products',
                '/api/sub/retail/categories',
                '/api/sub/retail/customers',
                '/api/sub/retail/sales/recent',
                '/api/sub/retail/held-sales',
                '/api/sub/retail/branches',
                '/api/sub/retail/payment-methods',
                '/api/sub/retail/cash-sessions/current'):
        assert cashier.get(url).status_code == 200, url


def test_a_cashier_refunds_against_a_sale_but_cannot_invent_one(shop):
    """The owner's model: 'a cashier sells and takes refunds against a local
    sale'. create_return is sale-bound -- it 404s without a real sale of this
    company -- so the shrink lever a refund permission usually implies (money
    out with no sale behind it) does not exist on this route."""
    _admin, company_id, product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    sale = _sale(cashier, product_id)
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    ok = cashier.post('/api/sub/retail/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': product_id, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert ok.status_code == 200, ok.get_json()

    nowhere = cashier.post('/api/sub/retail/returns', json={
        'sale_id': 999999999, 'items': [{'product_id': product_id, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert nowhere.status_code == 404, nowhere.get_json()


def test_a_cashier_without_the_refund_capability_is_refused(shop):
    """The grant is per user, not per role -- an owner who does not want this
    cashier refunding turns the row off and the route follows."""
    _admin, company_id, product_id = shop
    seller, _, _ = _make_user('cashier', company_id=company_id)
    sale = _sale(seller, product_id)
    sale_id = sale.get_json()['data']['id']

    denied, _, _ = _make_user('cashier', company_id=company_id,
                              capabilities={'retail.refund': 'none'})
    r = denied.post('/api/sub/retail/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': product_id, 'quantity': 1}],
        'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 403, r.get_json()


def test_a_cashier_sells_at_full_price_but_cannot_discount(shop):
    """`retail.discount` has no route of its own -- a discount is a FIELD on
    the sale, so the check has to be inside create_sale or it is not a check
    at all. The undiscounted sale must still go through: refusing the sale
    outright would be a worse product than refusing the discount."""
    _admin, company_id, product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)

    assert _sale(cashier, product_id).status_code == 200
    denied = _sale(cashier, product_id, discount_pct=20)
    assert denied.status_code == 403, denied.get_json()

    conn = get_retail_conn()
    discounted = conn.execute(
        "SELECT COUNT(*) FROM sales WHERE company_id=? AND discount_amount > 0.005", (company_id,)
    ).fetchone()[0]
    conn.close()
    assert discounted == 0, "the refused discount was written anyway"


def test_a_manager_may_discount_and_adjust_stock_and_read_reports(shop):
    _admin, company_id, product_id = shop
    manager, _, _ = _make_user('manager', company_id=company_id)

    assert _sale(manager, product_id, discount_pct=20).status_code == 200
    assert manager.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                        json={'quantity': 5, 'reason': 'recount'}).status_code == 200
    assert manager.get('/api/sub/retail/reports/summary').status_code == 200


def test_a_manager_may_not_administer_the_shop(shop):
    """Settings, branches, payment methods and the shop's money going OUT are
    the owner's, not the manager's."""
    _admin, company_id, _product_id = shop
    manager, _, _ = _make_user('manager', company_id=company_id)
    assert manager.post('/api/sub/retail/settings/tax',
                        json={'tax_calculation_mode': 'before_discount'}).status_code == 403
    assert manager.post('/api/sub/retail/branches', json={'name': 'Sneaky Branch'}).status_code == 403
    assert manager.post('/api/sub/retail/payment-methods', json={'name': 'Sneaky'}).status_code == 403
    assert manager.post('/api/sub/retail/suppliers/999999/payments',
                        json={'amount': 10}).status_code == 403


def test_a_cashier_may_work_this_terminals_drawer(shop):
    """`retail.cash.close` is a cashier default on purpose: the person who
    counted the drawer is the person who closes it. Whether the VARIANCE that
    close records is then accepted is a separate authority
    (retail.cash.approve), which is exactly why the two are separate codes.

    Asserted as a PAIR -- one cashier with the code, one without -- because
    "not 403" on its own would also hold if the decorator were missing
    entirely. Only the contrast shows the gate is really there and really
    reads the grant. The permitted cashier is allowed 409 ("a session is
    already open for this branch"), which is this module-scoped shop's
    ordinary state and is emphatically not a refusal."""
    _admin, company_id, _product_id = shop
    allowed, _, _ = _make_user('cashier', company_id=company_id)
    opened = allowed.post('/api/sub/retail/cash-sessions/open', json={'opening_float': 50})
    assert opened.status_code in (200, 409), opened.get_json()

    denied, _, _ = _make_user('cashier', company_id=company_id,
                              capabilities={'retail.cash.close': 'none'})
    refused = denied.post('/api/sub/retail/cash-sessions/open', json={'opening_float': 50})
    assert refused.status_code == 403, refused.get_json()


def test_nobody_who_can_close_a_drawer_gets_variance_approval_by_default():
    """The self-approval bar, applied to the defaults rather than only to a
    route. A role that holds BOTH retail.cash.close and retail.cash.approve
    could count its own drawer and sign off its own shortfall; the only role
    that holds the approval by default is the owner, who holds everything."""
    for role in (user_accounts.ROLE_MANAGER, user_accounts.ROLE_CASHIER):
        caps = user_accounts.capabilities_for_role(role)
        assert not ({user_accounts.CAP_CASH_CLOSE, user_accounts.CAP_CASH_APPROVE} <= caps), role


def test_the_admin_bypass_is_unchanged(shop):
    """Design §3: 'Admin bypass stays.' The owner has no capability rows to
    depend on for this -- role='admin' passes every gate."""
    admin, company_id, product_id = shop
    conn = registry_conn()
    conn.execute(
        "UPDATE user_permissions SET access_level='none' WHERE user_id IN "
        "(SELECT id FROM users WHERE company_id=? AND role='admin') AND subsystem LIKE 'retail.%'",
        (company_id,))
    conn.commit()
    conn.close()

    assert admin.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                      json={'quantity': 1, 'reason': 'owner'}).status_code == 200
    assert admin.get('/api/sub/retail/reports/summary').status_code == 200


def test_an_unauthenticated_request_is_401_not_403(shop):
    """Order check, driven rather than read off the source: the capability
    gate must not answer before the login gate, or the 403 itself tells an
    anonymous caller the route is real."""
    _admin, _company_id, product_id = shop
    anon = app.test_client()
    assert anon.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                     json={'quantity': 1}).status_code == 401


def test_the_capability_gate_fails_closed_when_the_registry_cannot_be_read(shop, monkeypatch):
    """A check that cannot reach its facts has learned nothing, and nothing
    must refuse -- the same rule mt_login_required's fail-open fix applies,
    and deliberately NOT the `except Exception: pass` that
    mt_require_subsystem still has beside it."""
    from commercial_runtime.identity import mt_auth

    _admin, company_id, product_id = shop
    manager, _, _ = _make_user('manager', company_id=company_id)
    assert manager.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                        json={'quantity': 1, 'reason': 'before'}).status_code == 200

    calls = {'n': 0}
    real = mt_auth._get_registry_conn

    def exploding():
        calls['n'] += 1
        raise sqlite3.OperationalError('database is locked')

    # mt_login_required runs FIRST and uses the same helper, so let its lookup
    # through and only break the capability check's own read -- otherwise this
    # would prove nothing about the capability gate.
    state = {'seen': 0}

    def selective():
        state['seen'] += 1
        if state['seen'] == 1:
            return real()
        return exploding()

    monkeypatch.setattr(mt_auth, '_get_registry_conn', selective)
    r = manager.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                     json={'quantity': 1, 'reason': 'during'})
    monkeypatch.undo()

    assert calls['n'] >= 1, "the capability check never read the registry -- this proves nothing"
    assert r.status_code == 403, r.get_json()


def test_a_missing_capability_row_is_a_denial_not_a_pass(shop):
    """Every migrated and newly created account gets a row per code, so a
    MISSING row means somebody wrote a user by hand -- and an authorization
    check must not read silence as consent."""
    _admin, company_id, product_id = shop
    email = f"cap-bare-{uuid.uuid4().hex[:8]}@test.local"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password("CapMatrixPW1"),
         "cashier", "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    client.post('/api/auth/login', json={'email': email, 'password': "CapMatrixPW1"})
    assert _sale(client, product_id).status_code == 403


def test_the_refusal_message_is_a_translation_catalog_key(shop):
    """This product ships Arabic and is RTL. A refusal the frontend cannot
    translate renders as raw English inside an RTL layout, so the exact string
    the route returns has to exist in BOTH catalogs."""
    import json
    _admin, company_id, product_id = shop
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    body = cashier.post(f'/api/sub/retail/products/{product_id}/stock-adjust',
                        json={'quantity': 1}).get_json()
    message = body.get('message') or body.get('error')

    locales = PRODUCT_DIR / 'frontend' / 'locales'
    en = json.loads((locales / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((locales / 'ar.json').read_text(encoding='utf-8'))
    assert message in en, f"{message!r} is not an English catalog key"
    assert message in ar, f"{message!r} is not an Arabic catalog key"
    assert ar[message] != message, "the Arabic entry is still the English string"


# ── B1: void_payment's authority must match the creating route's authority ──
#
# CREATING a supplier or PO payment requires CAP_EMPLOYEES (owner authority,
# ~retail_api.py:3495/3519 -- receiving a delivery and paying for it are
# deliberately different authorities). Before this fix, VOIDING any of the
# three payment shapes this route accepts required only CAP_REFUND -- a
# cashier default -- so a cashier who could never create a supplier payment
# could still reverse one and reopen the payable. That is owner money, not
# till money. Voiding a CUSTOMER-account payment stays on CAP_REFUND,
# symmetric with customer_payment's CAP_SELL.

def test_the_three_roles_used_by_the_void_split_actually_differ():
    """The tests below derive their expected status codes from
    capabilities_for_role() instead of a second hardcoded copy of the
    matrix (that copy going stale silently is exactly what broke a previous
    pass of this suite -- see the module docstring). That derivation only
    proves something if cashier/manager/admin are not all the same set --
    otherwise 'expected = CAP_EMPLOYEES in capabilities_for_role(role)' would
    happily keep passing even if the split were deleted tomorrow. This is
    the guard: it fails, loudly and on its own, the day that stops being
    true."""
    cashier_caps = user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER)
    manager_caps = user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)
    admin_caps = user_accounts.capabilities_for_role(user_accounts.ROLE_ADMIN)
    assert len({frozenset(cashier_caps), frozenset(manager_caps), frozenset(admin_caps)}) == 3, (
        "cashier/manager/admin collapsed to fewer than three distinct capability sets -- "
        "every expectation in the void_payment authority tests is derived from these sets "
        "and would now pass vacuously"
    )
    assert user_accounts.CAP_EMPLOYEES not in cashier_caps
    assert user_accounts.CAP_EMPLOYEES not in manager_caps
    assert user_accounts.CAP_EMPLOYEES in admin_caps
    assert user_accounts.CAP_REFUND in cashier_caps, "the customer-account void case below assumes this"


def test_void_payment_authority_matches_the_creating_routes_authority(shop):
    """The headline of this fix: before it, every assertion below expecting
    403 returned 200 instead -- a cashier successfully voided a supplier
    payment and a PO payment through this exact route."""
    admin, company_id, product_id = shop
    cashier_can_employ = user_accounts.CAP_EMPLOYEES in user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER)
    manager_can_employ = user_accounts.CAP_EMPLOYEES in user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)

    # cashier voids a customer-account payment -> allowed. This is the case
    # B1 must NOT break: it is symmetric with customer_payment's CAP_SELL,
    # a till-level correction, and stays on CAP_REFUND.
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    pid = _customer_account_payment_id(admin, company_id)
    r = cashier.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'till correction'})
    assert r.status_code == 200, r.get_json()
    assert _payment_status(pid) == 'voided'

    # cashier voids a supplier-account payment -> 403
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    pid = _supplier_account_payment_id(admin, company_id)
    r = cashier.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'sneaky'})
    expected = 200 if cashier_can_employ else 403
    assert r.status_code == expected, _diagnose(pid, cashier, r)
    assert _payment_status(pid) == ('voided' if expected == 200 else 'active')

    # cashier voids a PO payment -> 403
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    pid = _po_payment_id(admin, company_id, product_id)
    r = cashier.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'sneaky'})
    expected = 200 if cashier_can_employ else 403
    assert r.status_code == expected, _diagnose(pid, cashier, r)
    assert _payment_status(pid) == ('voided' if expected == 200 else 'active')

    # manager voids a supplier payment -> 403 (manager lacks retail.employees,
    # same as it lacks retail.cash.approve -- owner-only is owner-only)
    manager, _, _ = _make_user('manager', company_id=company_id)
    pid = _supplier_account_payment_id(admin, company_id)
    r = manager.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'sneaky'})
    expected = 200 if manager_can_employ else 403
    assert r.status_code == expected, _diagnose(pid, manager, r)
    assert _payment_status(pid) == ('voided' if expected == 200 else 'active')

    # manager voids a PO payment -> 403, same reasoning
    manager, _, _ = _make_user('manager', company_id=company_id)
    pid = _po_payment_id(admin, company_id, product_id)
    r = manager.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'sneaky'})
    expected = 200 if manager_can_employ else 403
    assert r.status_code == expected, _diagnose(pid, manager, r)
    assert _payment_status(pid) == ('voided' if expected == 200 else 'active')

    # admin/owner voids all three -- unchanged, the role bypass this route
    # (and the new in-handler check) have always had
    for maker in (
        lambda: _customer_account_payment_id(admin, company_id),
        lambda: _supplier_account_payment_id(admin, company_id),
        lambda: _po_payment_id(admin, company_id, product_id),
    ):
        pid = maker()
        r = admin.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'owner correction'})
        assert r.status_code == 200, r.get_json()
        assert _payment_status(pid) == 'voided'


def test_the_sale_receipt_refusal_survives_the_authority_split(shop):
    """B1 only adds a party_type check for payments that get PAST the
    pre-existing 'this receipt belongs to a sale' guard -- a sale receipt
    must still be refused with 409 before that check is ever reached, for
    every role, exactly as it was before this fix."""
    admin, company_id, product_id = shop
    for role, make_actor in (
        ('cashier', lambda: _make_user('cashier', company_id=company_id)[0]),
        ('manager', lambda: _make_user('manager', company_id=company_id)[0]),
        ('admin', lambda: admin),
    ):
        actor = make_actor()
        pid = _sale_receipt_payment_id(actor, company_id, product_id)
        r = actor.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'nope'})
        assert r.status_code == 409, (role, r.get_json())
        assert _payment_status(pid) == 'active'


# ── REACHABILITY, not outcome ────────────────────────────────────────────────
#
# Everything above this line asserts what status code came back. That proves
# the ROUTE behaves correctly, but it cannot by itself distinguish "the
# CAP_EMPLOYEES check ran and made the decision" from two failure shapes that
# would look identical on a status-code-only assertion:
#
#   * the check never ran, but some OTHER guard happened to also return 403
#     for an unrelated reason -- the "right code, wrong mechanism" case;
#   * the check never ran, and the request happened to be harmless anyway --
#     the "right code, no gate at all" case, which is exactly how the
#     ORIGINAL bug this fix closes went unnoticed: void_payment always
#     returned SOME status code, it was just the wrong one for the wrong
#     reason, and nothing was watching which code path produced it.
#
# The two tests below make the check's execution observable and assert
# THAT, on top of (not instead of) the outcome. Same discipline as
# test_the_capability_gate_fails_closed_when_the_registry_cannot_be_read's
# call-counting stub around mt_auth._get_registry_conn.

def test_void_payment_authority_check_is_actually_consulted(shop, monkeypatch):
    """Wraps the REAL session_has_capability transparently (pass-through,
    behaviour unchanged) and asserts it was genuinely invoked with
    retail.employees whenever the payment being voided is party_type=
    'supplier' -- for every role, INCLUDING admin, whose eventual True
    comes from INSIDE session_has_capability's own role bypass, not from
    retail_api skipping the call. For a customer-account payment the
    check must NOT be called at all (party_type != 'supplier' short-
    circuits it) -- verified against the payment row's actual party_type
    read fresh from the database, not inferred from the response, so a
    fixture that quietly stopped creating what it claims to create would
    fail this test rather than pass it vacuously."""
    admin, company_id, product_id = shop
    probe = _CapabilityProbe(retail_api_module.session_has_capability)
    monkeypatch.setattr(retail_api_module, 'session_has_capability', probe)

    def _void(actor, pid, reason):
        probe.calls.clear()
        r = actor.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': reason})
        return r, list(probe.calls)

    for role, make_actor in (
        ('cashier', lambda: _make_user('cashier', company_id=company_id)[0]),
        ('manager', lambda: _make_user('manager', company_id=company_id)[0]),
        ('admin', lambda: admin),
    ):
        # supplier-account payment -- the check MUST be consulted
        actor = make_actor()
        pid = _supplier_account_payment_id(admin, company_id)
        r, calls = _void(actor, pid, 'reachability: supplier')
        assert calls, (role, 'supplier-account', 'session_has_capability(retail.employees) was never called',
                        r.get_json())
        assert all(code == user_accounts.CAP_EMPLOYEES for code, _verdict in calls), (role, calls)

        # PO payment -- same authority, same requirement
        actor = make_actor()
        pid = _po_payment_id(admin, company_id, product_id)
        r, calls = _void(actor, pid, 'reachability: po')
        assert calls, (role, 'po', 'session_has_capability(retail.employees) was never called', r.get_json())
        assert all(code == user_accounts.CAP_EMPLOYEES for code, _verdict in calls), (role, calls)

        # customer-account payment -- ground-truthed against the real row,
        # then the check must NOT fire at all (party_type short-circuits it)
        actor = make_actor()
        pid = _customer_account_payment_id(admin, company_id)
        conn = get_retail_conn()
        real_party_type = conn.execute("SELECT party_type FROM payments WHERE id=?", (pid,)).fetchone()['party_type']
        conn.close()
        assert real_party_type == 'customer', "fixture drifted -- this case no longer proves what it claims to"
        r, calls = _void(actor, pid, 'reachability: customer')
        assert not calls, (role, 'customer-account',
                            'session_has_capability was called for a customer-account payment -- '
                            'the party_type branch is not doing what it claims', r.get_json())


def test_void_payment_authority_outcome_tracks_the_live_capability_verdict(shop, monkeypatch):
    """Stronger than reachability: proves the response is actually DRIVEN
    by session_has_capability's return value, for every role including
    admin -- which rules out a second, duplicated admin exemption sitting
    next to the real check (e.g. an accidental `or session.get('mt_role')
    == 'admin'` written directly into void_payment). Admin's normal pass
    has exactly one source in this codebase: the role check inside
    session_has_capability itself. Forcing that function to always deny
    must therefore refuse admin too, at this specific call site, when
    voiding a supplier payment -- and forcing it to always grant must let
    even a bare cashier through."""
    admin, company_id, _product_id = shop

    def _always(value):
        def stub(code):
            return value
        return stub

    def _roles():
        return (
            ('cashier', _make_user('cashier', company_id=company_id)[0]),
            ('manager', _make_user('manager', company_id=company_id)[0]),
            ('admin', admin),
        )

    monkeypatch.setattr(retail_api_module, 'session_has_capability', _always(True))
    for role, actor in _roles():
        pid = _supplier_account_payment_id(admin, company_id)
        r = actor.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'forced-grant probe'})
        assert r.status_code == 200, (role, 'forced True', r.get_json())
    monkeypatch.undo()

    monkeypatch.setattr(retail_api_module, 'session_has_capability', _always(False))
    for role, actor in _roles():
        pid = _supplier_account_payment_id(admin, company_id)
        r = actor.post(f'/api/sub/retail/payments/{pid}/void', json={'reason': 'forced-deny probe'})
        assert r.status_code == 403, (role, 'forced False -- including admin: no hardcoded bypass at this '
                                       'call site is allowed to exist', r.get_json())


# ── Fix 1: create_purchase_order's amount_paid is a weaker second path to a ──
#     supplier payment (same class of bug as B1's void_payment split)
#
# supplier_payment (~3497) and pay_purchase_order (~3521) both require
# CAP_EMPLOYEES to move money OUT to a supplier. create_purchase_order itself
# is gated only CAP_STOCK_ADJUST -- correct for ORDERING stock, a manager-
# level action -- but its own `amount_paid` field posts that exact same
# money-out payment (~1284) under that weaker gate. A manager, refused by
# both dedicated payment routes, could pay a supplier by typing a number
# into a brand-new PO instead.

def _po_count(company_id):
    conn = get_retail_conn()
    n = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE company_id=?", (company_id,)).fetchone()[0]
    conn.close()
    return n


def _po_payload(product_id, supplier_id, amount_paid):
    payload = {'supplier_id': supplier_id, 'items': [{'product_id': product_id, 'quantity': 1, 'unit_cost': 15}]}
    if amount_paid is not None:
        payload['amount_paid'] = amount_paid
    return payload


def test_create_purchase_order_payment_authority_matches_the_dedicated_payment_routes(shop):
    """The headline of this fix: before it, every assertion below expecting
    403 for a non-zero amount_paid returned 200 instead, with a real
    payments row recorded -- a manager paid a supplier by embedding
    amount_paid in a new PO. Expected outcomes are derived from
    capabilities_for_role() (both CAP_STOCK_ADJUST, the route's own
    decorator floor, and CAP_EMPLOYEES, the new in-handler gate) rather than
    hardcoded, so this tracks the real seeded defaults; the existing guard
    test (test_the_three_roles_used_by_the_void_split_actually_differ)
    already pins that cashier/manager/admin do not collapse together."""
    admin, company_id, product_id = shop

    def _expected(caps, amount_paid):
        if user_accounts.CAP_STOCK_ADJUST not in caps:
            return 403
        if (amount_paid or 0) > 0.005 and user_accounts.CAP_EMPLOYEES not in caps:
            return 403
        return 200

    for role, caps in (
        ('cashier', user_accounts.capabilities_for_role(user_accounts.ROLE_CASHIER)),
        ('manager', user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)),
        ('admin', user_accounts.capabilities_for_role(user_accounts.ROLE_ADMIN)),
    ):
        for amount_paid in (0, 20):
            actor = admin if role == 'admin' else _make_user(role, company_id=company_id)[0]
            sup_id = _create_supplier(admin)
            before = _po_count(company_id)
            r = actor.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, amount_paid))
            expected = _expected(caps, amount_paid)
            assert r.status_code == expected, (role, amount_paid, r.get_json())
            after = _po_count(company_id)
            if expected == 200:
                assert after == before + 1, (role, amount_paid, "PO should have been created")
                if amount_paid and amount_paid > 0.005:
                    conn = get_retail_conn()
                    row = conn.execute(
                        "SELECT id FROM payments WHERE company_id=? AND party_id=? AND related_type='po' "
                        "ORDER BY id DESC LIMIT 1", (company_id, sup_id)).fetchone()
                    conn.close()
                    assert row, (role, amount_paid, "the down-payment should have been recorded")
            else:
                assert after == before, (role, amount_paid, "a refused request must not create the PO at all -- "
                                          "the PO and its payment component are refused TOGETHER, not partially")


def test_create_purchase_order_payment_authority_resists_amount_paid_evasions(shop):
    """The gate re-uses the SAME _money() coercion and SAME > 0.005
    threshold the real payment write uses (one local variable, computed
    once), so no numeric spelling of a real payment can reach
    _record_payment without first passing through here."""
    admin, company_id, product_id = shop
    manager_can_pay = user_accounts.CAP_EMPLOYEES in user_accounts.capabilities_for_role(user_accounts.ROLE_MANAGER)

    # absent amount_paid -> allowed regardless (nothing paid out)
    manager, _, _ = _make_user('manager', company_id=company_id)
    sup_id = _create_supplier(admin)
    r = manager.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, None))
    assert r.status_code == 200, r.get_json()

    # zero -> allowed
    manager, _, _ = _make_user('manager', company_id=company_id)
    sup_id = _create_supplier(admin)
    r = manager.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, 0))
    assert r.status_code == 200, r.get_json()

    # negative -> allowed. It never posted a payment before this fix either
    # (the pre-existing `if amount_paid > 0.005:` guard already excludes
    # it), so refusing it here would be refusing a case that pays nothing.
    manager, _, _ = _make_user('manager', company_id=company_id)
    sup_id = _create_supplier(admin)
    r = manager.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, -50))
    assert r.status_code == 200, r.get_json()

    # a numeric STRING that coerces to a real amount -> refused exactly like
    # the float form (unless the role actually holds CAP_EMPLOYEES)
    manager, _, _ = _make_user('manager', company_id=company_id)
    sup_id = _create_supplier(admin)
    before = _po_count(company_id)
    r = manager.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, '20'))
    expected = 200 if manager_can_pay else 403
    assert r.status_code == expected, r.get_json()
    assert _po_count(company_id) == (before + 1 if expected == 200 else before)

    # a float that rounds UP past the 0.005 threshold under _money()'s
    # ROUND_HALF_UP quantize (0.006 -> 0.01) -> refused, not waved through
    # as "basically zero"
    manager, _, _ = _make_user('manager', company_id=company_id)
    sup_id = _create_supplier(admin)
    before = _po_count(company_id)
    r = manager.post('/api/sub/retail/purchase-orders', json=_po_payload(product_id, sup_id, 0.006))
    expected = 200 if manager_can_pay else 403
    assert r.status_code == expected, r.get_json()
    assert _po_count(company_id) == (before + 1 if expected == 200 else before)


# ── Fix 2: import_api.py carries no mt_require_subsystem gate ────────────────
#
# Every route in retail_api.py carries BOTH mt_require_subsystem (the
# licence/module check) and a capability gate (the permission check). The
# five import_api.py POSTs carry only the capability gate -- confirmed by
# grep returning zero matches for mt_require_subsystem in that file, not
# even an import.

IMPORT_POST_ROUTES = (
    '/api/import/parse', '/api/import/detect', '/api/import/smart-execute',
    '/api/import/clean', '/api/import/execute',
)


def _make_user_without_retail_module(role, *, company_id):
    """Same shape as _make_user, but WITHOUT the legacy retail subsystem
    grant every other _make_user call seeds -- the exact account shape
    mt_require_subsystem('retail') exists to refuse. role='manager' (not
    'admin', which bypasses this specific check; not 'cashier', which would
    also be refused by the capability gate and so would not isolate which
    gate actually fired)."""
    email = f"cap-nosubsys-{uuid.uuid4().hex[:10]}@test.local"
    password = "CapMatrixPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


def test_import_routes_now_carry_the_subsystem_gate(shop):
    """A manager -- HAS retail.stock.adjust, so the capability gate alone
    would let them through -- but built WITHOUT the legacy retail subsystem
    row, must be refused on all five import POSTs. Before this fix these
    five routes had no such gate at all, so this account shape sailed
    through every one of them."""
    _admin, company_id, _product_id = shop
    denied = _make_user_without_retail_module('manager', company_id=company_id)
    for path in IMPORT_POST_ROUTES:
        r = denied.post(path)
        assert r.status_code == 403, (path, r.get_json())


def test_import_routes_unaffected_for_a_normal_session(shop):
    """The new gate must not regress a session that DOES carry the
    subsystem grant. Proven by reaching the HANDLER's own 400 ("No file
    uploaded") rather than being stopped by either gate -- for admin
    (bypasses both), a manager (has the subsystem row AND
    retail.stock.adjust), and a cashier (has the subsystem row but lacks
    retail.stock.adjust, so is correctly refused by the capability gate,
    unaffected by this fix)."""
    admin, company_id, _product_id = shop
    manager, _, _ = _make_user('manager', company_id=company_id)
    cashier, _, _ = _make_user('cashier', company_id=company_id)
    for path in IMPORT_POST_ROUTES:
        r = admin.post(path)
        assert r.status_code == 400, (path, r.get_json())
        r = manager.post(path)
        assert r.status_code == 400, (path, r.get_json())
        r = cashier.post(path)
        assert r.status_code == 403, (path, r.get_json())


def test_import_routes_are_blocked_under_a_restricted_license(shop, monkeypatch):
    """RETAIL_RESTRICTED_ALLOWLIST's own reasoning (retail_api.py ~70-78):
    'new products/suppliers/customers, stock adjustment... is blocked'
    under a restricted licence. execute/smart-execute rewrite the whole
    catalogue and opening stock -- squarely in that bucket -- yet had NO
    licence-state gate at all before this fix. Monkeypatches
    LicenseStateRepository.load (module-global, matching this file's
    existing pattern for mt_auth's registry-read failure test) rather than
    touching the real licensing.db this whole module's fixtures share, so
    no other test's license state is disturbed."""
    from commercial_runtime.licensing_contracts import flask_guard
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord

    admin, _company_id, _product_id = shop

    def fake_load(self):
        return LicenseStateRecord(
            licensing_schema_version=1, product_code='AURA_RETAIL', platform='WINDOWS',
            current_state='RESTRICTED',
        )

    monkeypatch.setattr(flask_guard.LicenseStateRepository, 'load', fake_load)
    for path in IMPORT_POST_ROUTES:
        r = admin.post(path)
        body = r.get_json()
        assert r.status_code == 403, (path, body)
        assert body.get('message') == 'This action is not available in the current licensing state.', (path, body)
    monkeypatch.undo()

    # restored: the handler is reachable again
    r = admin.post(IMPORT_POST_ROUTES[0])
    assert r.status_code == 400, r.get_json()
