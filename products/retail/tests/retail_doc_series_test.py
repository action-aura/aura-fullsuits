"""
Aura Retail -- per-document-type numbering series, schema v34 (ROADMAP.md's
2026-09-15 "schema versions v32-v35 RESERVED" entry, v34). Allocation,
gaplessness and the legacy allow-half. See
`retail_doc_series_migration_test.py` for the schema layer,
`retail_doc_series_einvoice_firewall_test.py` for the e-invoicing firewall,
`retail_doc_series_sync_test.py` for emit/apply, and
`retail_doc_series_device_isolation_test.py` for the cross-device collision
property.

Two layers, deliberately kept separate:

  A. PURE MODULE TESTS (`core.retail.doc_series.allocate`/`resolve_series`)
     against a bare hand-built sqlite database carrying only the two
     tables this module touches -- no Flask, no registry, no license seed.
     Fast, and what proves the seed arithmetic and pad_width by VALUE.

  B. ROUTE-LEVEL TESTS through the real Flask app (the
     `retail_po_number_uniqueness_test.py` bootstrap shape) -- what
     proves gaplessness under a real rollback, the allow-half's exact
     byte-for-byte legacy format, and `create_doc_series`'s own
     containment. `at_terminal()` is copied verbatim from
     `retail_drawer_terminal_scope_test.py`'s identical helper (its own
     docstring: "Patches the ONE function the product uses to answer
     'which terminal am I'... Nothing else is faked").

MUTATION PROOFS (run by hand while writing this file; both directions
below were confirmed, then reverted):
  * seeded `last_no = start_no` instead of `start_no - 1` in `allocate` ->
    `test_start_no_is_the_first_documents_number` failed (first number was
    `A-000501`, not `A-000500`) -- RED. Reverted -> GREEN.
  * removed the seed INSERT entirely (the submitted design's own
    `allocate`) -> the same test's first number was `A-000001` -- RED.
    Restored -> GREEN. This is the exact defect C2 named as "a test that
    could not be written against the implementation described".
  * hardcoded `:06d` in `allocate` instead of `{series_row['pad_width']}`
    -> `test_pad_width_is_honoured` failed (`A-000500` instead of
    `A-0500`) -- RED. Reverted -> GREEN.
  * removed `_device_doc_discriminator()` from `create_sale`'s `else`
    branch -> `test_with_no_series_configured_sale_number_is_byte_
    identical_to_legacy` failed its regex -- RED. Restored -> GREEN.
  * monkeypatched `retail_api._record_payment`/`_queue_sync_event` to
    raise `sqlite3.IntegrityError` (see each rollback test's own docstring)
    -> without the `try/except/rollback` wrapping each mint site already
    has, the assertion that the NEXT document still gets the first number
    failed because the aborted one had already consumed it -- RED, then
    confirmed GREEN against the real (already-correct) routes.

Run (ONE process per file -- AUDIT-010):
    C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_doc_series_test.py -v
"""
import contextlib
import os
import re
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_doc_series_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from api import retail_api  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import doc_series  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


TILL_A = '8f0c4a2e-1111-4c3a-9d55-aaaaaaaa1234'


@contextlib.contextmanager
def at_terminal(terminal_id):
    """Copied verbatim from retail_drawer_terminal_scope_test.py's own
    `at_terminal` -- see that file's docstring for the full reasoning.
    Not imported: this test directory's own stated convention is that each
    file survives independently (see e.g. `_accept_device.py`'s module
    docstring for the identical "copied rather than imported" choice)."""
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api.local_terminal_id = previous


def _make_admin_client(prefix):
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'DocSeriesPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c, company_id


@pytest.fixture
def client_and_company():
    return _make_admin_client('docseries')


def _seed_product(c, **kw):
    payload = {
        'name': 'Doc Series Test Widget', 'sku': f'DOCSER-{uuid.uuid4().hex[:8]}',
        'cost_price': 5.0, 'sell_price': 10.0, 'initial_stock': 1000,
    }
    payload.update(kw)
    r = c.post('/api/sub/retail/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell(c, pid, **kw):
    payload = {'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash',
               'amount_paid': 999999, 'idempotency_key': str(uuid.uuid4())}
    payload.update(kw)
    return c.post('/api/sub/retail/sales', json=payload)


def _seed_series(cid, doc_type, code, *, allocator_terminal_uid=None, pad_width=6, start_no=1,
                  status='active'):
    """Directly inserts a `doc_series` row -- bypassing `create_doc_series`
    on purpose for the tests in this file that are about ALLOCATION, not
    about the create route's own validation (that half is covered by
    `test_create_doc_series_*` below and by the migration test's index
    proofs).

    `idx_doc_series_code` is UNIQUE on `(doc_type, code)` WITH NO
    `company_id` -- deliberately (see database/schema.py's own comment: two
    companies on one shared install both coding a sale book 'A' would
    collide inside the SAME `sales.sale_number` column, mid-sale). This
    whole test FILE shares one retail.db across every test function, so
    every test below uses its own distinct `code`, never 'A' twice for the
    same `doc_type` -- reusing one would raise a real, uncaught
    `sqlite3.IntegrityError` here and, worse, leak this connection (see
    ENGINEERING.md's own "a leaked connection wedges every later write"
    lesson, which is exactly what happened the first time this file was
    run with a shared code and cascaded into nine unrelated failures
    downstream, all reading 'database is locked').
    """
    series_id = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO doc_series (id, company_id, doc_type, code, label, allocator_terminal_uid, "
            "pad_width, start_no, status, row_version) VALUES (?,?,?,?,?,?,?,?,?,1)",
            (series_id, cid, doc_type, code, code, allocator_terminal_uid, pad_width, start_no, status),
        )
        conn.commit()
    finally:
        conn.close()
    return series_id


# ═════════════════════════════════════════════════════════════════════════
# A. Pure module tests -- core.retail.doc_series.allocate/resolve_series
# ═════════════════════════════════════════════════════════════════════════

def _bare_conn():
    """A sqlite database carrying ONLY doc_series/doc_series_counter --
    no Flask, no registry, no license. Fast, and proves `allocate`/
    `resolve_series` are genuinely standalone functions with no hidden
    dependency on the rest of the schema."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE doc_series (
            id TEXT PRIMARY KEY, company_id INTEGER NOT NULL, doc_type TEXT NOT NULL,
            code TEXT NOT NULL, label TEXT NOT NULL, branch_uid TEXT,
            allocator_terminal_uid TEXT, claimed_at_utc TEXT,
            pad_width INTEGER NOT NULL DEFAULT 6, start_no INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active', created_at TEXT, updated_at_utc TEXT,
            row_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE doc_series_counter (series_id TEXT PRIMARY KEY, last_no INTEGER NOT NULL DEFAULT 0);
    """)
    return conn


def _insert_bare_series(conn, **kw):
    row = {'id': str(uuid.uuid4()), 'company_id': 1, 'doc_type': 'sale', 'code': 'A', 'label': 'A',
           'branch_uid': None, 'allocator_terminal_uid': TILL_A, 'claimed_at_utc': None,
           'pad_width': 6, 'start_no': 1, 'status': 'active', 'created_at': '2026-01-01', 'row_version': 1}
    row.update(kw)
    conn.execute(
        "INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, "
        "allocator_terminal_uid, claimed_at_utc, pad_width, start_no, status, created_at, row_version) "
        "VALUES (:id,:company_id,:doc_type,:code,:label,:branch_uid,:allocator_terminal_uid,"
        ":claimed_at_utc,:pad_width,:start_no,:status,:created_at,:row_version)", row,
    )
    conn.commit()
    return conn.execute("SELECT * FROM doc_series WHERE id=?", (row['id'],)).fetchone()


def test_contiguity_three_allocations_are_sequential():
    conn = _bare_conn()
    row = _insert_bare_series(conn)
    assert doc_series.allocate(conn, row) == 'A-000001'
    assert doc_series.allocate(conn, row) == 'A-000002'
    assert doc_series.allocate(conn, row) == 'A-000003'


def test_start_no_is_the_first_documents_number():
    """C2's own pin, by VALUE: `start_no=500` mints `A-000500` FIRST, not
    `A-000501` and not `A-000001`. See this file's module docstring for
    both mutation directions confirmed against this exact assertion."""
    conn = _bare_conn()
    row = _insert_bare_series(conn, start_no=500)
    assert doc_series.allocate(conn, row) == 'A-000500'
    assert doc_series.allocate(conn, row) == 'A-000501'


def test_default_start_no_of_one_mints_a_000001_first():
    conn = _bare_conn()
    row = _insert_bare_series(conn, start_no=1)
    assert doc_series.allocate(conn, row) == 'A-000001'


def test_pad_width_is_honoured():
    conn = _bare_conn()
    row = _insert_bare_series(conn, start_no=500, pad_width=4)
    assert doc_series.allocate(conn, row) == 'A-0500'


def test_peek_next_no_does_not_consume_a_number():
    conn = _bare_conn()
    row = _insert_bare_series(conn, start_no=1)
    assert doc_series.peek_next_no(conn, row) == 'A-000001'
    assert doc_series.peek_next_no(conn, row) == 'A-000001', "peek must be idempotent"
    assert doc_series.allocate(conn, row) == 'A-000001'
    assert doc_series.peek_next_no(conn, row) == 'A-000002'


def test_resolve_series_never_matches_a_none_terminal_uid():
    """Short-circuits to None BEFORE querying -- a till with no established
    identity must never be handed ANY book, unclaimed or otherwise. MUT:
    replace the short-circuit with a query that matches
    `allocator_terminal_uid IS NULL` when `terminal_uid` is falsy -> this
    test goes RED (it would return the unclaimed row inserted below)."""
    conn = _bare_conn()
    _insert_bare_series(conn, allocator_terminal_uid=None)  # unclaimed
    assert doc_series.resolve_series(conn, 1, 'sale', None) is None
    assert doc_series.resolve_series(conn, 1, 'sale', '') is None


def test_resolve_series_only_returns_this_terminals_own_book():
    conn = _bare_conn()
    mine = _insert_bare_series(conn, allocator_terminal_uid=TILL_A)
    _insert_bare_series(conn, allocator_terminal_uid='some-other-till', code='B')
    got = doc_series.resolve_series(conn, 1, 'sale', TILL_A)
    assert got['id'] == mine['id']


def test_resolve_series_ignores_a_retired_book():
    conn = _bare_conn()
    _insert_bare_series(conn, allocator_terminal_uid=TILL_A, status='retired')
    assert doc_series.resolve_series(conn, 1, 'sale', TILL_A) is None


# ═════════════════════════════════════════════════════════════════════════
# B. Route-level: the allow-half (no series configured -> legacy path)
# ═════════════════════════════════════════════════════════════════════════

_LEGACY_SALE_RE = re.compile(r'^SALE-\d{6}-[0-9a-f]{8}-[0-9a-f]{8}$')
_LEGACY_RETURN_RE = re.compile(r'^RET-\d{6}-[0-9a-f]{8}-[0-9a-f]{8}$')
_LEGACY_PO_RE = re.compile(r'^PO-\d{6}-[0-9a-f]{8}-[0-9a-f]{8}$')


def test_with_no_series_configured_sale_number_is_byte_identical_to_legacy(client_and_company):
    """THE ALLOW-HALF -- the half that gets skipped by default. An install
    that never configures a series must mint the EXACT SAME string it
    minted yesterday: this is what stops AUDIT-032B's fragments from ever
    being silently dropped for an install that has not opted in."""
    c, _cid = client_and_company
    pid = _seed_product(c)
    with at_terminal(TILL_A):
        r = _sell(c, pid)
    assert r.status_code == 200, r.get_json()
    assert _LEGACY_SALE_RE.match(r.get_json()['data']['sale_number']), r.get_json()


def test_with_no_series_configured_return_number_is_byte_identical_to_legacy(client_and_company):
    c, _cid = client_and_company
    pid = _seed_product(c)
    with at_terminal(TILL_A):
        sale = _sell(c, pid, items=[{'product_id': pid, 'quantity': 2}])
        assert sale.status_code == 200, sale.get_json()
        sale_id = sale.get_json()['data']['id']
        ret = c.post('/api/sub/retail/returns', json={
            'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
        })
    assert ret.status_code == 200, ret.get_json()
    assert _LEGACY_RETURN_RE.match(ret.get_json()['data']['return_number']), ret.get_json()


def test_with_no_series_configured_po_number_is_byte_identical_to_legacy(client_and_company):
    c, _cid = client_and_company
    pid = _seed_product(c)
    with at_terminal(TILL_A):
        r = c.post('/api/sub/retail/purchase-orders', json={
            'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 5.0}],
        })
    assert r.status_code == 200, r.get_json()
    assert _LEGACY_PO_RE.match(r.get_json()['data']['po_number']), r.get_json()


# ═════════════════════════════════════════════════════════════════════════
# C. Route-level: a claimed book is actually used
# ═════════════════════════════════════════════════════════════════════════

def test_a_claimed_sale_book_is_used_instead_of_the_legacy_path(client_and_company):
    c, cid = client_and_company
    pid = _seed_product(c)
    _seed_series(cid, 'sale', 'SALEBOOK1', allocator_terminal_uid=TILL_A)
    with at_terminal(TILL_A):
        r1 = _sell(c, pid)
        r2 = _sell(c, pid)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()['data']['sale_number'] == 'SALEBOOK1-000001'
    assert r2.get_json()['data']['sale_number'] == 'SALEBOOK1-000002'


def test_a_book_claimed_by_a_different_till_is_never_used(client_and_company):
    """The collision-prevention property at its simplest: a book claimed
    by SOME OTHER terminal must never be picked up here, even though it is
    the only 'sale' book this company has configured."""
    c, cid = client_and_company
    pid = _seed_product(c)
    _seed_series(cid, 'sale', 'SALEBOOK2', allocator_terminal_uid='some-other-till')
    with at_terminal(TILL_A):
        r = _sell(c, pid)
    assert r.status_code == 200, r.get_json()
    assert _LEGACY_SALE_RE.match(r.get_json()['data']['sale_number']), \
        "a peer's claimed book leaked into this till's own numbering"


def test_a_return_book_and_a_sale_book_can_share_the_same_code():
    """The C1 allow-half at the mint site, not just the index (see
    retail_doc_series_migration_test.py's own DB-level proof of the same
    property)."""
    c, cid = _make_admin_client('docseries-samecode')
    pid = _seed_product(c)
    _seed_series(cid, 'sale', 'SHAREDCODE', allocator_terminal_uid=TILL_A)
    _seed_series(cid, 'return', 'SHAREDCODE', allocator_terminal_uid=TILL_A)
    with at_terminal(TILL_A):
        sale = _sell(c, pid, items=[{'product_id': pid, 'quantity': 2}])
        assert sale.status_code == 200, sale.get_json()
        assert sale.get_json()['data']['sale_number'] == 'SHAREDCODE-000001'
        ret = c.post('/api/sub/retail/returns', json={
            'sale_id': sale.get_json()['data']['id'],
            'items': [{'product_id': pid, 'quantity': 1}],
        })
    assert ret.status_code == 200, ret.get_json()
    assert ret.get_json()['data']['return_number'] == 'SHAREDCODE-000001'


def test_a_claimed_po_book_is_used_at_both_mint_sites(client_and_company):
    """`retail_po_number_uniqueness_test.py` exists because fixing one PO
    mint site and not the other left AUDIT-032B fully live through
    `accept_reorder_request`; this feature must not repeat that mistake."""
    c, cid = client_and_company
    _seed_series(cid, 'po', 'POBOOK1', allocator_terminal_uid=TILL_A)
    pid = _seed_product(c)
    with at_terminal(TILL_A):
        po = c.post('/api/sub/retail/purchase-orders', json={
            'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 5.0}],
        })
        assert po.status_code == 200, po.get_json()
        assert po.get_json()['data']['po_number'] == 'POBOOK1-000001'

        # Second mint site: accept_reorder_request. Trigger a pending
        # reorder request via a real sale that drops stock to/below its
        # reorder_level -- the same technique
        # retail_po_number_uniqueness_test.py's own
        # `_trigger_pending_reorder_request` uses.
        rp = _seed_product(c, name='Reorder Widget', reorder_level=5,
                            reorder_method='whatsapp', initial_stock=6)
        sale = _sell(c, rp, items=[{'product_id': rp, 'quantity': 2}])
        assert sale.status_code == 200, sale.get_json()
        conn = get_retail_conn()
        try:
            req = conn.execute(
                "SELECT id FROM reorder_requests WHERE product_id=? AND status='pending'", (rp,)
            ).fetchone()
        finally:
            conn.close()
        assert req is not None
        accept = c.post(f"/api/sub/retail/reorder-requests/{req['id']}/accept")
    assert accept.status_code == 200, accept.get_json()
    assert accept.get_json()['data']['po_number'] == 'POBOOK1-000002', (
        "the reorder-accept mint site did not resolve the SAME claimed book "
        "create_purchase_order already used -- exactly the two-mint-site gap "
        "retail_po_number_uniqueness_test.py exists to prevent")


# ═════════════════════════════════════════════════════════════════════════
# D. Gapless under rollback -- once per mint site
# ═════════════════════════════════════════════════════════════════════════

def test_sale_number_is_not_consumed_when_the_sale_transaction_rolls_back(client_and_company, monkeypatch):
    """Forces a REAL sqlite3.IntegrityError AFTER `sale_number` has already
    been minted (create_sale's own `_queue_sync_event` call happens after
    the mint and before `conn.commit()`) and confirms the NEXT sale still
    gets `A-000001` -- proving the failed attempt's number was never
    persisted. MUT: move `doc_series.allocate` onto its own connection and
    commit it independently of `BEGIN IMMEDIATE` -> the aborted sale's
    number IS consumed, the next sale becomes `A-000002` -> RED (confirmed
    by hand while writing this test, then reverted)."""
    c, cid = client_and_company
    pid = _seed_product(c)
    _seed_series(cid, 'sale', 'ROLLBACKSALE', allocator_terminal_uid=TILL_A)

    def _boom(*a, **kw):
        raise sqlite3.IntegrityError("forced failure after mint, for the gapless-under-rollback proof")

    monkeypatch.setattr(retail_api, '_queue_sync_event', _boom)
    with at_terminal(TILL_A):
        failed = _sell(c, pid)
    assert failed.status_code in (400, 409, 500), failed.get_data(as_text=True)

    monkeypatch.undo()
    with at_terminal(TILL_A):
        ok = _sell(c, pid)
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['data']['sale_number'] == 'ROLLBACKSALE-000001', (
        "the aborted sale's number was consumed anyway -- gaplessness-under-rollback is broken")


def test_return_number_is_not_consumed_when_the_return_transaction_rolls_back(client_and_company, monkeypatch):
    c, cid = client_and_company
    pid = _seed_product(c)
    _seed_series(cid, 'return', 'ROLLBACKRET', allocator_terminal_uid=TILL_A)
    with at_terminal(TILL_A):
        sale = _sell(c, pid, items=[{'product_id': pid, 'quantity': 5}])
    assert sale.status_code == 200, sale.get_json()
    sale_id = sale.get_json()['data']['id']

    def _boom(*a, **kw):
        raise sqlite3.IntegrityError("forced failure after mint, for the gapless-under-rollback proof")

    monkeypatch.setattr(retail_api, '_queue_sync_event', _boom)
    with at_terminal(TILL_A):
        failed = c.post('/api/sub/retail/returns', json={
            'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
        })
    assert failed.status_code in (400, 409, 500), failed.get_data(as_text=True)

    monkeypatch.undo()
    with at_terminal(TILL_A):
        ok = c.post('/api/sub/retail/returns', json={
            'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': 1}],
        })
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['data']['return_number'] == 'ROLLBACKRET-000001'


def test_po_number_is_not_consumed_when_the_po_transaction_rolls_back(client_and_company, monkeypatch):
    """create_purchase_order has no explicit `BEGIN IMMEDIATE`: the implicit
    deferred transaction from `_conn`'s default `isolation_level` and its
    SINGLE `conn.commit()` (after `_audit`) is the whole gaplessness
    contract -- see database/schema.py's own migration docstring, "GAP
    BEHAVIOUR", for the enumerated commit points this test re-proves rather
    than inherits. `amount_paid > 0.005` is REQUIRED here so `_record_
    payment` actually runs inside the route -- the hook this test forces
    to fail."""
    c, cid = client_and_company
    pid = _seed_product(c)
    _seed_series(cid, 'po', 'ROLLBACKPO', allocator_terminal_uid=TILL_A)

    def _boom(*a, **kw):
        raise sqlite3.IntegrityError("forced failure after mint, for the gapless-under-rollback proof")

    monkeypatch.setattr(retail_api, '_record_payment', _boom)
    with at_terminal(TILL_A):
        failed = c.post('/api/sub/retail/purchase-orders', json={
            'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 5.0}], 'amount_paid': 1.0,
        })
    assert failed.status_code in (400, 409, 500), failed.get_data(as_text=True)

    monkeypatch.undo()
    with at_terminal(TILL_A):
        ok = c.post('/api/sub/retail/purchase-orders', json={
            'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 5.0}],
        })
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()['data']['po_number'] == 'ROLLBACKPO-000001', (
        "the aborted PO's number was consumed anyway -- gaplessness-under-rollback is broken")


# ═════════════════════════════════════════════════════════════════════════
# E. create_doc_series: containment and the (doc_type, code) allow-half
# ═════════════════════════════════════════════════════════════════════════

def test_create_doc_series_duplicate_code_is_409_and_does_not_leak_the_connection(client_and_company):
    c, _cid = client_and_company
    first = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'DUP', 'label': 'First'})
    assert first.status_code == 200, first.get_json()

    second = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'DUP', 'label': 'Second'})
    assert second.status_code == 409, second.get_data(as_text=True)
    assert second.is_json
    assert second.get_json()['status'] == 'error'

    # The install-wide "database is locked" outage `create_purchase_order`
    # already learned the hard way -- an unrelated write on the SAME
    # database must still succeed after the failed INSERT above.
    third = c.post('/api/sub/retail/products', json={
        'name': 'Post-Collision Product', 'sku': f'DOCSER-POST-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.0,
    })
    assert third.status_code == 200, \
        f"a later write was wedged by the failed doc_series INSERT above: {third.get_data(as_text=True)}"


def test_create_doc_series_same_code_is_allowed_under_a_different_doc_type(client_and_company):
    c, _cid = client_and_company
    sale_book = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'SHARED', 'label': 'S'})
    assert sale_book.status_code == 200, sale_book.get_json()
    return_book = c.post('/api/sub/retail/doc-series', json={'doc_type': 'return', 'code': 'SHARED', 'label': 'R'})
    assert return_book.status_code == 200, return_book.get_json()


def test_create_doc_series_rejects_a_reserved_einvoicing_prefix(client_and_company):
    c, _cid = client_and_company
    r = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'INC', 'label': 'x'})
    assert r.status_code == 400, r.get_json()
    r2 = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'gs', 'label': 'x'})
    assert r2.status_code == 400, r2.get_json()


def test_update_doc_series_cannot_change_immutable_fields(client_and_company):
    """`code`/`doc_type`/`pad_width`/`start_no` are silently ignored by
    PATCH -- ONLY `label`/`status`/`branch_uid` are writable."""
    c, cid = client_and_company
    created = c.post('/api/sub/retail/doc-series',
                      json={'doc_type': 'sale', 'code': 'IMM', 'label': 'Original', 'pad_width': 6, 'start_no': 1})
    assert created.status_code == 200, created.get_json()
    series_id = created.get_json()['data']['id']

    patch = c.patch(f'/api/sub/retail/doc-series/{series_id}',
                     json={'code': 'CHANGED', 'pad_width': 9, 'start_no': 999, 'label': 'Renamed'})
    assert patch.status_code == 200, patch.get_json()

    conn = get_retail_conn()
    try:
        row = conn.execute("SELECT * FROM doc_series WHERE id=?", (series_id,)).fetchone()
    finally:
        conn.close()
    assert row['code'] == 'IMM', "code must be immutable through PATCH"
    assert row['pad_width'] == 6, "pad_width must be immutable through PATCH"
    assert row['start_no'] == 1, "start_no must be immutable through PATCH"
    assert row['label'] == 'Renamed', "label IS mutable and must have changed"


def test_claim_doc_series_refuses_when_this_device_has_no_identity(client_and_company):
    c, _cid = client_and_company
    created = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'NOID', 'label': 'x'})
    assert created.status_code == 200, created.get_json()
    series_id = created.get_json()['data']['id']
    with at_terminal(None):
        r = c.post(f'/api/sub/retail/doc-series/{series_id}/allocator-claim')
    assert r.status_code == 400, r.get_json()


def test_claim_doc_series_refuses_a_second_claim_on_an_already_claimed_book(client_and_company):
    c, cid = client_and_company
    created = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'ONCE', 'label': 'x'})
    series_id = created.get_json()['data']['id']
    with at_terminal(TILL_A):
        first = c.post(f'/api/sub/retail/doc-series/{series_id}/allocator-claim')
        assert first.status_code == 200, first.get_json()
    with at_terminal('some-other-till'):
        second = c.post(f'/api/sub/retail/doc-series/{series_id}/allocator-claim')
    assert second.status_code == 409, second.get_json()


def test_claim_doc_series_refuses_a_second_active_book_of_the_same_doc_type_for_one_till(client_and_company):
    """B3's scoping fix: one till may only actively own ONE book per
    (company, doc_type) at a time -- claiming a second must be refused
    outright rather than leaving `resolve_series`'s `LIMIT 1` to pick a
    non-deterministic winner."""
    c, _cid = client_and_company
    first = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'FIRST', 'label': 'x'})
    second = c.post('/api/sub/retail/doc-series', json={'doc_type': 'sale', 'code': 'SECOND', 'label': 'x'})
    with at_terminal(TILL_A):
        r1 = c.post(f"/api/sub/retail/doc-series/{first.get_json()['data']['id']}/allocator-claim")
        assert r1.status_code == 200, r1.get_json()
        r2 = c.post(f"/api/sub/retail/doc-series/{second.get_json()['data']['id']}/allocator-claim")
    assert r2.status_code == 409, r2.get_json()
