"""
Aura Retail -- W2a (schema v48): loyalty points shared between computers.
ROUTE SIDE: what create_sale / create_return / the importer / the boot
backfill put on the wire, and the 409 that stops a return on a computer that
does not yet hold the sale's points.

WHY (W2a). `loyalty_ledger` rows never left the computer that wrote them, so a
return on B of A's sale found no earn/redeem rows and gave the wrong refund.
Ledger rows are now immutable synced FACTS (entity `loyalty_entry`, event
'create'), `customers.loyalty_points` is a CACHE of SUM(points_delta), and a
return refuses (409 LOYALTY_NOT_ARRIVED) while the sale's own points have not
arrived.

The apply side (sync_service.py) has its own file,
retail_loyalty_sync_apply_test.py.

ONE pytest process per file. Run with AURA_SYNC_RELAY_URL /
AURA_OWNER_LICENSING_URL unset.
"""
import json
import os
import shutil
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_loyalty_sync_routes_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api  # noqa: E402
from core.retail import loyalty as L  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'

#: The exact wire key set of one loyalty_entry event (plan D1). A key added or
#: dropped on one side only is the drift the sync-payload coverage ratchet and
#: the apply side both read from.
ENTRY_KEYS = {'uid', 'customer_id', 'points_delta', 'entry_type', 'sale_uid',
              'return_uid', 'created_by', 'created_at'}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_background_push(monkeypatch):
    """These tests READ the outbox; the real route's `_sync_nudge()` starts a
    background push whose ack deletes outbox rows."""
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda: None)


def _login_admin(company_id):
    email = f"w2a-{uuid.uuid4().hex[:10]}@test.local"
    password = "W2aRoutesPW1"  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), 'admin', 'active'))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, 'admin')
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def shop():
    """(client, cid): a fresh company with an admin and a 100-priced product
    set up by the helpers below. Loyalty point value 1 so points can be spent."""
    cid = str(uuid.uuid4())
    client = _login_admin(cid)
    client.get(f'{API}/settings/tax')   # warms the lazy schema
    r = client.post(f'{API}/settings/credit', json={'loyalty_point_value': 1})
    assert r.status_code == 200, r.get_json()
    return client, cid


def _q(sql, *args):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _x(sql, *args):
    conn = get_retail_conn()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _product(client, price=100.0):
    tag = uuid.uuid4().hex[:8]
    r = client.post(f'{API}/products', json={
        'name': f'W2a item {tag}', 'sku': f'W2A-{tag}', 'sell_price': price,
        'cost_price': 1.0, 'tax_rate': 0, 'initial_stock': 1000})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _customer(client):
    r = client.post(f'{API}/customers', json={'name': f'W2a Customer {uuid.uuid4().hex[:6]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sale(client, pid, customer_id, qty=1, **extra):
    payload = dict({'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'cash',
                    'customer_id': customer_id, 'idempotency_key': str(uuid.uuid4())}, **extra)
    return client.post(f'{API}/sales', json=payload)


def _return(client, sale_id, pid, qty=1):
    return client.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': qty}],
        'reason': 'w2a', 'idempotency_key': str(uuid.uuid4())})


def _events(entity_type='loyalty_entry'):
    rows = _q("SELECT * FROM sync_outbox WHERE entity_type=? ORDER BY rowid", entity_type)
    for r in rows:
        r['payload'] = json.loads(r['payload'])
    return rows


def _entry_events_for(cid, customer_id):
    return [e for e in _events() if e['payload']['customer_id'] == customer_id]


def _grant(cid, customer_id, points, entry_type='adjust'):
    """A starting balance written the way a goodwill grant is (no queue)."""
    _x("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,created_by) "
       "VALUES (?,?,?,?,?,'fixture')", str(uuid.uuid4()), cid, customer_id, points, entry_type)
    conn = get_retail_conn()
    L.refresh_points(conn, cid, customer_id)
    conn.commit(); conn.close()


def _sum(cid, customer_id):
    return _q("SELECT COALESCE(SUM(points_delta),0) AS s FROM loyalty_ledger WHERE company_id=? "
              "AND customer_id=?", cid, customer_id)[0]['s']


def _cache(customer_id):
    return _q("SELECT loyalty_points FROM customers WHERE id=?", customer_id)[0]['loyalty_points']


# ── sale: earn and redeem ride the wire ──────────────────────────────────────

def test_sale_with_a_customer_queues_the_earn_entry_with_the_exact_key_set(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    before = len(_events())

    r = _sale(client, pid, cust)
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']

    evs = _entry_events_for(cid, cust)
    assert len(_events()) == before + 1 and len(evs) == 1, evs
    ev = evs[0]
    p = ev['payload']
    assert ev['event_type'] == 'create'
    assert set(p) == ENTRY_KEYS, set(p) ^ ENTRY_KEYS
    sale_uid = _q("SELECT uid FROM sales WHERE id=?", sale_id)[0]['uid']
    row = _q("SELECT * FROM loyalty_ledger WHERE company_id=? AND customer_id=?", cid, cust)[0]
    assert (p['entry_type'], p['points_delta'], p['sale_uid'], p['return_uid']) == ('earn', 10, sale_uid, None)
    assert ev['entity_id'] == p['uid'] == row['uid'], "entity_id is the row's own uid"
    assert p['created_at'] == row['created_at'] and p['created_by'] == row['created_by']


def test_redeeming_sale_queues_both_the_earn_and_the_redeem_entry(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    _grant(cid, cust, 50)

    r = _sale(client, pid, cust, points_redeemed=5, amount_paid=95.0)
    assert r.status_code == 200, r.get_json()

    by_type = {e['payload']['entry_type']: e['payload'] for e in _entry_events_for(cid, cust)}
    assert set(by_type) == {'earn', 'redeem'}, by_type
    assert by_type['redeem']['points_delta'] == -5
    assert by_type['redeem']['sale_uid'] == by_type['earn']['sale_uid'] is not None
    assert all(set(p) == ENTRY_KEYS for p in by_type.values())
    # Immutable facts are queued, not re-armed for the boot backfill.
    flags = _q("SELECT sync_backfill_pending AS f FROM loyalty_ledger WHERE company_id=? AND customer_id=? "
               "AND entry_type IN ('earn','redeem')", cid, cust)
    assert [f['f'] for f in flags] == [0, 0]


def test_a_walk_in_sale_queues_no_loyalty_entry(shop):
    client, cid = shop
    pid = _product(client)
    before = len(_events())
    r = client.post(f'{API}/sales', json={'items': [{'product_id': pid, 'quantity': 1}],
                                          'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    assert len(_events()) == before


def test_points_earned_is_stored_and_travels_on_the_sale(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    r = _sale(client, pid, cust, qty=3)          # total 300 -> 30 points
    assert r.status_code == 200, r.get_json()
    sale_id = r.get_json()['data']['id']
    assert _q("SELECT points_earned FROM sales WHERE id=?", sale_id)[0]['points_earned'] == 30
    sale_ev = [e for e in _events('sale') if e['payload'].get('customer_id') == cust][-1]
    assert sale_ev['payload']['points_earned'] == 30


# ── cache is the SUM after every route ───────────────────────────────────────

def test_cache_equals_the_ledger_sum_after_sale_and_return(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    _grant(cid, cust, 40)
    _x("UPDATE customers SET loyalty_points=999 WHERE id=?", cust)   # drifted cache

    r = _sale(client, pid, cust, points_redeemed=7, amount_paid=93.0)
    assert r.status_code == 200, r.get_json()
    assert _cache(cust) == _sum(cid, cust) == 43     # 40 + 10 earned - 7 redeemed

    rr = _return(client, r.get_json()['data']['id'], pid)
    assert rr.status_code == 200, rr.get_json()
    assert _cache(cust) == _sum(cid, cust)
    assert _sum(cid, cust) == 40                     # earn clawed back, redeem given back


def test_sale_and_return_do_not_bump_the_customer_row_version(shop):
    """The cache refresh must never look like an edit (plan D3)."""
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    v0 = _q("SELECT row_version FROM customers WHERE id=?", cust)[0]['row_version']
    cust_events = len(_events('customer'))
    r = _sale(client, pid, cust)
    _return(client, r.get_json()['data']['id'], pid)
    assert _q("SELECT row_version FROM customers WHERE id=?", cust)[0]['row_version'] == v0
    assert len(_events('customer')) == cust_events


# ── return: the adjust rows ride the wire with return_uid ────────────────────

def test_return_queues_the_adjust_rows_with_the_return_uid(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    _grant(cid, cust, 50)
    r = _sale(client, pid, cust, points_redeemed=5, amount_paid=95.0)
    sale_id = r.get_json()['data']['id']
    n_before = len(_entry_events_for(cid, cust))

    rr = _return(client, sale_id, pid)
    assert rr.status_code == 200, rr.get_json()
    ret_uid = _q("SELECT uid FROM returns WHERE id=?", rr.get_json()['data']['id'])[0]['uid']

    new = _entry_events_for(cid, cust)[n_before:]
    assert len(new) == 2, new
    assert all(set(e['payload']) == ENTRY_KEYS for e in new)
    assert {e['payload']['entry_type'] for e in new} == {'adjust'}
    assert {e['payload']['return_uid'] for e in new} == {ret_uid}
    assert {e['payload']['sale_uid'] for e in new} == {None}
    assert sorted(e['payload']['points_delta'] for e in new) == [-10, 5]


# ── return: 409 LOYALTY_NOT_ARRIVED ──────────────────────────────────────────

def _snapshot(cid):
    return {
        'returns': _q("SELECT COUNT(*) AS n FROM returns WHERE company_id=?", cid)[0]['n'],
        'ledger': _q("SELECT COUNT(*) AS n FROM loyalty_ledger WHERE company_id=?", cid)[0]['n'],
        'outbox': _q("SELECT COUNT(*) AS n FROM sync_outbox")[0]['n'],
        'payments': _q("SELECT COUNT(*) AS n FROM payments WHERE company_id=?", cid)[0]['n'],
        'stock': _q("SELECT COALESCE(SUM(quantity_on_hand),0) AS n FROM inventory_balances "
                    "WHERE company_id=?", cid)[0]['n'],
    }


def test_return_is_refused_until_the_sales_earn_row_has_arrived(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    sale_id = _sale(client, pid, cust).get_json()['data']['id']
    # This computer holds the sale but not its points (they sync separately).
    _x("DELETE FROM loyalty_ledger WHERE company_id=? AND customer_id=? AND entry_type='earn'", cid, cust)
    snap = _snapshot(cid)

    rr = _return(client, sale_id, pid)
    assert rr.status_code == 409, rr.get_json()
    assert rr.get_json()['code'] == 'LOYALTY_NOT_ARRIVED'
    assert _snapshot(cid) == snap, "a refused return must write nothing at all"


def test_return_is_refused_until_the_sales_redeem_row_has_arrived(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    _grant(cid, cust, 50)
    sale_id = _sale(client, pid, cust, points_redeemed=5, amount_paid=95.0).get_json()['data']['id']
    _x("DELETE FROM loyalty_ledger WHERE company_id=? AND customer_id=? AND entry_type='redeem'", cid, cust)
    snap = _snapshot(cid)

    rr = _return(client, sale_id, pid)
    assert rr.status_code == 409 and rr.get_json()['code'] == 'LOYALTY_NOT_ARRIVED', rr.get_json()
    assert _snapshot(cid) == snap


def test_return_goes_through_once_the_points_are_here(shop):
    """The allow half: the same return succeeds when the rows ARE present."""
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    sale_id = _sale(client, pid, cust).get_json()['data']['id']
    rr = _return(client, sale_id, pid)
    assert rr.status_code == 200, rr.get_json()
    assert _sum(cid, cust) == 0


def test_legacy_sale_with_unknown_points_earned_is_not_refused(shop):
    """A sale from before v48 (points_earned NULL) has nothing to wait for."""
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    sale_id = _sale(client, pid, cust).get_json()['data']['id']
    _x("UPDATE sales SET points_earned=NULL WHERE id=?", sale_id)
    _x("DELETE FROM loyalty_ledger WHERE company_id=? AND customer_id=?", cid, cust)
    rr = _return(client, sale_id, pid)
    assert rr.status_code == 200, rr.get_json()


def test_walk_in_sale_return_is_never_refused_for_points(shop):
    client, cid = shop
    pid = _product(client)
    sale_id = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4())}).get_json()['data']['id']
    assert _return(client, sale_id, pid).status_code == 200


# ── boot backfill ────────────────────────────────────────────────────────────

def _legacy_row(cid, customer_id, delta, entry_type, sale_id=None, uid=None):
    _x("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type,sale_id,"
       "created_by,sync_backfill_pending) VALUES (?,?,?,?,?,?,'legacy',1)",
       uid or str(uuid.uuid4()), cid, customer_id, delta, entry_type, sale_id)


def test_backfill_queues_each_flagged_row_once_and_clears_the_flag(shop):
    client, cid = shop
    pid, cust = _product(client), _customer(client)
    sale_id = _sale(client, pid, cust).get_json()['data']['id']
    sale_uid = _q("SELECT uid FROM sales WHERE id=?", sale_id)[0]['uid']
    _x("DELETE FROM sync_outbox")
    _legacy_row(cid, cust, 100, 'opening')                       # random uid: re-keyed to the deterministic one
    _legacy_row(cid, cust, 4, 'earn', sale_id=sale_id)
    _legacy_row(cid, cust, 3, 'earn', sale_id=987654)            # its sale is gone locally

    conn = get_retail_conn()
    assert retail_api.backfill_legacy_loyalty(conn) == 3
    conn.close()

    evs = _events()
    assert len(evs) == 3
    assert all(set(e['payload']) == ENTRY_KEYS for e in evs)
    by_delta = {e['payload']['points_delta']: e['payload'] for e in evs}
    assert by_delta[100]['uid'] == L.opening_uid(cust), "opening goes out under the deterministic uid"
    assert by_delta[4]['sale_uid'] == sale_uid
    assert by_delta[3]['sale_uid'] is None, "a vanished sale goes out with no reference"
    assert _q("SELECT COUNT(*) AS n FROM loyalty_ledger WHERE sync_backfill_pending=1")[0]['n'] == 0

    conn = get_retail_conn()
    assert retail_api.backfill_legacy_loyalty(conn) == 0
    conn.close()
    assert len(_events()) == 3, "a second boot must emit nothing"


# ── importer ─────────────────────────────────────────────────────────────────

def _import_points(client, email, points):
    import csv, io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=['name', 'email', 'loyalty_points'])
    w.writeheader(); w.writerow({'name': 'W2a Imported', 'email': email, 'loyalty_points': points})
    r = client.post("/api/import/execute", data={
        'system': 'retail', 'entity': 'customers',
        'mapping': json.dumps({'name': 'name', 'email': 'email', 'loyalty_points': 'loyalty_points'}),
        'file': (io.BytesIO(buf.getvalue().encode('utf-8')), 'customers.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200 and r.get_json()['success'] is True, r.get_json()


def test_import_queues_its_ledger_rows_and_refreshes_the_cache(shop, monkeypatch):
    import api.import_api as import_api
    monkeypatch.setattr(import_api, '_sync_nudge', lambda: None)
    client, cid = shop
    email = f'w2a-{uuid.uuid4().hex[:6]}@test.local'

    _import_points(client, email, '150')
    cust = _q("SELECT id FROM customers WHERE company_id=? AND email=?", cid, email)[0]['id']
    evs = _entry_events_for(cid, cust)
    assert len(evs) == 1 and set(evs[0]['payload']) == ENTRY_KEYS
    assert evs[0]['payload']['entry_type'] == 'opening'
    assert evs[0]['payload']['uid'] == L.opening_uid(cust)
    assert evs[0]['payload']['points_delta'] == 150
    assert _cache(cust) == _sum(cid, cust) == 150

    _import_points(client, email, '100')       # a re-import SETS the balance
    evs = _entry_events_for(cid, cust)
    assert len(evs) == 2
    assert evs[1]['payload']['entry_type'] == 'adjust' and evs[1]['payload']['points_delta'] == -50
    assert evs[1]['payload']['uid'] != L.opening_uid(cust)
    assert _cache(cust) == _sum(cid, cust) == 100


# ── plain sentences for the review screen ───────────────────────────────────

NEW_REASONS = ('missing_parent:customer', 'loyalty_opening_differs', 'loyalty_overspent')


def test_new_loyalty_reasons_have_their_own_plain_sentence_in_both_languages():
    root = PRODUCT_DIR / 'frontend' / 'locales'
    en = json.loads((root / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((root / 'ar.json').read_text(encoding='utf-8'))
    generic = retail_api._SYNC_REVIEW_REASON_UNKNOWN[0]
    other = retail_api._SYNC_REVIEW_REASON_MISSING_OTHER[0]
    for reason in NEW_REASONS:
        assert reason in retail_api._SYNC_REVIEW_REASONS, reason
        text, _ = retail_api._sync_review_reason(reason, 'loyalty_entry')
        assert text not in (generic, other), reason
        assert en.get(text) == text, reason
        assert ar.get(text) and ar[text] != text, reason
    kind = retail_api._SYNC_REVIEW_KINDS['loyalty_entry']
    assert en.get(kind) == kind and ar.get(kind) and ar[kind] != kind
