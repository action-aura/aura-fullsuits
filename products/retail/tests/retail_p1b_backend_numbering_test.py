"""
Aura Retail -- Retail UX P1b item 6 (backend half): readable per-till numbers.

THE DESIGN THIS FILE PINS (fix round after an adversarial two-computer run):
readable numbers NEVER switch on by themselves, for a new shop or an old one.
The owner switches them on for THIS computer, chooses this computer's till
number (1-99) by hand, and the next shift on this computer creates its books
(`ABD3`, `ABD3-R`, `ABD3-P`, so the receipt reads `ABD3-000001`). The switch is a
DEVICE-LOCAL row that never syncs; the branch code is a shop-wide, synced row.
The owner sees and changes all of it through GET/POST /api/sub/retail/numbering.

What each section proves (every "MUT:" line in the crew report is a mutation
applied to the real code, watched go RED on the named test, then restored):

  A. The pure naming rules (core/retail/doc_series.py), no Flask.
  B. The two routes, BOTH directions: the owner is served and a change is really
     stored and read back; a manager (who even holds retail.employees) and a
     cashier get 403 OWNER_ONLY with the right verb; a branch of ANOTHER company
     is a 404; every refusal writes NOTHING.
  C. Switching OFF really stops it: the books are retired, the next sale is the
     legacy number, and switching back on continues the counter.
  D. The shift-open hook: nothing books by itself; an enabled computer's next
     shift books it; a code change applies to the next book only when the
     computer has none yet; every skip still lets the shift open.
  E. Two computers: a number another computer already holds is refused, and the
     switch never travels.

Run (ONE file per process):
    .venv/Scripts/python.exe -m pytest products/retail/tests/retail_p1b_backend_numbering_test.py -q -p no:cacheprovider
"""
import contextlib
import json
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
LEGACY = re.compile(r'^SALE-\d+-[0-9a-f]{8}-[0-9a-f]{8}$')


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
    """Every shop gets a branch whose default code is UNIQUE and letters-only
    (QAA, QAB, ...): idx_doc_series_code is global across companies, so two
    shops both called 'Main' would want the same book codes and no test could
    name the code it expects. Returns (branch name, its default code)."""
    n = _SHOP_COUNTER[0]
    _SHOP_COUNTER[0] += 1
    code = 'Q' + chr(65 + (n // 26) % 26) + chr(65 + n % 26)
    return f"{code} shop", code


def _make_shop(branch_name=None):
    """Real company, real owner, real login, one branch WITH a wire uid.
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


def _add_branch(company_id, name):
    uid = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        cur = conn.execute("INSERT INTO branches (company_id,name,uid) VALUES (?,?,?)",
                           (company_id, name, uid))
        conn.commit()
        return cur.lastrowid, uid
    finally:
        conn.close()


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
    return _rows("SELECT id, doc_type, code, allocator_terminal_uid, status, branch_uid, pad_width, start_no "
                 "FROM doc_series WHERE company_id=? ORDER BY doc_type", (cid,))


def _setting(cid, skey):
    rows = _rows("SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?", (cid, skey))
    return rows[0]['svalue'] if rows else None


def _till_state(cid, terminal):
    raw = _setting(cid, 'numbering_till:' + terminal)
    return None if raw is None else json.loads(raw)


def _audit_rows(cid, action):
    return _rows("SELECT details FROM audit_log WHERE company_id=? AND action=? ORDER BY id", (cid, action))


def _get(c, terminal=TILL_1, **qs):
    with at_terminal(terminal):
        return c.get(NUM, query_string=qs)


def _post(c, body, terminal=TILL_1):
    with at_terminal(terminal):
        return c.post(NUM, json=body)


def _enable(c, till, terminal=TILL_1, **extra):
    body = {'readable_enabled': True, 'till_number': till}
    body.update(extra)
    return _post(c, body, terminal)


def _disable(c, terminal=TILL_1):
    return _post(c, {'readable_enabled': False}, terminal)


def _open_shift(client, terminal, branch_id=None):
    body = {'opening_float': 0}
    if branch_id is not None:
        body['branch_id'] = branch_id
    with at_terminal(terminal):
        return client.post('/api/sub/retail/cash-sessions/open', json=body)


def _close_shift(client, terminal, branch_id=None):
    qs = {} if branch_id is None else {'branch_id': branch_id}
    with at_terminal(terminal):
        cur = client.get('/api/sub/retail/cash-sessions/current', query_string=qs).get_json()['data']
        r = client.post(f"/api/sub/retail/cash-sessions/{cur['id']}/close",
                        json={'closing_float_counted': 0})
    assert r.status_code == 200, r.get_json()


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


def _sale_number(client, pid, terminal):
    r = _sell(client, pid, terminal)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['sale_number']


def _rival_book(doc_type, code, company='rival', terminal=None, status='active'):
    """A book that already exists on this computer and is NOT this till's."""
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO doc_series (id,company_id,doc_type,code,label,allocator_terminal_uid,status,row_version) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (str(uuid.uuid4()), company, doc_type, code, code, terminal, status))
        conn.commit()
    finally:
        conn.close()


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
        CREATE TABLE doc_series_counter (series_id TEXT PRIMARY KEY, last_no INTEGER NOT NULL);
    """)
    return conn


def _put(conn, cid, doc_type, code, terminal=None, status='active'):
    sid = str(uuid.uuid4())
    conn.execute("INSERT INTO doc_series (id,company_id,doc_type,code,label,allocator_terminal_uid,status) "
                 "VALUES (?,?,?,?,?,?,?)", (sid, cid, doc_type, code, code, terminal, status))
    conn.commit()
    return sid


@pytest.mark.parametrize("raw,expected", [
    ('abd', 'ABD'), ('  ab ', 'AB'), ('AB', 'AB'), ('ABCD', 'ABCD'),
    ('A', None), ('ABCDE', None), ('A-B', None), ('', None), (None, None), (12, None),
    # F10: LETTERS only. A digit would let ('AB1', till 2) and ('AB', till 12)
    # both spell AB12.
    ('AB1', None), ('AB12', None), ('A1B', None), ('1AB', None),
    ('عم', None),  # Arabic letters are not valid: the code must be ASCII
])
def test_normalise_branch_code(raw, expected):
    assert doc_series.normalise_branch_code(raw) == expected


def test_default_branch_code_takes_the_first_three_latin_letters():
    assert doc_series.default_branch_code('Abdoun') == 'ABD'
    assert doc_series.default_branch_code('  a-b c d') == 'ABC'
    assert doc_series.default_branch_code('Ab') == 'AB'
    # digits are not letters: '2 Main' has only 'Main' to offer
    assert doc_series.default_branch_code('2 Main') == 'MAI'


def test_default_branch_code_is_none_when_the_name_gives_fewer_than_two_letters():
    """F10: no invented "BR" fallback -- the screen must ASK."""
    assert doc_series.default_branch_code('عبدون') is None
    assert doc_series.default_branch_code('') is None
    assert doc_series.default_branch_code(None) is None
    assert doc_series.default_branch_code('X') is None
    assert doc_series.default_branch_code('B2') is None


def test_default_branch_code_never_returns_a_reserved_prefix():
    assert doc_series.default_branch_code('Inc trading', reserved={'INC'}) is None
    assert doc_series.default_branch_code('Inc trading', reserved=()) == 'INC'


def test_book_codes():
    assert doc_series.book_code('ABD', 1, 'sale') == 'ABD1'
    assert doc_series.book_code('ABD', 1, 'return') == 'ABD1-R'
    assert doc_series.book_code('ABD', 12, 'po') == 'ABD12-P'
    assert doc_series.till_codes('ABD', 3) == {'sale': 'ABD3', 'return': 'ABD3-R', 'po': 'ABD3-P'}


def test_the_longest_book_code_fits_the_series_code_limit():
    # 4 letters + 2 digits + '-R' == 8; the create route allows 12.
    worst = doc_series.book_code('ABCD', doc_series.MAX_TILL_NUMBER, 'return')
    assert len(worst) <= 12, worst


def test_two_different_branch_and_till_pairs_never_spell_the_same_book_code():
    """The reason for letters-only: with a letters-then-digits shape every code
    reads back one way, so the whole 2-4 letter x 1-99 space is collision-free."""
    seen = {}
    for branch in ('AB', 'ABC', 'ABD', 'ABCD', 'BC'):
        for till in range(1, doc_series.MAX_TILL_NUMBER + 1):
            code = doc_series.book_code(branch, till, 'sale')
            assert code not in seen, (code, seen.get(code), (branch, till))
            seen[code] = (branch, till)


def test_inspect_a_fresh_till_creates_all_three():
    conn = _bare()
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert i['codes'] == {'sale': 'ABD3', 'return': 'ABD3-R', 'po': 'ABD3-P'}
    assert i['create'] == ['sale', 'return', 'po']
    assert i['have'] == i['reactivate'] == i['conflicts'] == i['other_active'] == []


def test_inspect_any_book_with_the_code_that_is_not_this_terminals_is_a_conflict():
    """idx_doc_series_code is unique on (doc_type, code) GLOBALLY, so the check
    is global too: another company, another computer, an unclaimed book, and a
    RETIRED book of somebody else (codes are immutable) all block the number."""
    conn = _bare()
    _put(conn, 'other-company', 'sale', 'ABD3', terminal=TILL_1)      # same terminal id, other company
    _put(conn, 'c1', 'return', 'ABD3-R', terminal=TILL_2)             # another computer
    _put(conn, 'c1', 'po', 'ABD3-P', terminal=None)                   # unclaimed
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert {c['code'] for c in i['conflicts']} == {'ABD3', 'ABD3-R', 'ABD3-P'}
    assert i['create'] == i['have'] == i['reactivate'] == []
    conn2 = _bare()
    _put(conn2, 'c1', 'sale', 'ABD3', terminal=TILL_2, status='retired')
    i2 = doc_series.inspect_till_books(conn2, 'c1', TILL_1, 'ABD', 3)
    assert [c['code'] for c in i2['conflicts']] == ['ABD3']


def test_inspect_this_terminals_own_books_are_have_or_reactivate_not_conflicts():
    conn = _bare()
    a = _put(conn, 'c1', 'sale', 'ABD3', terminal=TILL_1)
    b = _put(conn, 'c1', 'return', 'ABD3-R', terminal=TILL_1, status='retired')
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert [r['id'] for r in i['have']] == [a]
    assert [r['id'] for r in i['reactivate']] == [b]
    assert i['create'] == ['po'] and i['conflicts'] == []


def test_inspect_another_active_book_of_this_terminal_is_reported_and_others_are_not():
    conn = _bare()
    _put(conn, 'c1', 'sale', 'HAND', terminal=TILL_1)                 # hand-made, this terminal
    _put(conn, 'c1', 'return', 'OTHR', terminal=TILL_2)               # another terminal's: irrelevant
    _put(conn, 'c1', 'po', 'OLDP', terminal=TILL_1, status='retired')  # retired: irrelevant
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert i['other_active'] == [{'doc_type': 'sale', 'code': 'HAND'}]


def test_inspect_without_a_terminal_never_claims_an_unclaimed_book():
    conn = _bare()
    _put(conn, 'c1', 'sale', 'ABD3', terminal=None)
    i = doc_series.inspect_till_books(conn, 'c1', None, 'ABD', 3)
    assert i['have'] == [] and [c['code'] for c in i['conflicts']] == ['ABD3']
    assert i['other_active'] == []


def test_till_numbers_in_use_reads_sale_books_of_every_company_and_status():
    conn = _bare()
    _put(conn, 'a', 'sale', 'ABD1')
    _put(conn, 'b', 'sale', 'ABD7', status='retired')
    _put(conn, 'c', 'sale', 'ABD12')
    _put(conn, 'c', 'return', 'ABD5-R')      # not a sale book: does not count
    _put(conn, 'c', 'sale', 'ABDE3')         # another branch code that merely starts with ABD
    _put(conn, 'c', 'sale', 'XYZ9')
    assert doc_series.till_numbers_in_use(conn, 'ABD') == [1, 7, 12]
    assert doc_series.till_numbers_in_use(conn, 'ABDE') == [3]
    assert doc_series.till_numbers_in_use(conn, 'QQQ') == []


def test_preview_next_shows_the_real_counter_not_always_000001():
    """F9: a till that has sold 124 times says 000125 next."""
    conn = _bare()
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert doc_series.preview_next(conn, i) == {'sale': 'ABD3-000001', 'return': 'ABD3-R-000001'}
    sale = _put(conn, 'c1', 'sale', 'ABD3', terminal=TILL_1)
    conn.execute("INSERT INTO doc_series_counter (series_id,last_no) VALUES (?,124)", (sale,))
    ret = _put(conn, 'c1', 'return', 'ABD3-R', terminal=TILL_1, status='retired')
    conn.execute("INSERT INTO doc_series_counter (series_id,last_no) VALUES (?,7)", (ret,))
    conn.commit()
    i = doc_series.inspect_till_books(conn, 'c1', TILL_1, 'ABD', 3)
    assert doc_series.preview_next(conn, i) == {'sale': 'ABD3-000125', 'return': 'ABD3-R-000008'}
    # and asking did not consume anything
    assert conn.execute("SELECT last_no FROM doc_series_counter WHERE series_id=?", (sale,)).fetchone()[0] == 124


# ═════════════════════════════════════════════════════════════════════════
# B. GET/POST /numbering
# ═════════════════════════════════════════════════════════════════════════

def test_default_is_off_on_this_computer_with_an_example_preview_and_a_get_writes_nothing():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    before = _rows("SELECT COUNT(*) AS n FROM retail_settings WHERE company_id=?", (cid,))[0]['n']
    r = _get(c)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['branch_uid'] == buid and d['branch_name'] == 'Abdoun Mall'
    assert d['branch_code'] == 'ABD' and d['default_branch_code'] == 'ABD'
    assert d['branch_code_is_saved'] is False and d['branch_code_taken_by'] is None
    assert d['readable_enabled'] is False and d['till_number'] is None and d['books_ready'] is False
    assert d['has_identity'] is True and d['max_till_number'] == 99 and d['till_numbers_in_use'] == []
    assert d['preview'] == {'sale': 'ABD1-000001', 'return': 'ABD1-R-000001'}
    assert d['preview_is_example'] is True
    assert 'long numbers' in d['preview_note'], d['preview_note']
    assert 'switched on afterwards' in d['branch_code_note']
    after = _rows("SELECT COUNT(*) AS n FROM retail_settings WHERE company_id=?", (cid,))[0]['n']
    assert after == before, 'GET is a pure read'


def test_the_example_preview_follows_a_requested_till_number_and_ignores_junk():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    assert _get(c, till_number='7').get_json()['data']['preview']['sale'] == 'ABD7-000001'
    for junk in ('0', '100', 'x', '-3', '2.5'):
        assert _get(c, till_number=junk).get_json()['data']['preview']['sale'] == 'ABD1-000001', junk


def test_a_saved_branch_code_is_stored_read_back_audited_and_synced():
    c, cid, buid, code = _make_shop()
    r = _post(c, {'branch_code': ' zqz '})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['branch_code'] == 'ZQZ' and d['branch_code_is_saved'] is True
    assert d['preview']['sale'] == 'ZQZ1-000001'
    assert _setting(cid, 'branch_code:' + buid) == 'ZQZ'          # really stored, not echoed
    again = _get(c).get_json()['data']
    assert again['branch_code'] == 'ZQZ' and again['default_branch_code'] == code
    assert [a['details'] for a in _audit_rows(cid, 'NUMBERING_BRANCH_CODE_SET')] == ['ZQZ']
    ev = _rows("SELECT payload FROM sync_outbox WHERE entity_type='retail_setting' AND payload LIKE ?",
               ('%branch_code:' + buid + '%',))
    assert ev, "the branch code is a fact about the branch: it must travel to the shop's other computers"


def test_an_arabic_only_branch_name_has_no_code_until_the_owner_chooses_one():
    """F10: never invent a code. The screen asks; enabling without one is a 400."""
    c, cid, buid, _ = _make_shop('عبدون')
    d = _get(c).get_json()['data']
    assert d['branch_code'] is None and d['default_branch_code'] is None
    assert d['preview'] is None and d['till_numbers_in_use'] == []
    r = _enable(c, 1)
    assert r.status_code == 400 and 'no code' in r.get_json()['message'], r.get_json()
    assert _till_state(cid, TILL_1) is None
    assert _post(c, {'branch_code': 'abd'}).status_code == 200
    r = _enable(c, 1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['branch_code'] == 'ABD' and r.get_json()['data']['readable_enabled'] is True


_MISSING = object()


@pytest.mark.parametrize("till", [_MISSING, None, 0, 100, -1, True, '3', 2.5])
def test_enabling_needs_a_till_number_from_1_to_99_and_a_refusal_writes_nothing(till):
    """The number is never picked for the owner. Nothing is written on a 400."""
    c, cid, buid, code = _make_shop()
    body = {'readable_enabled': True}
    if till is not _MISSING:
        body['till_number'] = till
    r = _post(c, body)
    assert r.status_code == 400, (till, r.get_json())
    assert 'never picked' in r.get_json()['message']
    assert _till_state(cid, TILL_1) is None
    assert _audit_rows(cid, 'NUMBERING_READABLE_SET') == []
    assert _books(cid) == []


@pytest.mark.parametrize("edge", [1, 99])
def test_the_edges_of_the_till_number_range_are_accepted(edge):
    c, cid, buid, code = _make_shop()
    r = _enable(c, edge)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['till_number'] == edge


def test_enabling_stores_a_device_local_switch_and_creates_no_books_yet():
    c, cid, buid, code = _make_shop()
    r = _enable(c, 3)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['readable_enabled'] is True and d['till_number'] == 3 and d['books_ready'] is False
    assert d['preview_is_example'] is False
    assert d['preview'] == {'sale': code + '3-000001', 'return': code + '3-R-000001'}
    assert 'next shift' in d['preview_note']
    assert _till_state(cid, TILL_1) == {'on': True, 'till': 3, 'branch_uid': buid, 'series_ids': []}
    assert _books(cid) == [], 'the books are made at the next shift, not by the switch'
    assert [a['details'] for a in _audit_rows(cid, 'NUMBERING_READABLE_SET')] == ['1:till 3']
    # the switch is DEVICE-LOCAL: it must not have been queued for sync
    assert _rows("SELECT 1 FROM sync_outbox WHERE payload LIKE '%numbering_till%'") == []


def test_the_switch_is_per_computer_another_computer_of_the_same_shop_still_reads_off():
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    other = _get(c, TILL_2).get_json()['data']
    assert other['readable_enabled'] is False and other['till_number'] is None
    assert other['preview_is_example'] is True
    assert _get(c, TILL_1).get_json()['data']['readable_enabled'] is True


def test_a_computer_with_no_identity_cannot_be_switched_on():
    c, cid, buid, code = _make_shop()
    r = _enable(c, 1, terminal=None)
    assert r.status_code == 409 and 'identity' in r.get_json()['message'], r.get_json()
    assert _rows("SELECT 1 FROM retail_settings WHERE company_id=? AND skey LIKE 'numbering_till:%'", (cid,)) == []
    assert _get(c, terminal=None).get_json()['data']['has_identity'] is False


def test_a_till_number_another_computer_already_holds_is_refused_and_a_free_one_is_not():
    c, cid, buid, code = _make_shop()
    _rival_book('sale', code + '3', company='other-shop', terminal=TILL_2)   # somebody else has sale book 3
    r = _enable(c, 3)
    assert r.status_code == 409 and r.get_json().get('code') == 'TILL_NUMBER_TAKEN', r.get_json()
    assert 'already used' in r.get_json()['message']
    assert _till_state(cid, TILL_1) is None and _audit_rows(cid, 'NUMBERING_READABLE_SET') == []
    d = _get(c).get_json()['data']
    assert d['till_numbers_in_use'] == [3]                       # the screen can grey it out
    ok = _enable(c, 4)                                           # the allow half
    assert ok.status_code == 200, ok.get_json()


def test_a_taken_return_or_po_book_blocks_the_number_too():
    for doc_type, suffix in (('return', '-R'), ('po', '-P')):
        c, cid, buid, code = _make_shop()
        _rival_book(doc_type, code + '2' + suffix, company='other-shop')
        r = _enable(c, 2)
        assert r.status_code == 409 and r.get_json().get('code') == 'TILL_NUMBER_TAKEN', (doc_type, r.get_json())
        assert _till_state(cid, TILL_1) is None


def test_a_computer_that_already_numbers_with_other_books_must_switch_those_off_first():
    c, cid, buid, code = _make_shop()
    _rival_book('sale', 'HANDMADE', company=cid, terminal=TILL_1)          # this computer's own hand-made book
    r = _enable(c, 5)
    assert r.status_code == 409 and 'already numbers' in r.get_json()['message'], r.get_json()
    assert _till_state(cid, TILL_1) is None
    assert [b['status'] for b in _books(cid)] == ['active'], 'the hand-made book is left alone'


def test_two_branches_of_one_shop_cannot_share_a_code():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    b2_id, b2_uid = _add_branch(cid, 'Sweileh')
    # saving another branch's code is a 409, nothing stored
    r = c.post(NUM, json={'branch_uid': b2_uid, 'branch_code': 'abd'})
    assert r.status_code == 409 and r.get_json().get('code') == 'BRANCH_CODE_TAKEN', r.get_json()
    assert 'Abdoun Mall' in r.get_json()['message']
    assert _setting(cid, 'branch_code:' + b2_uid) is None
    # a code equal to the other branch's DEFAULT (not saved) counts as taken too
    r = c.post(NUM, json={'branch_uid': buid, 'branch_code': 'swe'})
    assert r.status_code == 409 and r.get_json().get('code') == 'BRANCH_CODE_TAKEN', r.get_json()
    # a free code is fine (the allow half)
    assert c.post(NUM, json={'branch_uid': b2_uid, 'branch_code': 'swl'}).status_code == 200
    # the GET says who holds a shared code
    b3_id, b3_uid = _add_branch(cid, 'Abdoun Annex')      # default ABD == branch 1's
    d = c.get(NUM, query_string={'branch_uid': b3_uid}).get_json()['data']
    assert d['branch_code'] == 'ABD' and d['branch_code_taken_by'] == 'Abdoun Mall'


def test_switching_on_a_branch_that_shares_its_code_is_refused_until_it_has_its_own():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    b3_id, b3_uid = _add_branch(cid, 'Abdoun Annex')      # default ABD == branch 1's
    with at_terminal(TILL_1):
        r = c.post(NUM, json={'branch_uid': b3_uid, 'readable_enabled': True, 'till_number': 1})
    assert r.status_code == 409 and r.get_json().get('code') == 'BRANCH_CODE_TAKEN', r.get_json()
    assert _till_state(cid, TILL_1) is None
    with at_terminal(TILL_1):
        r = c.post(NUM, json={'branch_uid': b3_uid, 'branch_code': 'ANX',
                              'readable_enabled': True, 'till_number': 1})
    assert r.status_code == 200, r.get_json()


def test_the_same_code_in_two_shops_is_allowed_and_the_till_number_then_decides():
    """Codes are unique per COMPANY (a shop cannot see another shop's branches);
    the book codes are globally unique, so the till number is what separates."""
    a, cid_a, buid_a, _ = _make_shop()
    b, cid_b, buid_b, _ = _make_shop()
    assert _post(a, {'branch_code': 'SAM'}).status_code == 200
    assert _post(b, {'branch_code': 'SAM'}).status_code == 200
    assert _enable(a, 1).status_code == 200
    assert _open_shift(a, TILL_1).status_code == 200                    # A now holds SAM1
    r = _enable(b, 1, terminal=TILL_2)
    assert r.status_code == 409 and r.get_json().get('code') == 'TILL_NUMBER_TAKEN', r.get_json()
    assert _enable(b, 2, terminal=TILL_2).status_code == 200


def test_manager_and_cashier_are_refused_on_both_verbs_and_the_read_says_open():
    c, cid, buid, code = _make_shop()
    manager = _make_staff(cid, 'manager', {'retail.employees': 'full'})
    cashier = _make_staff(cid, 'cashier')
    for who in (manager, cashier):
        g = _get(who)
        assert g.status_code == 403 and g.get_json().get('code') == 'OWNER_ONLY', g.get_json()
        assert g.get_json()['message'] == 'Only the shop owner can open this.'
        p = who.post(NUM, json={'branch_code': 'HAX'})
        assert p.status_code == 403, p.get_json()
        e = _enable(who, 1)
        assert e.status_code == 403, e.get_json()
    # The write route sits behind the employees capability FIRST, so a cashier
    # is stopped by that gate (generic wording); a manager who DOES hold the
    # capability gets past it and must be stopped by the owner-only guard with
    # the "change" verb. That second refusal is the one this pins.
    p = manager.post(NUM, json={'branch_code': 'HAX'})
    assert p.get_json().get('code') == 'OWNER_ONLY', p.get_json()
    assert p.get_json()['message'] == 'Only the shop owner can change this.'
    e = _enable(manager, 1)
    assert e.get_json().get('code') == 'OWNER_ONLY', e.get_json()
    assert _setting(cid, 'branch_code:' + buid) is None and _till_state(cid, TILL_1) is None
    assert _get(c).status_code == 200      # the allow half


def test_anonymous_callers_are_refused():
    assert app.test_client().get(NUM).status_code in (401, 403)
    assert app.test_client().post(NUM, json={'branch_code': 'HAX'}).status_code in (401, 403)


@pytest.mark.parametrize("bad", ['A', 'ABCDE', 'A-B', '', 'عم', 12, None, 'AB1', 'AB12', 'A B'])
def test_an_invalid_branch_code_is_a_400_and_nothing_is_stored(bad):
    c, cid, buid, code = _make_shop()
    r = _post(c, {'branch_code': bad})
    assert r.status_code == 400, r.get_json()
    assert _setting(cid, 'branch_code:' + buid) is None


@pytest.mark.parametrize("reserved", ['INC', 'GS', 'inc', 'SALE', 'sale', 'RET', 'PO', 'REC', 'PAY', 'HOLD', 'QUO'])
def test_a_prefix_this_app_already_mints_is_refused_as_a_branch_code(reserved):
    """The tax-authority sequence owns INC/GS; SALE/RET/PO/... are the legacy
    document prefixes. A branch coded PO would print `PO1-000001` beside `PO-...`."""
    c, cid, buid, code = _make_shop()
    r = _post(c, {'branch_code': reserved})
    assert r.status_code == 400, r.get_json()
    assert 'already used' in r.get_json()['message']
    assert _setting(cid, 'branch_code:' + buid) is None


def test_an_empty_body_and_a_non_boolean_flag_are_400():
    c, cid, buid, code = _make_shop()
    assert _post(c, {}).status_code == 400
    assert _post(c, {'unrelated': 1}).status_code == 400
    for junk in ('true', 1, 0, None, 'yes'):
        r = _post(c, {'readable_enabled': junk, 'till_number': 3})
        assert r.status_code == 400, (junk, r.get_json())
    assert _till_state(cid, TILL_1) is None


def test_another_companys_branch_is_a_404_on_read_and_write_and_is_never_touched():
    a, cid_a, buid_a, _ = _make_shop()
    b, cid_b, buid_b, _ = _make_shop()
    assert _get(a, branch_uid=buid_b).status_code == 404
    r = _post(a, {'branch_uid': buid_b, 'branch_code': 'HAX'})
    assert r.status_code == 404, r.get_json()
    r = _post(a, {'branch_uid': buid_b, 'readable_enabled': True, 'till_number': 1})
    assert r.status_code == 404, r.get_json()
    assert _setting(cid_b, 'branch_code:' + buid_b) is None
    assert _setting(cid_a, 'branch_code:' + buid_b) is None
    assert _till_state(cid_a, TILL_1) is None
    assert _get(a, branch_uid='no-such-branch').status_code == 404


def test_a_saved_code_that_has_since_become_reserved_is_ignored():
    c, cid, buid, code = _make_shop()
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                     (cid, 'branch_code:' + buid, 'INC'))
        conn.commit()
    finally:
        conn.close()
    d = _get(c).get_json()['data']
    assert d['branch_code'] == code and d['branch_code_is_saved'] is False


# ═════════════════════════════════════════════════════════════════════════
# C. Switching OFF really stops it (F5)
# ═════════════════════════════════════════════════════════════════════════

def test_switching_off_retires_the_books_and_the_next_sale_is_the_legacy_number():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert _sale_number(c, pid, TILL_1) == code + '3-000001'
    made = {b['id'] for b in _books(cid)}
    r = _disable(c)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['readable_enabled'] is False and d['till_number'] is None and d['books_ready'] is False
    assert d['preview_is_example'] is True
    assert {b['status'] for b in _books(cid)} == {'retired'} and {b['id'] for b in _books(cid)} == made
    assert LEGACY.match(_sale_number(c, pid, TILL_1)), 'after OFF the till must be back on the legacy number'
    assert _till_state(cid, TILL_1) == {'on': False, 'till': None, 'branch_uid': None, 'series_ids': []}
    assert [a['details'] for a in _audit_rows(cid, 'NUMBERING_READABLE_SET')] == ['1:till 3', '0']
    assert len(_audit_rows(cid, 'DOC_SERIES_RETIRED')) == 3
    # the retirement travels, so the shop's other computers see the books as retired
    upd = _rows("SELECT entity_id FROM sync_outbox WHERE entity_type='doc_series' AND event_type='update'")
    assert made <= {u['entity_id'] for u in upd}


def test_switching_back_on_continues_the_counter_instead_of_repeating_numbers():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert _sale_number(c, pid, TILL_1) == code + '3-000001'
    assert _disable(c).status_code == 200
    assert LEGACY.match(_sale_number(c, pid, TILL_1))
    assert _enable(c, 3).status_code == 200, 'its own retired books are not a conflict'
    _close_shift(c, TILL_1)
    assert _open_shift(c, TILL_1).status_code == 200
    assert {b['status'] for b in _books(cid)} == {'active'} and len(_books(cid)) == 3
    assert _sale_number(c, pid, TILL_1) == code + '3-000002'


def test_switching_off_touches_only_this_computers_tracked_books():
    c, cid, buid, code = _make_shop()
    _rival_book('sale', 'ELSEWHERE', company=cid, terminal=TILL_2)       # another computer of this shop
    assert _enable(c, 3).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert _disable(c).status_code == 200
    by_code = {b['code']: b['status'] for b in _books(cid)}
    assert by_code['ELSEWHERE'] == 'active'
    assert {by_code[code + '3'], by_code[code + '3-R'], by_code[code + '3-P']} == {'retired'}


def test_switching_off_when_it_was_never_on_is_a_harmless_200():
    c, cid, buid, code = _make_shop()
    r = _disable(c)
    assert r.status_code == 200 and r.get_json()['data']['readable_enabled'] is False
    assert _books(cid) == []


def test_switching_off_on_one_computer_leaves_another_computers_switch_alone():
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3, TILL_1).status_code == 200
    assert _enable(c, 4, TILL_2).status_code == 200
    assert _disable(c, TILL_1).status_code == 200
    assert _get(c, TILL_2).get_json()['data']['till_number'] == 4
    assert _get(c, TILL_1).get_json()['data']['readable_enabled'] is False


# ═════════════════════════════════════════════════════════════════════════
# D. The shift-open hook
# ═════════════════════════════════════════════════════════════════════════

def test_nothing_books_by_itself_for_a_new_shop_or_a_shop_with_history():
    """The whole point of the fix round: no switch, no books -- ever."""
    for with_history in (False, True):
        c, cid, buid, code = _make_shop()
        pid = _product(c)
        if with_history:
            assert LEGACY.match(_sale_number(c, pid, TILL_1))
        assert _open_shift(c, TILL_1).status_code == 200
        assert _books(cid) == [], f'history={with_history}'
        assert _rows("SELECT 1 FROM retail_settings WHERE company_id=? AND skey LIKE 'numbering%'", (cid,)) == []
        assert LEGACY.match(_sale_number(c, pid, TILL_1)), f'history={with_history}'


def test_an_enabled_computer_gets_its_books_at_the_next_shift_and_the_first_sale_is_readable():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3).status_code == 200
    assert _books(cid) == []
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    books = _books(cid)
    assert {b['doc_type']: b['code'] for b in books} == {
        'sale': code + '3', 'return': code + '3-R', 'po': code + '3-P'}
    assert all(b['allocator_terminal_uid'] == TILL_1 and b['status'] == 'active'
               and b['branch_uid'] == buid and b['pad_width'] == 6 and b['start_no'] == 1 for b in books)
    assert _sale_number(c, pid, TILL_1) == code + '3-000001'
    # announced to the shop's other computers and audited
    created = {r_['entity_id'] for r_ in _rows(
        "SELECT entity_id FROM sync_outbox WHERE entity_type='doc_series' AND event_type='create'")}
    assert {b['id'] for b in books} <= created
    assert len(_audit_rows(cid, 'DOC_SERIES_AUTO_CREATED')) == 3
    # the switch now remembers its books, and still never travelled
    assert set(_till_state(cid, TILL_1)['series_ids']) == {b['id'] for b in books}
    assert _rows("SELECT 1 FROM sync_outbox WHERE payload LIKE '%numbering_till%'") == []


def test_the_preview_shows_the_real_next_number_after_sales():
    """F9."""
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 6).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    d = _get(c).get_json()['data']
    assert d['books_ready'] is True and d['preview_is_example'] is False
    assert d['preview'] == {'sale': code + '6-000001', 'return': code + '6-R-000001'}
    assert _sale_number(c, pid, TILL_1) == code + '6-000001'
    assert _sale_number(c, pid, TILL_1) == code + '6-000002'
    d = _get(c).get_json()['data']
    assert d['preview']['sale'] == code + '6-000003'
    assert d['till_numbers_in_use'] == [6]
    # and reading the preview consumed nothing
    assert _sale_number(c, pid, TILL_1) == code + '6-000003'


def test_a_code_change_after_the_books_exist_keeps_the_books_and_the_preview_says_so():
    """F6, half one: the till already has books. A new code does NOT rename them
    (codes are immutable), so a later shift must not silently make a second
    set, and the screen must say the change is for tills switched on later."""
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    r = _post(c, {'branch_code': 'NEW'})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['branch_code'] == 'NEW'
    assert d['preview']['sale'] == code + '3-000001', 'the preview must show what this till will really print'
    assert 'keeps numbering with its current books' in d['branch_code_note']
    assert 'switched on afterwards' in d['branch_code_note']
    _close_shift(c, TILL_1)
    assert _open_shift(c, TILL_1).status_code == 200
    assert {b['code'] for b in _books(cid)} == {code + '3', code + '3-R', code + '3-P'}
    assert _sale_number(c, pid, TILL_1) == code + '3-000001'


def test_a_code_change_before_the_books_exist_is_used_by_the_books():
    """F6, half two: switched on but no books yet -> the books take the new code."""
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3).status_code == 200
    d = _post(c, {'branch_code': 'NEW'}).get_json()['data']
    assert d['preview']['sale'] == 'NEW3-000001'
    assert _open_shift(c, TILL_1).status_code == 200
    assert {b['code'] for b in _books(cid)} == {'NEW3', 'NEW3-R', 'NEW3-P'}
    assert _sale_number(c, pid, TILL_1) == 'NEW3-000001'


def test_a_shift_on_a_second_computer_that_was_never_switched_on_gets_nothing():
    c, cid, buid, code = _make_shop()
    pid = _product(c)
    assert _enable(c, 3, TILL_1).status_code == 200
    assert _open_shift(c, TILL_2).status_code == 200
    assert _books(cid) == []
    assert LEGACY.match(_sale_number(c, pid, TILL_2))


def test_a_shift_on_another_branch_than_the_one_switched_on_gets_nothing():
    c, cid, buid, code = _make_shop('Abdoun Mall')
    b2_id, b2_uid = _add_branch(cid, 'Sweileh')
    assert _enable(c, 3).status_code == 200                         # branch 1
    assert _open_shift(c, TILL_1, branch_id=b2_id).status_code == 200
    assert _books(cid) == []
    _close_shift(c, TILL_1, branch_id=b2_id)
    b1_id = _rows("SELECT id FROM branches WHERE uid=?", (buid,))[0]['id']
    assert _open_shift(c, TILL_1, branch_id=b1_id).status_code == 200
    assert len(_books(cid)) == 3          # the allow half: the right branch does get them


def test_a_device_that_is_behind_on_sync_creates_nothing_but_the_shift_opens(monkeypatch):
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False, 'seconds_since_last_success': 10 ** 9})
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []
    monkeypatch.setattr(retail_api, '_sync_get_active_health', lambda: {
        'configured': True, 'never_synced': False, 'seconds_since_last_success': 1})
    _close_shift(c, TILL_1)
    assert _open_shift(c, TILL_1).status_code == 200
    assert len(_books(cid)) == 3


def test_a_branch_without_a_wire_uid_creates_nothing_but_the_shift_opens():
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    conn = get_retail_conn()
    try:
        conn.execute("UPDATE branches SET uid=NULL WHERE company_id=?", (cid,))
        conn.commit()
    finally:
        conn.close()
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []


def test_a_device_that_lost_its_identity_creates_nothing_but_the_shift_opens(caplog):
    """The no-identity guard is what says WHY nothing happened. Without it the
    outcome (no books, shift open) is identical -- the switch lookup for a
    missing terminal also reads as off -- so the outcome alone cannot tell the
    guard ran. The logged reason can: MUT (remove the guard) -> the log says
    'not switched on' instead and this goes RED."""
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    with caplog.at_level('INFO', logger=retail_api.log.name):
        r = _open_shift(c, None)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == []
    reasons = [rec.getMessage() for rec in caplog.records if 'readable numbering skipped' in rec.getMessage()]
    assert reasons == ['readable numbering skipped: this device has no identity yet'], reasons


@pytest.mark.parametrize("raw", [
    'not json at all', '[]', '{}', '{"on": true}', '{"on": true, "till": 0, "branch_uid": "%B"}',
    '{"on": true, "till": 100, "branch_uid": "%B"}', '{"on": true, "till": true, "branch_uid": "%B"}',
    '{"on": true, "till": "3", "branch_uid": "%B"}',
    # Every field but `on` is valid here, on purpose: with a wrong branch_uid these
    # would be skipped for that reason and the `on is True` check could be
    # loosened (MUT: `state.get('on')` truthy) without any test noticing.
    '{"on": "true", "till": 3, "branch_uid": "%B"}', '{"on": 1, "till": 3, "branch_uid": "%B"}',
])
def test_an_unreadable_switch_counts_as_off_and_never_books(raw):
    c, cid, buid, code = _make_shop()
    raw = raw.replace('%B', buid)
    conn = get_retail_conn()
    try:
        conn.execute("INSERT INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                     (cid, 'numbering_till:' + TILL_1, raw))
        conn.commit()
    finally:
        conn.close()
    assert _get(c).get_json()['data']['readable_enabled'] is False
    assert _open_shift(c, TILL_1).status_code == 200
    assert _books(cid) == []


def test_a_failure_inside_the_hook_can_never_fail_the_shift(monkeypatch):
    """The drawer is already committed when the hook runs. MUT: remove the
    hook's `except Exception` -> the open comes back 500 and this goes RED."""
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200

    def boom(*a, **k):
        raise RuntimeError('inspector exploded')
    monkeypatch.setattr(doc_series, 'inspect_till_books', boom)
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == []
    with at_terminal(TILL_1):
        assert c.get('/api/sub/retail/cash-sessions/current').get_json()['data'] is not None


def _racing_inspect(rival_code_for):
    real = doc_series.inspect_till_books

    def racing(conn, company_id, terminal, branch_code, till):
        out = real(conn, company_id, terminal, branch_code, till)
        # a rival takes the return code AFTER we looked and BEFORE we insert
        _rival_book('return', out['codes'][rival_code_for])
        return out
    return racing


def test_a_taken_code_race_is_swallowed_and_leaves_no_half_set_of_books(monkeypatch):
    """Another computer grabbed a code between our read and our insert: the
    unique index fires on the 2nd book. All-or-nothing: no orphan sale book
    remains, and the shift is open."""
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    monkeypatch.setattr(doc_series, 'inspect_till_books', _racing_inspect('return'))
    r = _open_shift(c, TILL_1)
    assert r.status_code == 200, r.get_json()
    assert _books(cid) == [], 'a half set of books must not survive a refused insert'
    assert _till_state(cid, TILL_1)['series_ids'] == []


def test_a_refused_insert_is_rolled_back_on_the_callers_own_connection(monkeypatch):
    """The route closes its connection right after the hook and SQLite drops an
    uncommitted transaction on close, so the outcome above is the same with or
    without `conn.rollback()`. This calls the hook directly and looks at the SAME
    connection afterwards -- the only place a leftover half set is visible.
    MUT: remove the IntegrityError branch's `conn.rollback()` -> RED."""
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3).status_code == 200
    monkeypatch.setattr(doc_series, 'inspect_till_books', _racing_inspect('return'))
    conn = get_retail_conn()
    try:
        bid = conn.execute("SELECT id FROM branches WHERE company_id=?", (cid,)).fetchone()['id']
        with at_terminal(TILL_1):
            retail_api._book_till_at_shift_open(conn, cid, bid)
        assert not conn.in_transaction, 'the refused insert left a transaction open on the caller connection'
        left = conn.execute("SELECT doc_type FROM doc_series WHERE company_id=?", (cid,)).fetchall()
        assert left == [], 'a half set of books is still visible on the caller connection'
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════
# E. Two computers
# ═════════════════════════════════════════════════════════════════════════

def test_a_number_that_a_known_computer_already_holds_is_refused_on_the_second_one():
    """In-process half of the two-computer proof (the real two-process half is
    retail_p1b_numbering_twodevice_test.py): once the first computer's books are
    known here, the same number is refused, and a different one is accepted."""
    c, cid, buid, code = _make_shop()
    assert _enable(c, 3, TILL_1).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200                 # TILL_1 now owns <code>3
    seen_from_2 = _get(c, TILL_2).get_json()['data']
    assert seen_from_2['till_numbers_in_use'] == [3]
    r = _enable(c, 3, TILL_2)
    assert r.status_code == 409 and r.get_json().get('code') == 'TILL_NUMBER_TAKEN', r.get_json()
    assert _till_state(cid, TILL_2) is None
    assert _enable(c, 4, TILL_2).status_code == 200
    assert _open_shift(c, TILL_2).status_code == 200
    by_till = {}
    for b in _books(cid):
        by_till.setdefault(b['allocator_terminal_uid'], set()).add(b['code'])
    assert by_till == {TILL_1: {code + '3', code + '3-R', code + '3-P'},
                       TILL_2: {code + '4', code + '4-R', code + '4-P'}}


def test_the_computers_switch_never_reaches_the_sync_outbox_but_the_branch_code_does():
    """Both halves in one test on purpose: 'nothing is synced' is meaningless
    unless the same run shows that a shop-wide setting IS."""
    c, cid, buid, code = _make_shop()
    assert _post(c, {'branch_code': 'SYN'}).status_code == 200
    assert _enable(c, 3).status_code == 200
    assert _open_shift(c, TILL_1).status_code == 200
    assert _disable(c).status_code == 200
    assert _enable(c, 3).status_code == 200
    payloads = [r['payload'] for r in _rows("SELECT payload FROM sync_outbox WHERE entity_type='retail_setting'")]
    assert any('branch_code:' + buid in p for p in payloads), 'the branch code must sync'
    assert not any('numbering_till' in p for p in payloads), 'the per-computer switch must never sync'
    assert _rows("SELECT 1 FROM sync_outbox WHERE payload LIKE '%numbering_till%'") == []
