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
    # Coordinator item 2 added the two `returns_*` keys: parked refunds are
    # counted on their own, so the summary is compared whole (a key that
    # silently vanished or a sale leaking into the refund figure fails here).
    assert data['summary'] == {'waiting_count': 1, 'sales_count': 1, 'sales_total': 12.5,
                               'returns_count': 0, 'returns_total': 0.0}
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
        'waiting_count': 0, 'sales_count': 0, 'sales_total': 0.0,
        'returns_count': 0, 'returns_total': 0.0}, b
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


_S_AUTH_REASONS = (
    'unsigned_authority_event', 'bad_signature', 'foreign_licence', 'untrusted_lease',
    'signer_binding_clash', 'signer_revoked', 'takeover_main_not_silent',
    'device_change_needs_main', 'owner_change_needs_main', 'second_admin_refused',
    'needs_main_computer', 'main_unknown', 'release_ack_wrong_sender',
    'alias_origin_mismatch',
)


def test_every_s_auth_park_reason_has_a_plain_sentence_in_both_languages():
    """AUDIT-033 (D6): the signed-authority gate parks with these reasons. The
    engine-source scan above cannot see them until the gate lands, so they are
    listed here; a missing sentence (or a missing Arabic one) fails this."""
    root = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'locales')
    with open(os.path.join(root, 'ar.json'), encoding='utf-8') as f:
        ar = json.load(f)
    with open(os.path.join(root, 'en.json'), encoding='utf-8') as f:
        en = json.load(f)
    generic = retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    for reason in _S_AUTH_REASONS:
        text, _ = retail_api._sync_review_reason(reason, 'user')
        assert text != generic, reason
        assert '_' not in text and ':' not in text, (reason, text)
        assert en.get(text) == text, f"en.json lacks the sentence for {reason!r}"
        assert ar.get(text) and ar[text] != text, f"ar.json lacks Arabic for {reason!r}"


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
    # The parked refund is its OWN figure (item 2) and is never netted against
    # the sales: sales_total above must stay 12.3 with a 999 refund parked.
    assert s['returns_count'] == 1 and s['returns_total'] == 999.0, s
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
                            'summary': {'waiting_count': 0, 'sales_count': 0, 'sales_total': 0.0,
                                        'returns_count': 0, 'returns_total': 0.0}}


# ── returns: the same honesty a parked sale already gets (coordinator item 2) ─
#
# The P1b verifier (vb c1-c3) showed the screen said "this will retry" about
# rows that can never resolve, one level below the sale: a RETURN of a
# permanently parked sale, and a return_item of a permanently parked RETURN.
# Their parent is not "missing", it is set aside for good, so nothing will ever
# bring it back. The three tests below are ported from that attack file; the
# first two were RED against the code before this change ("retries_itself" was
# True), the third is the ALLOW half and must stay green.

def _rows_by_kind(items, *kinds):
    return [i for i in items if i['kind_code'] in kinds]


def test_a_return_of_a_permanently_parked_sale_does_not_claim_it_retries(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number', {'uid': 'ps-1', 'sale_number': 'AAA1-000001', 'total': 50})
    _park('return', 'missing_parent:sale', {'uid': 'rt-1', 'sale_uid': 'ps-1',
                                            'return_number': 'AAA1-R-000001', 'refund_amount': 20})
    _, body = _get(client)
    [ret] = _rows_by_kind(body['data']['items'], 'return')
    assert ret['retries_itself'] is False, ret
    assert ret['parent_parked'] is True and ret['note'], ret


def test_children_of_a_permanently_parked_return_do_not_claim_they_retry(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('return', 'duplicate_document_number', {'uid': 'rt-2', 'sale_uid': 'x',
                                                  'return_number': 'AAA1-R-000002', 'refund_amount': 20})
    _park('return_item', 'missing_parent:return', {'uid': 'ri-2', 'return_uid': 'rt-2', 'quantity': 1})
    _, body = _get(client)
    kids = [i for i in body['data']['items'] if i['kind_code'] not in ('return', 'sale')]
    assert kids, body['data']['items']
    assert all(i['retries_itself'] is False and i['parent_parked'] is True and i['note'] for i in kids), kids


def test_a_chain_sale_then_return_then_return_item_all_stop_claiming_a_retry(monkeypatch):
    """The whole ladder: parked sale -> its return waiting on it -> that
    return's item. Each rung is decided from the rung above."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number', {'uid': 'ps-9', 'sale_number': 'S9', 'total': 50})
    _park('return', 'missing_parent:sale', {'uid': 'rt-9', 'sale_uid': 'ps-9', 'refund_amount': 5})
    _park('return_item', 'missing_parent:return', {'uid': 'ri-9', 'return_uid': 'rt-9', 'quantity': 1})
    _, body = _get(client)
    by = {i['kind_code']: i for i in body['data']['items']}
    assert by['return']['retries_itself'] is False and by['return_item']['retries_itself'] is False, by


def test_allow_half_a_merely_missing_parent_still_says_it_retries(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale_item', 'missing_parent:sale', {'sale_uid': 'late-sale', 'quantity': 1})
    _park('payment', 'missing_parent:sale', {'sale_uid': 'late-sale', 'related_type': 'sale', 'amount': 1})
    # Same allow-half for the return ladder: a return waiting on a sale that has
    # simply not arrived yet, and an item waiting on THAT return (same uid: an
    # earlier draft pointed the item at a different, absent return, which let a
    # mutation that treats every return as parked go unnoticed -- the item could
    # not tell, because it never referred to a return that was in the table).
    _park('return', 'missing_parent:sale', {'uid': 'rt-late', 'sale_uid': 'not-parked', 'refund_amount': 3})
    _park('return_item', 'missing_parent:return', {'uid': 'ri-late', 'return_uid': 'rt-late', 'quantity': 1})
    _, body = _get(client)
    items = body['data']['items']
    assert len(items) == 4
    assert all(i['retries_itself'] is True and i['parent_parked'] is False and i['note'] is None
               for i in items), items


# Track S (crew Y, 52aa477c): a set-aside sale's/return's stock movement and journal
# now park WITH it, so the flag `stock_and_books_applied` is DERIVED from what is in
# this computer's tables (gl_journals by (source_type, source_uid); inventory_movements
# by (type, reference, terminal_id)), never assumed. Both halves are pinned below:
#   deny  -> nothing of the document is here  -> False + the "set aside with it" wording
#   allow -> a journal or a movement IS here  -> True  + the old "WERE added" wording
# (a till that took the row on the older engine really did add them).

def _uid():
    return 'u-' + uuid.uuid4().hex[:12]


def _plant_journal(source_type, source_uid, company_id=1):
    """A journal of the document exists here. gl_journals is append-only (a delete
    ABORTs), so tests use fresh uids instead of cleaning up."""
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO gl_journals (company_id, business_date, source_type, source_uid, currency) "
            "VALUES (?, '2026-09-30', ?, ?, 'JOD')", (company_id, source_type, source_uid))
        conn.commit()
    finally:
        conn.close()


def _plant_movement(movement_type, reference, terminal_id):
    conn = get_retail_conn()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")     # the product is irrelevant to what is read
        conn.execute(
            "INSERT INTO inventory_movements (product_id, movement_type, quantity, reference, terminal_id) "
            "VALUES (999999, ?, 1, ?, ?)", (movement_type, reference, terminal_id))
        conn.commit()
    finally:
        conn.close()


def _one(body, kind):
    [row] = _rows_by_kind(body['data']['items'], kind)
    return row


def _dup_sale(uid=None, number=None, terminal='T-SENDER'):
    uid = uid or _uid()
    number = number or f"S-{uid}"
    _park('sale', 'duplicate_document_number',
          {'uid': uid, 'sale_number': number, 'terminal_id': terminal, 'total': 10})
    return uid, number, terminal


def _dup_return(uid=None, number=None, terminal='T-SENDER', sale_uid='x'):
    uid = uid or _uid()
    number = number or f"R-{uid}"
    _park('return', 'duplicate_document_number',
          {'uid': uid, 'return_number': number, 'terminal_id': terminal, 'sale_uid': sale_uid,
           'refund_amount': '7.5'})
    return uid, number, terminal


def test_deny_half_a_parked_sale_with_nothing_of_it_here_says_it_was_set_aside_with_it(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _dup_sale()
    _, body = _get(client)
    sale = _one(body, 'sale')
    assert sale['stock_and_books_applied'] is False and sale['retries_itself'] is False, sale
    assert 'set aside with it' in sale['note'] and 'WERE added' not in sale['note'], sale


def test_allow_half_a_parked_sale_whose_journal_is_here_says_it_was_applied(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    uid, _, _ = _dup_sale()
    _plant_journal('sale', uid)
    _, body = _get(client)
    sale = _one(body, 'sale')
    assert sale['stock_and_books_applied'] is True and sale['retries_itself'] is False, sale
    assert 'WERE added' in sale['note'] and 'accounting' in sale['note'] and 'stock' in sale['note'], sale


def test_allow_half_a_parked_sale_whose_movement_is_here_says_it_was_applied(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _, number, terminal = _dup_sale()
    _plant_movement('sale_out', number, terminal)
    _, body = _get(client)
    sale = _one(body, 'sale')
    assert sale['stock_and_books_applied'] is True and 'WERE added' in sale['note'], sale


def test_the_winners_movement_with_the_same_number_is_not_mistaken_for_the_losers(monkeypatch):
    """The computer whose sale WON the number holds a sale_out with the SAME
    reference but ITS OWN terminal. That is not the parked sale's movement, so the
    flag stays False -- this is why terminal_id is part of the key."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _, number, _ = _dup_sale(terminal='T-LOSER')
    _plant_movement('sale_out', number, 'T-WINNER')
    _, body = _get(client)
    assert _one(body, 'sale')['stock_and_books_applied'] is False


def test_a_journal_or_movement_of_the_wrong_kind_does_not_count(monkeypatch):
    """A RETURN journal with the same uid text, or a return_in movement with the
    sale's number, is not the sale's stock or books."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    uid, number, terminal = _dup_sale()
    _plant_journal('return', uid)
    _plant_movement('return_in', number, terminal)
    _, body = _get(client)
    assert _one(body, 'sale')['stock_and_books_applied'] is False


def test_only_the_document_that_has_a_journal_is_flagged(monkeypatch):
    """Two parked sales, one with a journal here: each row gets its OWN answer."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    with_j, _, _ = _dup_sale()
    without, _, _ = _dup_sale()
    _plant_journal('sale', with_j)
    _, body = _get(client)
    got = [i['stock_and_books_applied'] for i in body['data']['items'] if i['kind_code'] == 'sale']
    assert sorted(got) == [False, True], got


def test_the_movement_and_journal_parked_with_a_dead_sale_do_not_claim_they_retry(monkeypatch):
    """Y's engine parks a set-aside sale's stock movement and journal as
    `missing_parent:sale`. If that sale is permanently set aside they can never
    resolve, so the screen must not promise a retry (deny half). The allow half:
    the same rows for a sale that is merely late keep saying they retry."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    dead, _, _ = _dup_sale()
    ret_dead, _, _ = _dup_return()
    late = _uid()
    _park('inventory_movement', 'missing_parent:sale',
          {'uid': _uid(), 'source_type': 'sale', 'source_uid': dead, 'movement_type': 'sale_out'})
    _park('gl_journal', 'missing_parent:sale', {'uid': _uid(), 'source_type': 'sale', 'source_uid': dead})
    _park('gl_journal', 'missing_parent:return', {'uid': _uid(), 'source_type': 'return', 'source_uid': ret_dead})
    _park('inventory_movement', 'missing_parent:sale',
          {'uid': _uid(), 'source_type': 'sale', 'source_uid': late, 'movement_type': 'sale_out'})
    _, body = _get(client)
    kids = [i for i in body['data']['items'] if i['kind_code'] in ('inventory_movement', 'gl_journal')]
    assert len(kids) == 4, kids
    stuck = [i for i in kids if i['parent_parked']]
    assert len(stuck) == 3 and all(i['retries_itself'] is False and i['note'] for i in stuck), kids
    [waiting] = [i for i in kids if not i['parent_parked']]
    assert waiting['retries_itself'] is True and waiting['note'] is None, waiting


def test_deny_half_a_parked_duplicate_return_with_nothing_here_says_it_was_set_aside(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _dup_return()
    _, body = _get(client)
    ret = _one(body, 'return')
    assert ret['stock_and_books_applied'] is False and ret['retries_itself'] is False, ret
    assert 'set aside with it' in ret['note'] and 'WERE added' not in ret['note'], ret


def test_allow_half_a_parked_duplicate_return_with_its_journal_or_movement_here(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    by_journal, _, _ = _dup_return()
    _, number, terminal = _dup_return()
    _plant_journal('return', by_journal)
    _plant_movement('return_in', number, terminal)
    _, body = _get(client)
    rets = _rows_by_kind(body['data']['items'], 'return')
    assert len(rets) == 2 and all(r['stock_and_books_applied'] is True for r in rets), rets
    assert all('stock' in r['note'] and 'accounting' in r['note'] and 'WERE added' in r['note']
               for r in rets), rets


def test_a_parked_duplicate_return_of_a_parked_sale_keeps_its_own_stronger_note(monkeypatch):
    """Parked for two permanent reasons: the stock-and-books sentence is the
    more important one and must win over the generic 'sale was set aside'. Both
    halves: journal here -> applied wording; nothing here -> set-aside wording."""
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    sale_uid, _, _ = _dup_sale()
    ret_uid, _, _ = _dup_return(sale_uid=sale_uid)
    _, body = _get(client)
    ret = _one(body, 'return')
    assert ret['stock_and_books_applied'] is False and ret['retries_itself'] is False, ret
    assert ret['parent_parked'] is True and 'set aside with it' in ret['note'], ret
    _plant_journal('return', ret_uid)
    _, body = _get(client)
    ret = _one(body, 'return')
    assert ret['stock_and_books_applied'] is True and ret['retries_itself'] is False, ret
    assert 'accounting' in ret['note'] and 'WERE added' in ret['note'], ret


def test_parked_refunds_are_their_own_figure_and_never_netted_against_sales(monkeypatch):
    client, cid = _make_admin()
    _local_company_is(monkeypatch, cid)
    _park('sale', 'duplicate_document_number', {'uid': 's1', 'sale_number': 'S1', 'total': 100})
    _park('return', 'duplicate_document_number', {'uid': 'r1', 'sale_uid': 'x', 'refund_amount': 40})
    _park('return', 'missing_parent:sale', {'uid': 'r2', 'sale_uid': 's1', 'refund_amount': '0.125'})
    _park('return', 'duplicate_document_number', {'uid': 'r3', 'sale_uid': 'x', 'refund_amount': 'junk'})
    _, body = _get(client)
    s = body['data']['summary']
    # 100 of sales and 40.125 of refunds is NOT "59.875 short": two figures.
    assert s['sales_count'] == 1 and s['sales_total'] == 100.0, s
    assert s['returns_count'] == 3 and s['returns_total'] == 40.125, s


# ── REAL rows from two real computers ───────────────────────────────────────

def test_real_two_computer_quarantine_rows_read_back_through_the_route(tmp_path, monkeypatch):
    """Two REAL computers of one shop (separate retail.db each, one shared device
    registry, joined by the repo's FileRelay -- `_v42_shop.Shop`) that BOTH believe
    they are the main computer, so both give away till 1 (the pinned residual T5b of
    retail_p1b_numbering_twodevice_test.py). REWRITTEN for schema v42: the old
    version got its clash from two offline computers each picking till 1 on their
    own; the main-computer rule now refuses that (409 TILL_NEEDS_MAIN_COMPUTER), so
    the only way left for the engine to see the clash is two computers that each
    think they are main. The rows the ENGINE parked -- not hand-written ones -- are
    copied into this process's DB and read through the route."""
    from _v42_shop import Shop
    shop = Shop(tmp_path).build()
    a, b = shop.ids['a'], shop.ids['b']
    a_branch_uid = shop.sel('a', "SELECT uid FROM branches ORDER BY id LIMIT 1")[0]['uid']
    shop.patch_branch_uid('b', a_branch_uid)             # both mean the same branch
    # B becomes a second main computer with a NEWER epoch and A never hears of it
    # (one admin per registry, so B's copy demotes A before it promotes B).
    shop.run('b', [
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=0 WHERE id=?", "params": [a]},
        {"op": "reg_exec", "sql": "UPDATE devices SET is_admin_device=1 WHERE id=?", "params": [b]},
        {"op": "reg_exec", "sql": "UPDATE admin_device_assignment SET epoch=epoch+1", "params": []}])
    for w in ('a', 'b'):
        [r] = shop.login(w, [{"op": "numbering_set", "branch_code": "AAA",
                              "readable_enabled": True, "till_number": 1}])
        assert r["status_code"] == 200, (w, r)          # neither computer can refuse the other
        [bid] = shop.sel(w, "SELECT id FROM branches ORDER BY id LIMIT 1")
        out = shop.login(w, [{"op": "cash_session_open", "branch_id": bid["id"]},
                             {"op": "sell", "product_id": shop.shop["product_id"], "quantity": 1}])
        assert out[0]["status_code"] == 200 and out[1]["status_code"] == 200, (w, out)
    shop.sync_all()
    shop.sync_all()

    cols = "entity_id, entity_type, event_type, payload, reason, detail"
    real = []
    per_device = {}
    for w in ('a', 'b'):
        rows = shop.sel(w, f"SELECT {cols} FROM sync_apply_quarantine")
        per_device[w] = rows
        real += rows
    assert real, "the two-computer scenario parked nothing -- the window closed; update this test on purpose"
    types = {r["entity_type"] for r in real}
    assert 'doc_series' in types, types
    # What the engine did with the parked sale's stock and books, on the DEVICES'
    # own databases (this is the fact the route's derived flag reads): nothing of a
    # parked sale is in gl_journals or inventory_movements; both waited with it.
    for w in ('a', 'b'):
        parked = [json.loads(r["payload"]) for r in per_device[w] if r["entity_type"] == "sale"]
        assert parked, f"{w}: no sale was parked -- the scenario changed; update this test on purpose"
        for p in parked:
            assert shop.sel(w, "SELECT 1 FROM gl_journals WHERE source_type='sale' AND source_uid=?",
                            [p["uid"]]) == [], f"{w}: a parked sale's journal was applied"
            assert shop.sel(w, "SELECT 1 FROM inventory_movements WHERE movement_type='sale_out' "
                               "AND reference=? AND terminal_id=?",
                            [p["sale_number"], p["terminal_id"]]) == [], \
                f"{w}: a parked sale's stock movement was applied"
        kinds = {(r["entity_type"], r["reason"]) for r in per_device[w]}
        assert ("inventory_movement", "missing_parent:sale") in kinds, (w, kinds)
        assert ("gl_journal", "missing_parent:sale") in kinds, (w, kinds)

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
        # Round 3 point 3, on rows the ENGINE really wrote: a parked sale's
        # items/payments are permanent, not "retrying". The flag is DERIVED: the
        # asserts above showed the devices hold no journal or movement of any
        # parked sale, and this process's tables hold none either, so every parked
        # sale reads "set aside with it" (False). The allow half (True) is pinned
        # above on planted rows; a device snapshot of applied rows is not
        # reproducible with the current engine, which parks them.
        for i in data['items']:
            if i['kind_code'] == 'sale':
                assert i['stock_and_books_applied'] is False and 'set aside with it' in i['note'], i
        parked_uids = {json.loads(r["payload"]).get("uid") for r in real_sales}
        kids = [r for r in real if r["entity_type"] in ("sale_item", "payment")
                and json.loads(r["payload"]).get("sale_uid") in parked_uids]
        # Coordinator item 4: this used to be `if kids:`, so if the engine parked
        # no children the assertions below never ran and the test passed without
        # checking the very claim it exists to make. A parked sale here always
        # leaves its items/payments behind (a sale is never parked without its
        # children arriving), so an empty `kids` means the scenario changed --
        # fail loudly and let a person update this test on purpose.
        assert kids, ("the engine parked a sale but none of its sale_item/payment "
                      "children -- the scenario changed; update this test on purpose")
        got = [i for i in data['items'] if i['kind_code'] in ('sale_item', 'payment')
               and i['parent_parked']]
        assert len(got) == len(kids), (len(got), len(kids))
        assert all(i['retries_itself'] is False and i['note'] for i in got), got
