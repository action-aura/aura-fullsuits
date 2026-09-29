"""
Aura Retail -- Retail UX P1b item 6 (backend half): readable per-till numbers.

A NEW shop's first shift on a till books that till its own sale / return /
purchase-order series (`ABD1`, `ABD1-R`, `ABD1-P`) so the receipt reads
`ABD1-000001` instead of `SALE-<device>-000001`. The owner sees and changes the
branch code through GET/POST /api/sub/retail/numbering.

What this file proves, and how each guard was checked (every "MUT:" line is a
mutation that was applied to the real code, watched go RED on the named test,
and restored -- the RED/GREEN lines are quoted in the crew report):

  A. The pure naming rules (core/retail/doc_series.py), no Flask.
  B. The two routes, BOTH directions: the owner is served and the change is
     really stored and really read back; a manager (who even holds the
     retail.employees capability) and a cashier get 403 OWNER_ONLY; a branch of
     ANOTHER company is a 404, never served, never written.
  C. The shift-open hook: a brand-new shop's till gets the three books and the
     first sale is really numbered `<CODE>1-000001`; a shop with history, a
     shop whose owner switched it off, a device that is behind on sync, a
     device with no identity and a till that already owns books all get NOTHING
     and the shift still opens; a failure inside the hook can never fail the
     shift.

The allow-half is tested as hard as the deny-half: the legacy `SALE-` number
must still be what an existing shop mints.

Run (ONE file per process):
    .venv/Scripts/python.exe -m pytest products/retail/tests/retail_p1b_backend_numbering_test.py -q -p no:cacheprovider
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_numbering_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.identity import device_context, user_accounts  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402
from core.retail import doc_series  # noqa: E402

NUM = '/api/sub/retail/numbering'
TILL_1 = '11111111-aaaa-4aaa-8aaa-000000000001'
TILL_2 = '22222222-bbbb-4bbb-8bbb-000000000002'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_device_cache():
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@contextlib.contextmanager
def at_terminal(terminal_id):
    """Same seam retail_doc_series_test.py uses: pin which till this process
    is for the duration of a call."""
    previous = retail_api.local_terminal_id
    retail_api.local_terminal_id = lambda: terminal_id
    try:
        yield
    finally:
        retail_api.local_terminal_id = previous


_SHOP_COUNTER = [0]


def _next_branch_name():
    """Every shop in this file gets a branch whose default code is UNIQUE
    (T00, T01, ...). idx_doc_series_code is global across companies, so two
    shops both called 'Main' would legitimately get MAI1 and MAI2 and no
    test could name the code it expects."""
    _SHOP_COUNTER[0] += 1
    return f"T{_SHOP_COUNTER[0]:02d} shop", f"T{_SHOP_COUNTER[0]:02d}"


def _make_shop(branch_name=None):
    """Real company, real owner, real login, one branch WITH a wire uid (the
    production branch-creating paths all stamp one; a raw INSERT does not).
    Returns (client, company_id, branch_uid, default_code)."""
    if branch_name is None:
        branch_name, code = _next_branch_name()
    else:
        code = None
    email = f"p1bnum-{uuid.uuid4().hex[:10]}@test.local"
    password = "P1bNumberPW1"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"))
    conn.commit()
    conn.close()
    branch_uid = str(uuid.uuid4())
    rconn = get_retail_conn()
    try:
        rconn.execute("INSERT INTO branches (company_id,name,uid) VALUES (?,?,?)",
                      (company_id, branch_name, branch_uid))
        rconn.commit()
    finally:
        rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get('/api/sub/retail/settings/tax')  # materialise the lazy tables
    import random as _random
    rconn2 = get_retail_conn()
    try:
        rconn2.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
            (company_id, _random.randint(1, 5_000_000)))
        rconn2.commit()
    finally:
        rconn2.close()
    return client, company_id, branch_uid, code


def _make_staff(company_id, role, capabilities=None):
    email = f"p1bnum-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "P1bNumberPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"))
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, "retail", "full"))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        for code, level in (capabilities or {}).items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code))
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    return client


def _rows(sql, params=()):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _books(cid):
    return _rows("SELECT doc_type, code, allocator_terminal_uid, status, branch_uid, pad_width, start_no "
                 "FROM doc_series WHERE company_id=? ORDER BY doc_type", (cid,))


def _setting(cid, skey):
    rows = _rows("SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?", (cid, skey))
    return rows[0]['svalue'] if rows else None


def _open_shift(client, terminal):
    with at_terminal(terminal):
        return client.post('/api/sub/retail/cash-sessions/open', json={'opening_float': 0})


def _product(client):
    r = client.post('/api/sub/retail/products', json={
        'name': 'Numbering Widget', 'sku': f'NUM-{uuid.uuid4().hex[:8]}',
        'cost_price': 5.0, 'sell_price': 10.0, 'initial_stock': 1000})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell(client, pid, terminal):
    with at_terminal(terminal):
        return client.post('/api/sub/retail/sales', json={
            'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash',
            'amount_paid': 999999, 'idempotency_key': str(uuid.uuid4())})


# ═════════════════════════════════════════════════════════════════════════
# A. Pure naming rules
# ═════════════════════════════════════════════════════════════════════════

def _bare():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE doc_series (
            id TEXT PRIMARY KEY, company_id TEXT NOT NULL, doc_type TEXT NOT NULL,
            code TEXT NOT NULL, label TEXT NOT NULL, branch_uid TEXT,
            allocator_terminal_uid TEXT, claimed_at_utc TEXT,
            pad_width INTEGER NOT NULL DEFAULT 6, start_no INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active', created_at TEXT, updated_at_utc TEXT,
            row_version INTEGER NOT NULL DEFAULT 1
        );
    """)
    return conn


def _put(conn, cid, doc_type, code, terminal=None, status='active'):
    conn.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,allocator_terminal_uid,status) "
                 "VALUES (?,?,?,?,?,?,?)", (str(uuid.uuid4()), cid, doc_type, code, code, terminal, status))
    conn.commit()


@pytest.mark.parametrize("raw,expected", [
    ('abd', 'ABD'), ('  ab1 ', 'AB1'), ('AB', 'AB'), ('ABCD', 'ABCD'),
    ('A', None), ('ABCDE', None), ('A-B', None), ('', None), (None, None), (12, None),
    ('عم', None),  # Arabic letters are not valid: the code must be ASCII
])
def test_normalise_branch_code(raw, expected):
    assert doc_series.normalise_branch_code(raw) == expected


def test_default_branch_code_takes_the_first_three_latin_characters():
    assert doc_series.default_branch_code('Abdoun') == 'ABD'
    assert doc_series.default_branch_code('  a-b c d') == 'ABC'
    assert doc_series.default_branch_code('B2') == 'B2'


def test_default_branch_code_falls_back_for_an_arabic_only_or_empty_name():
    assert doc_series.default_branch_code('عبدون') == 'BR'
    assert doc_series.default_branch_code('') == 'BR'
    assert doc_series.default_branch_code(None) == 'BR'
    assert doc_series.default_branch_code('X') == 'BR'  # one usable char is not a code


def test_default_branch_code_never_returns_a_reserved_prefix():
    # The owner-visible default must be a code the create routes accept.
    assert doc_series.default_branch_code('Inc trading', reserved={'INC'}) == 'BR'
    assert doc_series.default_branch_code('Inc trading', reserved=()) == 'INC'


def test_book_codes_and_preview():
    assert doc_series.book_code('ABD', 1, 'sale') == 'ABD1'
    assert doc_series.book_code('ABD', 1, 'return') == 'ABD1-R'
    assert doc_series.book_code('ABD', 12, 'po') == 'ABD12-P'
    assert doc_series.preview('ABD') == {'sale': 'ABD1-000001', 'return': 'ABD1-R-000001'}
    assert doc_series.preview('ABD', 3, 4) == {'sale': 'ABD3-0001', 'return': 'ABD3-R-0001'}


def test_the_longest_book_code_fits_the_series_code_limit():
    # 4-char branch + 2-digit till + '-R' == 8; the create route allows 12.
    worst = doc_series.book_code('ABCD', doc_series.MAX_TILL_NUMBER, 'return')
    assert len(worst) <= 12, worst


def test_pick_till_number_takes_the_smallest_free_and_counts_globally_and_retired():
    conn = _bare()
    assert doc_series.pick_till_number(conn, 'ABD') == 1
    # ANOTHER company's sale book holds ABD1 -> the code is globally unique,
    # so this shop must skip to 2 (two shops both called 'ABD' must not collide).
    _put(conn, 'other-company', 'sale', 'ABD1')
    assert doc_series.pick_till_number(conn, 'ABD') == 2
    # A RETIRED book keeps its code forever (codes are immutable) -> still taken.
    _put(conn, 'other-company', 'return', 'ABD2-R', status='retired')
    assert doc_series.pick_till_number(conn, 'ABD') == 3
    # A po book alone also blocks the number: all three codes must be free.
    _put(conn, 'other-company', 'po', 'ABD3-P')
    assert doc_series.pick_till_number(conn, 'ABD') == 4


def test_pick_till_number_skips_reserved_codes_and_gives_none_when_full():
    conn = _bare()
    assert doc_series.pick_till_number(conn, 'ABD', reserved={'ABD1'}) == 2
    everything = {doc_series.book_code('ABD', n, dt)
                  for n in range(1, doc_series.MAX_TILL_NUMBER + 1)
                  for dt in doc_series.DOC_SERIES_TYPES}
    assert doc_series.pick_till_number(conn, 'ABD', reserved=everything) is None


def test_plan_books_gives_a_new_till_all_three_under_one_number():
    conn = _bare()
    plan = doc_series.plan_books(conn, 'c1', TILL_1, 'ABD')
    assert {b['doc_type']: b['code'] for b in plan} == {
        'sale': 'ABD1', 'return': 'ABD1-R', 'po': 'ABD1-P'}
    assert all(b['pad_width'] == 6 for b in plan)


def test_plan_books_is_empty_without_a_terminal_identity():
    conn = _bare()
    assert doc_series.plan_books(conn, 'c1', None, 'ABD') == []
    assert doc_series.plan_books(conn, 'c1', '', 'ABD') == []


def test_plan_books_is_empty_when_the_till_already_owns_everything():
    conn = _bare()
    for dt, code in (('sale', 'XY1'), ('return', 'XY1-R'), ('po', 'XY1-P')):
        _put(conn, 'c1', dt, code, terminal=TILL_1)
    assert doc_series.plan_books(conn, 'c1', TILL_1, 'ABD') == []


def test_plan_books_only_fills_what_is_missing_and_another_tills_books_do_not_count():
    conn = _bare()
    _put(conn, 'c1', 'sale', 'HAND', terminal=TILL_1)   # this till has a hand-made sale book
    _put(conn, 'c1', 'sale', 'ABD1', terminal=TILL_2)   # a DIFFERENT till owns ABD1
    plan = doc_series.plan_books(conn, 'c1', TILL_1, 'ABD')
    assert sorted(b['doc_type'] for b in plan) == ['po', 'return']
    assert {b['code'] for b in plan} == {'ABD2-R', 'ABD2-P'}   # 1 is taken by till 2


# ═════════════════════════════════════════════════════════════════════════
# B. GET/POST /numbering
# ═════════════════════════════════════════════════════════════════════════

def test_owner_reads_the_default_numbering():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    r = c.get(NUM)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['branch_uid'] == buid
    assert d['branch_code'] == 'ABD' and d['default_branch_code'] == 'ABD'
    assert d['branch_code_is_saved'] is False
    assert d['readable_enabled'] is True          # brand-new shop -> on
    assert d['preview'] == {'sale': 'ABD1-000001', 'return': 'ABD1-R-000001'}


def test_a_saved_branch_code_is_stored_read_back_and_previewed():
    c, cid, buid, code = _make_shop()
    r = c.post(NUM, json={'branch_code': ' zq7 '})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['branch_code'] == 'ZQ7' and d['branch_code_is_saved'] is True
    assert d['preview']['sale'] == 'ZQ71-000001'
    assert _setting(cid, 'branch_code:' + buid) == 'ZQ7'          # really stored, not echoed
    again = c.get(NUM).get_json()['data']
    assert again['branch_code'] == 'ZQ7' and again['default_branch_code'] == code
    # and the change is on the audit trail and on the sync outbox
    assert _rows("SELECT 1 FROM audit_log WHERE company_id=? AND action='NUMBERING_BRANCH_CODE_SET'", (cid,))
    ev = _rows("SELECT payload FROM sync_outbox WHERE entity_type='retail_setting' "
               "AND payload LIKE ?", ('%branch_code:' + buid + '%',))
    assert ev, 'the branch code must travel to the shop\'s other devices'


def test_the_flag_can_be_switched_off_and_on_and_is_stored():
    c, cid, buid, code = _make_shop()
    r = c.post(NUM, json={'readable_enabled': False})
    assert r.status_code == 200 and r.get_json()['data']['readable_enabled'] is False
    assert _setting(cid, 'numbering_readable_enabled') == '0'
    assert c.get(NUM).get_json()['data']['readable_enabled'] is False
    r = c.post(NUM, json={'readable_enabled': True})
    assert r.get_json()['data']['readable_enabled'] is True
    assert _setting(cid, 'numbering_readable_enabled') == '1'
    assert _rows("SELECT 1 FROM audit_log WHERE company_id=? AND action='NUMBERING_READABLE_SET'", (cid,))


def test_manager_and_cashier_are_refused_on_both_verbs_even_holding_the_capability():
    c, cid, buid, code = _make_shop()
    manager = _make_staff(cid, 'manager', {'retail.employees': 'full'})
    cashier = _make_staff(cid, 'cashier')
    for who in (manager, cashier):
        g = who.get(NUM)
        assert g.status_code == 403 and g.get_json().get('code') == 'OWNER_ONLY', g.get_json()
        p = who.post(NUM, json={'branch_code': 'HAX'})
        assert p.status_code == 403, p.get_json()
    # and nothing was written by the refused POSTs
    assert _setting(cid, 'branch_code:' + buid) is None
    # the owner is still served: the deny half must not have broken the allow half
    assert c.get(NUM).status_code == 200


def test_anonymous_callers_are_refused():
    assert app.test_client().get(NUM).status_code in (401, 403)
    assert app.test_client().post(NUM, json={'branch_code': 'HAX'}).status_code in (401, 403)


@pytest.mark.parametrize("bad", ['A', 'ABCDE', 'A-B', '', 'عم', 12, None])
def test_an_invalid_branch_code_is_a_400_and_nothing_is_stored(bad):
    c, cid, buid, code = _make_shop()
    r = c.post(NUM, json={'branch_code': bad})
    assert r.status_code == 400, r.get_json()
    assert _setting(cid, 'branch_code:' + buid) is None


@pytest.mark.parametrize("reserved", ['INC', 'GS', 'inc'])
def test_a_tax_authority_prefix_is_refused_as_a_branch_code(reserved):
    """The e-invoicing sequence owns these prefixes; a local book sharing one
    would collide with a number submitted to the tax authority."""
    c, cid, buid, code = _make_shop()
    r = c.post(NUM, json={'branch_code': reserved})
    assert r.status_code == 400, r.get_json()
    assert 'reserved' in r.get_json()['message'].lower()
    assert _setting(cid, 'branch_code:' + buid) is None


def test_an_empty_body_and_a_non_boolean_flag_are_400():
    c, cid, buid, code = _make_shop()
    assert c.post(NUM, json={}).status_code == 400
    assert c.post(NUM, json={'unrelated': 1}).status_code == 400
    for junk in ('true', 1, 0, None, 'yes'):
        r = c.post(NUM, json={'readable_enabled': junk})
        assert r.status_code == 400, (junk, r.get_json())
    assert _setting(cid, 'numbering_readable_enabled') is None


def test_another_companys_branch_is_a_404_on_read_and_write_and_is_never_touched():
    a, cid_a, buid_a, _ = _make_shop()
    b, cid_b, buid_b, _ = _make_shop()
    assert a.get(NUM, query_string={'branch_uid': buid_b}).status_code == 404
    r = a.post(NUM, json={'branch_uid': buid_b, 'branch_code': 'HAX'})
    assert r.status_code == 404, r.get_json()
    assert _setting(cid_b, 'branch_code:' + buid_b) is None
    assert _setting(cid_a, 'branch_code:' + buid_b) is None
    assert a.get(NUM, query_string={'branch_uid': 'no-such-branch'}).status_code == 404


def test_the_same_code_is_saved_per_company_and_never_leaks_across():
    a, cid_a, buid_a, _ = _make_shop()
    b, cid_b, buid_b, code_b = _make_shop()
    assert a.post(NUM, json={'branch_code': 'AAA'}).status_code == 200
    d = b.get(NUM).get_json()['data']
    assert d['branch_code'] == code_b and d['branch_code_is_saved'] is False


def test_a_saved_code_that_has_since_become_reserved_is_ignored():
    c, cid, buid, code = _make_shop()
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                     (cid, 'branch_code:' + buid, 'INC'))
        conn.commit()
    finally:
        conn.close()
    d = c.get(NUM).get_json()['data']
    assert d['branch_code'] == code and d['branch_code_is_saved'] is False


def test_the_readable_flag_defaults_off_for_a_shop_with_history():
    c, cid, buid, code = _make_shop()
    assert c.get(NUM).get_json()['data']['readable_enabled'] is True
    pid = _product(c)
    assert _sell(c, pid, TILL_1).status_code == 200
    d = c.get(NUM).get_json()['data']
    assert d['readable_enabled'] is False, 'a running shop must not be renumbered behind its back'
    # GET is a pure read: it must NOT have pinned anything.
    assert _setting(cid, 'numbering_readable_enabled') is None


# ═════════════════════════════════════════════════════════════════════════
# C. The shift-open hook
# ═════════════════════════════════════════════════════════════════════════

def test_a_new_shops_first_shift_books_the_till_and_the_first_sale_is_readable():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    books = _books(cid)
    assert {b['doc_type']: b['code'] for b in books} == {
        'sale': code + '1', 'return': code + '1-R', 'po': code + '1-P'}
    assert all(b['allocator_terminal_uid'] == TILL_1 and b['status'] == 'active'
               and b['branch_uid'] == buid and b['pad_width'] == 6 and b['start_no'] == 1
               for b in books)
    sale = _sell(c, pid, TILL_1)
    assert sale.status_code == 200, sale.get_json()
    assert sale.get_json()['data']['sale_number'] == code + '1-000001'
    # each new book was announced to the shop's other devices, and audited
    ev = _rows("SELECT entity_id FROM sync_outbox WHERE entity_type='doc_series' AND event_type='create'")
    assert {b_id for b_id in (r_['entity_id'] for r_ in ev)} >= {
        x['id'] for x in _rows("SELECT id FROM doc_series WHERE company_id=?", (cid,))}
    assert len(_rows("SELECT 1 FROM audit_log WHERE company_id=? AND action='DOC_SERIES_AUTO_CREATED'", (cid,))) == 3


def test_the_flag_is_pinned_at_the_first_shift_so_new_cannot_turn_into_not_new():
    c, cid, buid, code = _make_shop()
    assert _setting(cid, 'numbering_readable_enabled') is None
    assert _open_shift(c, TILL_1).status_code == 200
    assert _setting(cid, 'numbering_readable_enabled') == '1'


def test_a_shop_with_history_keeps_legacy_numbers_and_the_shift_still_opens():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _sell(c, pid, TILL_1).status_code == 200      # history: a real sale
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []
    assert _setting(cid, 'numbering_readable_enabled') == '0'   # pinned OFF
    legacy = _sell(c, pid, TILL_1).get_json()['data']['sale_number']
    assert re.match(r'^SALE-\d+-[0-9a-f]{8}-[0-9a-f]{8}$', legacy), legacy


def test_a_shop_with_only_a_purchase_order_or_a_return_book_is_not_new():
    for kind in ('doc_series', 'purchase_orders'):
        c, cid, buid, code = _make_shop()
        conn = get_retail_conn()
        try:
            if kind == 'doc_series':
                conn.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,status,row_version) "
                             "VALUES (?,?,'sale',?,?, 'active',1)",
                             (str(uuid.uuid4()), cid, 'H' + uuid.uuid4().hex[:6].upper(), 'hand'))
            else:
                conn.execute("INSERT INTO purchase_orders (company_id,po_number,status) VALUES (?,?,'pending')",
                             (cid, 'PO-X-' + uuid.uuid4().hex[:6]))
            conn.commit()
        finally:
            conn.close()
        assert c.get(NUM).get_json()['data']['readable_enabled'] is False, kind


def test_an_owner_who_switched_it_off_gets_nothing_on_a_new_shop():
    c, cid, buid, code = _make_shop()
    assert c.post(NUM, json={'readable_enabled': False}).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []


def test_an_existing_shop_that_switches_it_on_gets_books_at_its_next_shift():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _sell(c, pid, TILL_1).status_code == 200
    assert c.post(NUM, json={'readable_enabled': True}).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert {b['code'] for b in _books(cid)} == {code + '1', code + '1-R', code + '1-P'}


def test_the_owners_saved_branch_code_is_the_one_the_books_use():
    c, cid, buid, code = _make_shop()
    assert c.post(NUM, json={'branch_code': 'QZ9'}).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert {b['code'] for b in _books(cid)} == {'QZ91', 'QZ91-R', 'QZ91-P'}


def test_a_second_till_in_the_same_shop_gets_number_2_and_keeps_its_own_books():
    c, cid, buid, code = _make_shop()
    assert _open_shift(c, TILL_1).status_code == 200
    assert _open_shift(c, TILL_2).status_code == 200
    by_till = {}
    for b in _books(cid):
        by_till.setdefault(b['allocator_terminal_uid'], set()).add(b['code'])
    assert by_till[TILL_1] == {code + '1', code + '1-R', code + '1-P'}
    assert by_till[TILL_2] == {code + '2', code + '2-R', code + '2-P'}


def test_a_till_that_already_owns_all_three_books_gets_no_more():
    c, cid, buid, code = _make_shop()
    assert _open_shift(c, TILL_1).status_code == 200
    n = len(_books(cid))
    # close, reopen: a second shift on the same till must not mint a second set
    cur = c.get('/api/sub/retail/cash-sessions/current')
    with at_terminal(TILL_1):
        cur = c.get('/api/sub/retail/cash-sessions/current').get_json()['data']
        c.post(f"/api/sub/retail/cash-sessions/{cur['id']}/close",
               json={'counted_cash': 0, 'closing_cash': 0})
    assert _open_shift(c, TILL_1).status_code in (200, 409)
    assert len(_books(cid)) == n == 3


def test_a_hand_made_sale_book_is_left_alone_and_only_the_missing_books_are_added():
    c, cid, buid, code = _make_shop()
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,allocator_terminal_uid,"
                     "status,row_version) VALUES (?,?,'sale',?,?,?, 'active',1)",
                     (str(uuid.uuid4()), cid, 'HM' + uuid.uuid4().hex[:4].upper(), 'hand', TILL_1))
        conn.commit()
    finally:
        conn.close()
    # a hand-made book makes the shop "not new" -> off, so nothing is added
    assert _open_shift(c, TILL_1).status_code == 200
    assert sorted(b['doc_type'] for b in _books(cid)) == ['sale']
    # the owner turns it on: only the two missing books arrive, the sale book is untouched
    assert c.post(NUM, json={'readable_enabled': True}).status_code == 200
    with at_terminal(TILL_1):
        cur = c.get('/api/sub/retail/cash-sessions/current').get_json()['data']
        c.post(f"/api/sub/retail/cash-sessions/{cur['id']}/close", json={'counted_cash': 0, 'closing_cash': 0})
    assert _open_shift(c, TILL_1).status_code == 200
    assert sorted(b['doc_type'] for b in _books(cid)) == ['po', 'return', 'sale']


def test_a_device_that_is_behind_on_sync_creates_nothing_but_the_shift_opens(monkeypatch):
    c, cid, buid, code = _make_shop()
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False, 'seconds_since_last_success': 10 ** 9})
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []
    # ...and once it has caught up, the next shift books the till (the allow half)
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False, 'seconds_since_last_success': 1})
    with at_terminal(TILL_1):
        cur = c.get('/api/sub/retail/cash-sessions/current').get_json()['data']
        c.post(f"/api/sub/retail/cash-sessions/{cur['id']}/close", json={'counted_cash': 0, 'closing_cash': 0})
    assert _open_shift(c, TILL_1).status_code == 200
    assert len(_books(cid)) == 3


def test_a_device_with_no_identity_creates_nothing_but_the_shift_opens():
    c, cid, buid, code = _make_shop()
    r = _open_shift(c, None)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == []
    # Decided behaviour: a device with no identity leaves the shop UNDECIDED
    # (flag not pinned), so the first shift of a device that does have one
    # decides. MUT (N10): remove the hook's no-identity early return -> the
    # flag gets pinned here and this goes RED.
    assert _setting(cid, 'numbering_readable_enabled') is None


def test_a_branch_without_a_wire_uid_creates_nothing_but_the_shift_opens():
    c, cid, buid, code = _make_shop()
    conn = get_retail_conn()
    try:
        conn.execute("UPDATE branches SET uid=NULL WHERE company_id=?", (cid,))
        conn.commit()
    finally:
        conn.close()
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []


def test_a_failure_inside_the_hook_can_never_fail_the_shift(monkeypatch):
    """The drawer is already committed when the hook runs. MUT: remove the
    hook's `except Exception` -> the open comes back 500 and this goes RED."""
    c, cid, buid, code = _make_shop()

    def boom(*a, **k):
        raise RuntimeError('planner exploded')
    monkeypatch.setattr(doc_series, 'plan_books', boom)
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == []
    with at_terminal(TILL_1):
        assert c.get('/api/sub/retail/cash-sessions/current').get_json()['data'] is not None


def test_a_taken_code_race_is_swallowed_and_leaves_no_half_set_of_books(monkeypatch):
    """Another device grabbed the code between the planner's read and our
    insert: the unique index fires on the 2nd book. All-or-nothing: no orphan
    sale book may remain, and the shift is open."""
    c, cid, buid, code = _make_shop()
    real = doc_series.plan_books

    def racing_plan(conn, company_id, terminal, branch_code, reserved=()):
        plan = real(conn, company_id, terminal, branch_code, reserved)
        # a rival company's return book takes the plan's return code right now
        other = get_retail_conn()
        try:
            other.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,status,row_version) "
                          "VALUES (?,?,'return',?,?,'active',1)",
                          (str(uuid.uuid4()), 'rival', plan[0]['code'] if False else
                           next(b['code'] for b in plan if b['doc_type'] == 'return'), 'rival'))
            other.commit()
        finally:
            other.close()
        return plan
    monkeypatch.setattr(doc_series, 'plan_books', racing_plan)
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == [], 'a half set of books must not survive a refused insert'


def test_a_refused_insert_is_rolled_back_on_the_callers_own_connection(monkeypatch):
    """The test above cannot see a missing `conn.rollback()`: the route closes
    the connection right after the hook and SQLite drops an uncommitted
    transaction on close, so the outcome is identical either way (mutation N9
    survived it). This one calls the hook directly and looks at the SAME
    connection afterwards, which is the only place a leftover half set is
    visible -- and where a later commit by any caller would make it permanent.
    MUT: remove the IntegrityError branch's `conn.rollback()` -> RED."""
    c, cid, buid, code = _make_shop()
    real = doc_series.plan_books

    def racing_plan(conn, company_id, terminal, branch_code, reserved=()):
        plan = real(conn, company_id, terminal, branch_code, reserved)
        other = get_retail_conn()
        try:
            other.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,status,row_version) "
                          "VALUES (?,?,'return',?,?,'active',1)",
                          (str(uuid.uuid4()), 'rival',
                           next(b['code'] for b in plan if b['doc_type'] == 'return'), 'rival'))
            other.commit()
        finally:
            other.close()
        return plan
    monkeypatch.setattr(doc_series, 'plan_books', racing_plan)
    conn = get_retail_conn()
    try:
        bid = conn.execute("SELECT id FROM branches WHERE company_id=?", (cid,)).fetchone()['id']
        with at_terminal(TILL_1):
            retail_api._auto_book_first_shift(conn, cid, bid)
        assert not conn.in_transaction, 'the refused insert left a transaction open on the caller connection'
        left = conn.execute("SELECT doc_type FROM doc_series WHERE company_id=?", (cid,)).fetchall()
        assert left == [], 'a half set of books is still visible on the caller connection'
    finally:
        conn.close()
