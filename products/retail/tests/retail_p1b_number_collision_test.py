"""Aura Retail P1b round 3, decision 2 -- a till NEVER hands out a document
number that already exists on its own database.

The bug (re-verifier B2, attack X4): a sale pulled from another computer can
already carry `AAA1-000003` while this computer's book counter is at 2. The
old `allocate` offered 3 again, the INSERT died with
`UNIQUE constraint failed: sales.sale_number`, the counter rolled back with the
failed sale, and EVERY later sale on that till died the same way, forever.
`sales.sale_number`, `returns.return_number` and `purchase_orders.po_number`
all carry a BARE UNIQUE index, so "taken" is a whole-table question.

Fixed at the root (core/retail/doc_series.py `allocate`, `peek_next_no`,
`number_is_taken`; api/retail_api.py `_mint_legacy_doc_number`): the allocator
advances past a taken number inside the caller's own transaction and logs once.

What is in here:
  * A. the book allocator, on a real sqlite schema slice, for all three doc types;
  * B. the read-only twin `peek_next_no` promises the number `allocate` will use;
  * C. `number_is_taken` fails closed for the ONE benign case and loudly for the rest;
  * D. the legacy (no-book) path, at the route level for a sale and through the
       mint helper for a return and a purchase order.

Mutation proofs are recorded in the round-3 report (the skip removed from
`allocate`, from `peek_next_no`, from `_mint_legacy_doc_number`; each goes RED
here with the UNIQUE error or a duplicate number).

Run:
    pytest products/retail/tests/retail_p1b_number_collision_test.py -v
"""
import logging
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_number_collision_"))
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


# ── A / B / C: the allocator on a real sqlite slice ─────────────────────────

_SLICE = """
CREATE TABLE doc_series_counter (series_id TEXT PRIMARY KEY, last_no INTEGER NOT NULL);
CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, sale_number TEXT UNIQUE);
CREATE TABLE returns (id INTEGER PRIMARY KEY AUTOINCREMENT, return_number TEXT UNIQUE);
CREATE TABLE purchase_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, po_number TEXT UNIQUE);
"""
_DOC_TABLE = {'sale': ('sales', 'sale_number'), 'return': ('returns', 'return_number'),
              'po': ('purchase_orders', 'po_number')}


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript(_SLICE)
    yield c
    c.close()


def _book(doc_type='sale', code='AAA1', start_no=1):
    return {'id': f'series-{doc_type}', 'code': code, 'doc_type': doc_type,
            'start_no': start_no, 'pad_width': 6}


def _plant(conn, doc_type, number):
    table, col = _DOC_TABLE[doc_type]
    conn.execute(f"INSERT INTO {table} ({col}) VALUES (?)", (number,))


def _insert(conn, doc_type, number):
    """The caller's real INSERT -- the thing that used to die with UNIQUE."""
    _plant(conn, doc_type, number)


@pytest.mark.parametrize('doc_type', ['sale', 'return', 'po'])
def test_x4_a_pulled_number_is_skipped_and_the_next_document_inserts(conn, doc_type):
    """X4: the other computer's document AAA1-000003 is already here while our
    counter sits at 2. The next document must get 000004 and INSERT cleanly."""
    book = _book(doc_type)
    conn.execute("INSERT INTO doc_series_counter VALUES (?, 2)", (book['id'],))
    _plant(conn, doc_type, 'AAA1-000003')

    number = doc_series.allocate(conn, book)
    assert number == 'AAA1-000004', number
    _insert(conn, doc_type, number)          # no UNIQUE error
    # and the counter moved past the skipped value, so the NEXT one is fresh too
    assert doc_series.allocate(conn, book) == 'AAA1-000005'


def test_several_taken_numbers_in_a_row_are_all_skipped(conn):
    book = _book()
    conn.execute("INSERT INTO doc_series_counter VALUES (?, 0)", (book['id'],))
    for n in (1, 2, 3):
        _plant(conn, 'sale', f'AAA1-{n:06d}')
    assert doc_series.allocate(conn, book) == 'AAA1-000004'


def test_an_untouched_book_still_starts_exactly_at_its_start_no(conn):
    """The allow half: the skip must not disturb the normal case."""
    assert doc_series.allocate(conn, _book(start_no=500)) == 'AAA1-000500'
    assert doc_series.allocate(conn, _book(start_no=500)) == 'AAA1-000501'


def test_the_skip_is_logged_once_not_per_number(conn, caplog):
    book = _book()
    conn.execute("INSERT INTO doc_series_counter VALUES (?, 0)", (book['id'],))
    for n in (1, 2, 3):
        _plant(conn, 'sale', f'AAA1-{n:06d}')
    with caplog.at_level(logging.WARNING):
        doc_series.allocate(conn, book)
    warned = [r for r in caplog.records if 'already present locally' in r.getMessage()]
    assert len(warned) == 1, [r.getMessage() for r in caplog.records]
    assert 'AAA1-000004' in warned[0].getMessage()


def test_the_skip_rolls_back_with_the_callers_transaction(conn):
    """The skipped counter values live in the caller's transaction, exactly like
    the number itself: a rolled-back document leaves no hole and no leak."""
    book = _book()
    conn.execute("INSERT INTO doc_series_counter VALUES (?, 2)", (book['id'],))
    _plant(conn, 'sale', 'AAA1-000003')
    conn.commit()
    doc_series.allocate(conn, book)
    conn.rollback()
    assert conn.execute("SELECT last_no FROM doc_series_counter").fetchone()[0] == 2


@pytest.mark.parametrize('doc_type', ['sale', 'return', 'po'])
def test_peek_promises_the_number_allocate_will_use(conn, doc_type):
    """B: the settings card must never promise a number the next document
    will not carry."""
    book = _book(doc_type)
    conn.execute("INSERT INTO doc_series_counter VALUES (?, 2)", (book['id'],))
    _plant(conn, doc_type, 'AAA1-000003')
    _plant(conn, doc_type, 'AAA1-000004')
    promised = doc_series.peek_next_no(conn, book)
    assert promised == 'AAA1-000005', promised
    assert doc_series.allocate(conn, book) == promised


def test_peek_on_an_unseeded_book_is_the_start_no(conn):
    assert doc_series.peek_next_no(conn, _book(start_no=7)) == 'AAA1-000007'


def test_number_is_taken_answers_per_doc_type(conn):
    _plant(conn, 'sale', 'X-1')
    assert doc_series.number_is_taken(conn, 'sale', 'X-1') is True
    assert doc_series.number_is_taken(conn, 'sale', 'X-2') is False
    # a sale number does not block a return number: separate tables
    assert doc_series.number_is_taken(conn, 'return', 'X-1') is False


def test_number_is_taken_unknown_doc_type_is_free(conn):
    assert doc_series.number_is_taken(conn, 'quotation', 'X-1') is False


def test_number_is_taken_missing_table_is_free_but_other_errors_surface():
    """C: ONLY 'no such table' is swallowed. A table that exists without the
    expected column is a real fault and must not be answered 'free'."""
    bare = sqlite3.connect(':memory:')
    try:
        assert doc_series.number_is_taken(bare, 'sale', 'X-1') is False
        bare.execute("CREATE TABLE sales (id INTEGER)")
        with pytest.raises(sqlite3.OperationalError):
            doc_series.number_is_taken(bare, 'sale', 'X-1')
    finally:
        bare.close()


# ── D: the legacy (no-book) path ────────────────────────────────────────────

def _make_admin_client(prefix):
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'NumCollidePW1'
    company_id = str(uuid.uuid4())
    c = registry_conn()
    c.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    c.commit()
    c.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id


def _sell(client, pid):
    return client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})


def test_legacy_sale_path_skips_a_number_that_a_pull_already_delivered():
    """X4 at the route level on the legacy path. The number the next sale would
    mint is planted (as a pulled sale from another computer would deliver it).
    Before the skip this answered 409/500 on every sale, forever."""
    client, cid = _make_admin_client('numcol')
    p = client.post('/api/sub/retail/products', json={
        'name': 'Collide Widget', 'sku': f'COL-{uuid.uuid4().hex[:8]}',
        'cost_price': 1.0, 'sell_price': 2.0, 'initial_stock': 50})
    assert p.status_code == 200, p.get_json()
    pid = p.get_json()['data']['id']

    first = _sell(client, pid)
    assert first.status_code == 200, first.get_json()
    n1 = first.get_json()['data']['sale_number']
    prefix, seq, cid_frag, dev_frag = n1.split('-')
    planted = f"{prefix}-{int(seq) + 1:06d}-{cid_frag}-{dev_frag}"
    conn = get_retail_conn()
    conn.execute("INSERT INTO sales (company_id, sale_number) VALUES (?,?)", (cid, planted))
    conn.commit()
    conn.close()

    second = _sell(client, pid)
    assert second.status_code == 200, second.get_data(as_text=True)
    n2 = second.get_json()['data']['sale_number']
    assert n2 not in (n1, planted), n2
    assert n2 == f"{prefix}-{int(seq) + 2:06d}-{cid_frag}-{dev_frag}", n2
    # and the till is not wedged: a third sale still works
    assert _sell(client, pid).status_code == 200


@pytest.mark.parametrize('doc_type,table,col', [
    ('return', 'returns', 'return_number'),
    ('po', 'purchase_orders', 'po_number'),
    ('sale', 'sales', 'sale_number'),
])
def test_mint_legacy_doc_number_never_returns_a_taken_number(doc_type, table, col):
    cid = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        first = retail_api._mint_legacy_doc_number(conn, cid, doc_type)
        conn.rollback()                       # the counter goes back to 0, like a failed document
        conn.execute(f"INSERT INTO {table} (company_id, {col}) VALUES (?,?)", (cid, first))
        conn.commit()
        second = retail_api._mint_legacy_doc_number(conn, cid, doc_type)
        assert second != first, "the legacy path handed out a number that already exists here"
        assert not doc_series.number_is_taken(conn, doc_type, second)
        conn.execute(f"INSERT INTO {table} (company_id, {col}) VALUES (?,?)", (cid, second))   # no UNIQUE error
        conn.commit()
    finally:
        conn.close()
