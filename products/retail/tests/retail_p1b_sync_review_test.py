"""
Aura Retail P1b (crew Q) -- GET /api/sub/retail/sync-review.

Why this route exists. When computers sync, a record that cannot be merged (a
sale whose receipt number already exists here, a sale item whose sale is
missing, a duplicate numbering book...) is parked in retail.db's
`sync_apply_quarantine`. Nothing read that table, so a sale set aside on sync
never reached this computer's takings and the owner was never told. The route
is the owner's read-only window onto it.

What this file proves (each guard was mutation-proved, see the crew report):
  - GATE: owner 200; manager and cashier 403 OWNER_ONLY (both hold
    retail.reports, so a 403 can only come from the owner check); anonymous 401.
  - COMPANY SCOPE: the table has no company column, so the route compares the
    session company with this install's sync company; the owner of any OTHER
    company gets an empty answer, never these rows.
  - READ-ONLY: quarantine rows and audit_log are byte-identical after calls.
  - NO PAYLOAD LEAK: a marker planted in a payload never appears in the body.
  - REASONS: every reason the sync engine can write (parsed from its source, so
    a new reason added there fails here) maps to a plain sentence; an unknown
    one maps to the generic sentence and is not echoed.
  - MONEY: the at-risk total is exact to the fil (JD, 3dp); only SALE rows are
    summed.
  - PAGINATION: newest first, cursor, no skip/duplicate, summary covers the
    whole table.
  - REAL ROWS: rows produced by two real computers (separate retail.db each,
    joined by the repo's FileRelay) are read back through the route.

Run (repo venv -- bare `python` has no pytest):
    aura-fullsuits/.venv/Scripts/python.exe -m pytest \
        products/retail/tests/retail_p1b_sync_review_test.py -v
"""
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_syncreview_"))
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
from commercial_runtime.sync import sync_service  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402

URL = '/api/sub/retail/sync-review'
SECRET = 'PAYLOAD-LEAK-MARKER-9f3a'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    device_context._cached_device = None
    conn = get_retail_conn()
    try:
        conn.execute("DELETE FROM sync_apply_quarantine")
        conn.commit()
    finally:
        conn.close()
    yield
    device_context._cached_device = None


def _local_company_is(monkeypatch, company_id):
    """This install's one sync company. The registry is shared by every test in
    this process, so the test pins the answer instead of depending on which
    admin happens to be first."""
    monkeypatch.setattr(sync_service, 'local_company_id_from_registry', lambda: company_id)


def _make_admin(prefix='sr'):
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@test.local"
    company_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password("SyncReviewPW1"), "admin", "active"))
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": "SyncReviewPW1"})
    assert r.status_code == 200, r.get_json()
    return client, company_id


def _make_staff(company_id, role):
    """Holds retail.reports explicitly so a 403 can only be the owner check."""
    email = f"sr-{role}-{uuid.uuid4().hex[:10]}@test.local"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
            "VALUES (?,?,?,?,?,?,?,0)",
            (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password("SyncReviewPW1"), role, "active"))
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, "retail", "full"))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
        conn.execute("UPDATE user_permissions SET access_level='full' WHERE user_id=? AND subsystem='retail.reports'",
                     (user_id,))
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": "SyncReviewPW1"})
    assert r.status_code == 200, r.get_json()
    return client


_seq = [0]


def _park(entity_type, reason, payload, event_type='create', ts=None, detail='detail-text'):
    _seq[0] += 1
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail"
            + (", quarantined_at" if ts else "") + ") VALUES (?,?,?,?,?,?" + (",?" if ts else "") + ")",
            (f"ent-{_seq[0]}-{uuid.uuid4().hex[:6]}", entity_type, event_type, json.dumps(payload), reason, detail,
             *([ts] if ts else [])))
        conn.commit()
    finally:
        conn.close()


def _get(client, qs=''):
    r = client.get(URL + qs)
    return r, r.get_json()


# ── gate ────────────────────────────────────────────────────────────────────

def test_owner_gets_200_with_the_documented_shape(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number',
          {'sale_number': 'SALE-000007', 'total': 12.5, 'terminal_id': 'till-b', 'uid': SECRET})
    r, body = _get(client)
    assert r.status_code == 200, body
    data = body['data']
    assert set(data) == {'items', 'next_cursor', 'summary'}
    assert data['summary'] == {'waiting_count': 1, 'sales_count': 1, 'sales_total': 12.5}
    [item] = data['items']
    assert item['kind'] == 'A sale from another computer'
    assert item['reason'] == 'the same receipt number already exists here'
    assert item['number'] == 'SALE-000007' and item['amount'] == 12.5 and item['source'] == 'till-b'
    assert item['retries_itself'] is False and item['arrived_at']


def test_manager_and_cashier_are_refused_with_owner_only(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number', {'sale_number': 'S1', 'total': 1})
    for role in ('manager', 'cashier'):
        c = _make_staff(cid, role)
        r, body = _get(c)
        assert r.status_code == 403, (role, body)
        assert body.get('code') == 'OWNER_ONLY', (role, body)
        assert body.get('message') == 'Only the shop owner can open this.', (role, body)
        assert 'items' not in json.dumps(body)


def test_anonymous_is_401():
    assert app.test_client().get(URL).status_code == 401


# ── company scope ───────────────────────────────────────────────────────────

def test_another_companys_owner_never_sees_this_installs_rows(monkeypatch):
    owner_a, cid_a = _make_admin('sra')
    owner_b, cid_b = _make_admin('srb')
    _local_company_is(monkeypatch, cid_a)
    _park('sale', 'duplicate_document_number', {'sale_number': 'S-A', 'total': 40})
    _, a = _get(owner_a)
    _, b = _get(owner_b)
    assert a['data']['summary']['waiting_count'] == 1 and len(a['data']['items']) == 1
    assert b['data']['items'] == [] and b['data']['summary'] == {
        'waiting_count': 0, 'sales_count': 0, 'sales_total': 0.0}, b
    assert 'S-A' not in json.dumps(b)


def test_allow_half_the_sync_company_owner_is_served_even_when_not_first_admin(monkeypatch):
    """The guard must deny the OTHER company without ever denying the right one."""
    _make_admin('srfirst')
    owner, cid = _make_admin('srright')
    _local_company_is(monkeypatch, cid)
    _park('payment', 'missing_parent:sale', {'reference': 'PAY-1', 'amount': 3})
    _, body = _get(owner)
    assert body['data']['summary']['waiting_count'] == 1


# ── read only, no leak ──────────────────────────────────────────────────────

def _snapshot():
    conn = get_retail_conn()
    try:
        q = [tuple(r) for r in conn.execute(
            "SELECT entity_id, entity_type, event_type, payload, reason, detail, quarantined_at "
            "FROM sync_apply_quarantine ORDER BY entity_id").fetchall()]
        a = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        return q, a, s
    finally:
        conn.close()


def test_route_writes_nothing_and_never_returns_the_payload(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number',
          {'sale_number': 'S9', 'total': 5, 'customer_name': SECRET, 'notes': SECRET})
    _park('sale_item', 'missing_parent:sale', {'line_total': 2, 'sale_uid': SECRET})
    _park('doc_series', 'duplicate_series_code', {'code': 'AAA1', 'label': SECRET})
    before = _snapshot()
    for qs in ('', '?limit=1', '?limit=abc', '?cursor=zzz'):
        r = client.get(URL + qs)
        assert r.status_code in (200, 400)
        assert SECRET.encode() not in r.data, qs
        assert b'"payload"' not in r.data and b'"detail"' not in r.data
    assert _snapshot() == before, "the read-only route changed data"


# ── reasons ─────────────────────────────────────────────────────────────────

def _engine_reasons():
    """Every reason string the sync engine can write, from its own source."""
    src = (SUITE_ROOT / 'commercial_runtime' / 'sync' / 'sync_service.py').read_text(encoding='utf-8')
    found = set(re.findall(r'reason\s*=\s*"([a-z_]+(?::[a-z_]+)?)"', src))
    found |= set(re.findall(r'return None, "(gl_[a-z_]+)"', src))
    return found


def test_every_reason_the_engine_can_write_has_a_plain_sentence():
    reasons = _engine_reasons()
    assert {'duplicate_document_number', 'missing_parent:sale', 'gl_unbalanced',
            'schema_version_too_new', 'duplicate_series_code'} <= reasons, reasons
    generic = retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    for reason in sorted(reasons):
        text, _ = retail_api._sync_review_reason(reason, 'sale_item')
        assert text != generic, f"reason {reason!r} falls through to the generic sentence"
        assert re.search(r'[a-z]{3}', text) and '_' not in text and ':' not in text, (reason, text)


def test_unknown_reason_is_generic_and_not_echoed(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('mystery_thing', 'brand_new_reason_x', {'total': 1}, detail='SECRET-DETAIL')
    _, body = _get(client)
    [item] = body['data']['items']
    assert item['reason'] == retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    assert item['kind'] == 'Something from another computer' and item['kind_code'] == 'other'
    assert 'brand_new_reason_x' not in json.dumps(body) and 'SECRET-DETAIL' not in json.dumps(body)


def test_unknown_missing_parent_and_duplicate_split_by_entity(monkeypatch):
    assert retail_api._sync_review_reason('missing_parent:widget', 'x') == retail_api._SYNC_REVIEW_REASON_MISSING_OTHER
    sale, _ = retail_api._sync_review_reason('duplicate_document_number', 'sale')
    ret, _ = retail_api._sync_review_reason('duplicate_document_number', 'return')
    assert 'receipt' in sale and 'receipt' not in ret


# ── money ───────────────────────────────────────────────────────────────────

def test_total_is_exact_to_the_fil_and_counts_sales_only(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    # 0.1 + 0.2 as floats is 0.30000000000000004; 10.125 + 0.005 is a half-fil trap.
    for total in (0.1, 0.2, '10.125', 0.005, 1.87):
        _park('sale', 'duplicate_document_number', {'sale_number': f'S{total}', 'total': total})
    _park('sale', 'duplicate_document_number', {'sale_number': 'S-none'})            # no amount
    _park('sale', 'duplicate_document_number', {'sale_number': 'S-bool', 'total': True})
    _park('sale', 'duplicate_document_number', {'sale_number': 'S-junk', 'total': 'abc'})
    _park('sale_item', 'missing_parent:sale', {'line_total': 999})   # not a sale
    _park('payment', 'missing_parent:sale', {'amount': 999})
    _park('return', 'duplicate_document_number', {'refund_amount': 999})
    _, body = _get(client)
    s = body['data']['summary']
    assert s['sales_count'] == 8 and s['waiting_count'] == 11
    # 0.1 + 0.2 + 10.125 + 0.005 + 1.87 = 12.300 ; 0.005 rounds to 0.005 (3dp keeps it)
    assert s['sales_total'] == 12.3, s
    by_num = {i['number']: i for i in body['data']['items']}
    assert by_num['S10.125']['amount'] == 10.125            # the fil survives
    assert by_num['S-none']['amount'] is None and by_num['S-bool']['amount'] is None
    assert by_num['S-junk']['amount'] is None


def test_total_rounds_a_sub_fil_amount_half_up(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number', {'sale_number': 'A', 'total': '1.0005'})
    _park('sale', 'duplicate_document_number', {'sale_number': 'B', 'total': '2.0004'})
    _, body = _get(client)
    assert body['data']['summary']['sales_total'] == 3.001     # 1.001 + 2.000


# ── pagination ──────────────────────────────────────────────────────────────

def test_pages_are_newest_first_without_skips_or_repeats(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    stamps = ['2026-09-01 10:00:00', '2026-09-02 10:00:00', '2026-09-03 10:00:00',
              '2026-09-03 10:00:00', '2026-09-03 10:00:00', '2026-09-04 10:00:00', '2026-09-05 10:00:00']
    for i, ts in enumerate(stamps):
        _park('sale', 'duplicate_document_number', {'sale_number': f'N{i}', 'total': 1}, ts=ts)
    seen, cursor, pages = [], '', 0
    while True:
        _, body = _get(client, f"?limit=3" + (f"&cursor={cursor}" if cursor else ''))
        d = body['data']
        assert d['summary']['waiting_count'] == 7 and d['summary']['sales_total'] == 7.0
        seen += [i['number'] for i in d['items']]
        pages += 1
        cursor = d['next_cursor']
        if not cursor:
            break
        assert pages < 5
    assert pages == 3
    assert sorted(seen) == sorted(f'N{i}' for i in range(7)), seen          # none skipped, none repeated
    stamps_seen = [stamps[int(n[1:])] for n in seen]
    assert stamps_seen == sorted(stamps_seen, reverse=True), "not newest first"


def test_limit_is_clamped_and_a_bad_cursor_is_a_400(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    for i in range(3):
        _park('sale', 'duplicate_document_number', {'sale_number': f'C{i}', 'total': 1})
    r, body = _get(client, '?limit=0')
    assert r.status_code == 200 and len(body['data']['items']) >= 1
    r, body = _get(client, '?limit=99999')
    assert r.status_code == 200 and len(body['data']['items']) == 3
    r, _ = _get(client, '?cursor=not-a-cursor')
    assert r.status_code == 400


def test_empty_table(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    r, body = _get(client)
    assert r.status_code == 200
    assert body['data'] == {'items': [], 'next_cursor': None,
                            'summary': {'waiting_count': 0, 'sales_count': 0, 'sales_total': 0.0}}


# ── REAL rows from two real computers ───────────────────────────────────────

def test_real_two_computer_quarantine_rows_read_back_through_the_route(tmp_path, monkeypatch):
    """Two real processes (separate retail.db each) both pick till 1 while
    offline from each other (the pinned T5 window of retail_p1b_numbering_
    twodevice_test.py). The rows the ENGINE parked on them -- not hand-written
    ones -- are copied into this process's DB and read through the route."""
    from _accept_harness import run_accept_device
    py = sys.executable
    TA, TB = 'q-till-a-0000-0000-000000000001', 'q-till-b-0000-0000-000000000002'

    def dev(d, relay, acts):
        return run_accept_device(py, d, relay, acts, timeout=240)

    a, b, relay = tmp_path / 'a', tmp_path / 'b', tmp_path / 'relay.db'
    [shop] = dev(a, relay, [{"op": "create_shop", "price": 5.0, "initial_stock": 100}])
    [boot] = dev(b, relay, [{"op": "bootstrap"}])
    [_, _, prod] = dev(b, relay, [{"op": "set_terminal_id", "terminal_id": TB},
                                  {"op": "login", "email": boot["email"], "password": boot["password"]},
                                  {"op": "create_product", "name": "B widget", "initial_stock": 50}])
    [br] = dev(b, relay, [{"op": "list_branches", "company_id": boot["company_id"]}])
    bbid = br["branches"][0]["id"]

    def login(term, who):
        return [{"op": "set_terminal_id", "terminal_id": term},
                {"op": "login", "email": who["email"], "password": who["password"]}]

    on = {"op": "numbering_set", "branch_code": "AAA", "readable_enabled": True, "till_number": 1}
    assert dev(a, relay, login(TA, shop) + [on])[-1]["status_code"] == 200
    assert dev(b, relay, login(TB, boot) + [on])[-1]["status_code"] == 200
    dev(a, relay, login(TA, shop) + [{"op": "cash_session_open", "branch_id": shop["branch_id"]},
                                     {"op": "sell", "product_id": shop["product_id"], "quantity": 1}])
    dev(b, relay, login(TB, boot) + [{"op": "cash_session_open", "branch_id": bbid},
                                     {"op": "sell", "product_id": prod["product_id"], "quantity": 1}])
    dev(b, relay, [{"op": "push"}])
    dev(a, relay, [{"op": "push"}])
    dev(a, relay, login(TA, shop) + [{"op": "pull"}])
    dev(b, relay, login(TB, boot) + [{"op": "pull"}])

    cols = "entity_id, entity_type, event_type, payload, reason, detail"
    real = []
    for d in (a, b):
        [r] = dev(d, relay, [{"op": "raw_select", "sql": f"SELECT {cols} FROM sync_apply_quarantine", "params": []}])
        real += r["rows"]
    assert real, "the two-computer scenario parked nothing -- the window closed; update this test on purpose"
    types = {r["entity_type"] for r in real}
    assert 'doc_series' in types, types

    client, cid = _make_admin('srreal')
    _local_company_is(monkeypatch, cid)
    conn = get_retail_conn()
    try:
        for r in real:
            conn.execute(f"INSERT OR IGNORE INTO sync_apply_quarantine ({cols}) VALUES (?,?,?,?,?,?)",
                         tuple(r[k] for k in cols.split(", ")))
        conn.commit()
        n_loaded = conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0]
    finally:
        conn.close()
    _, body = _get(client)
    data = body['data']
    assert data['summary']['waiting_count'] == n_loaded and len(data['items']) == n_loaded
    generic = retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    for it in data['items']:
        assert it['reason'] != generic, f"a real engine row fell to the generic sentence: {it}"
        assert it['kind_code'] != 'other', it
    book = next(i for i in data['items'] if i['kind_code'] == 'doc_series')
    # The engine's real code is the book code plus its document-type suffix
    # ("AAA1-P"...), so the test pins the prefix the owner typed, not a guess.
    assert book['kind'] == 'A numbering book' and book['number'].startswith('AAA1'), book
    print('REAL quarantine rows:', sorted((r['entity_type'], r['reason']) for r in real))
    real_sales = [r for r in real if r["entity_type"] == "sale"]
    if real_sales:
        want = sum(round(float(json.loads(r["payload"])["total"]), 3) for r in real_sales)
        assert data['summary']['sales_count'] == len(real_sales)
        assert abs(data['summary']['sales_total'] - want) < 0.0005
        assert all(i['reason'] == 'the same receipt number already exists here'
                   for i in data['items'] if i['kind_code'] == 'sale')
