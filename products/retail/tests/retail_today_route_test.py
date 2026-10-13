"""Aura Today (TA step 3) -- GET /api/sub/retail/reports/today.

The owner's home is ONE route. This file drives the real app and the real
route; only the TIMESTAMPS and the rows a UI could never create (a drawer left
uncounted, a cheque, a stale reorder level) are written with raw SQL -- the way
retail_aging_fifo_test.py ages its rows.

THE FIXTURE (hand-computed; every figure asserted below is derived here, not
read back from the code under test)

  Shop: Asia/Amman (+03:00 all year), JOD (3 dp), branches A "Abdali" and
  B "Irbid". "Now" is frozen at 2026-10-05 16:12 shop time (13:12 UTC).

  Today (2026-10-05)            total     unit cost (half the price)
    A cash   09:00              10.000      5.000
    A cash   11:00              20.000     10.000
    A cash   15:00              30.500     15.250
    B card   10:00              40.000     20.000
    A cash RETURN 12:00          5.000 refunded; half a unit of the 09:00
                                 line comes back -> 0.5 x 5.000 = 2.500 cost
  revenue = 100.5 - 5.0 = 95.500   transactions 4   avg ticket 95.5/4 = 23.875
  COGS    = 5 + 10 + 15.25 + 20 - 2.5 = 47.750  -> gross profit 47.750, margin 50.0
  tenders: cash 60.5 - 5.0 = 55.500 (58.1 %), card 40.000 (41.9 %)

  Yesterday: A 50.000 @10:00 and A 60.000 @17:00. By 16:12 only the 10:00 one
  counts -> yesterday_by_now 50.000, change (95.5-50)/50 = +91.0 %.
  Trend: 14 days ending today; the 13 complete days are all zero except
  yesterday's 110.000 -> average 110/13 = 8.462.
  Branches: A 55.500 (3 sales, 58.1 %, yesterday-by-now 50.000, +11.0 %);
            B 40.000 (1 sale, 41.9 %, yesterday-by-now 0 -> change None).

  Collect (more than 30 shop days): Hala charge 100 @40 d, paid 30 @10 d ->
  FIFO leaves 70.000 aged 40 d; Rania 15.000 @45 d; Omar 20.000 @ EXACTLY 30 d
  (not over 30 -> excluded). count 2, stake 85.000 -- and == aging buckets
  31_60 + 61_90 + 90_plus.
  Cheques (shop is owed): in/pending due +2 120.000 (counts); in/deposited
  +1 (folded away -> excluded); in/pending +9 200.000 (next week); out/pending
  +3 75.000 (out due). stake 120.000.
  Restock: P@B level 10 on hand 3 cost 2.000 -> 20.000; Q@A level 4 on hand 4
  cost 1.500 -> 6.000; R level 0 excluded; S on hand 6 level 5 excluded.
  stake 26.000, count 2.
  Drawers: one ended drawer counted 97.650 against 100.000 expected (-2.350,
  unverified) + one ended uncounted -> count 2, stake 2.350.

Run:
    pytest products/retail/tests/retail_today_route_test.py -v
"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_today_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
# AURA_SITE_RELAY_ENABLED="0": an active WINDOWS licence elects a till the LAN
# hub, which would start background threads inside a test about one screen.
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

import api.retail_api as retail_api_module  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
ZONE_SHIFT = timedelta(hours=3)                       # Asia/Amman, no DST since 2022
TODAY = date(2026, 10, 5)
NOW_UTC = datetime(2026, 10, 5, 13, 12, 0, tzinfo=timezone.utc)      # 16:12 in Amman
MONEY_KEYS = {'revenue', 'gross_sales', 'refunds', 'avg_ticket', 'yesterday_by_now', 'gross_profit',
              'amount', 'stake', 'average', 'variance', 'open_expected_cash', 'next_week_amount',
              'out_due_amount', 'owed'}


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# -- the clock ----------------------------------------------------------------

def _frozen_class(now_utc):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.astimezone().replace(tzinfo=None)
    return _FrozenDateTime


@contextmanager
def _frozen(now_utc=NOW_UTC):
    original = retail_api_module.datetime
    retail_api_module.datetime = _frozen_class(now_utc)
    try:
        yield
    finally:
        retail_api_module.datetime = original


def _local(day, hhmm):
    """A shop-local instant on `day` at 'HH:MM' -> (created_at wall, created_at_utc)."""
    h, m = hhmm.split(':')
    wall = datetime(day.year, day.month, day.day, int(h), int(m), 0)
    return wall.strftime('%Y-%m-%d %H:%M:%S'), (wall - ZONE_SHIFT).strftime('%Y-%m-%d %H:%M:%S')


# -- harness ------------------------------------------------------------------

def _register(role):
    email = f"today-{uuid.uuid4().hex[:10]}@test.local"
    password = "TodayRoutePW1"
    cid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    reg = registry_conn()
    reg.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)", (uid, cid, "EMP-0001", email, hash_password(password), role, "active"))
    reg.commit(); reg.close()
    return cid, uid, email, password


def _login(email, password):
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    return client


def _invite(admin, role):
    email = f"today-{role}-{uuid.uuid4().hex[:8]}@test.local"
    r = admin.post('/api/admin/employees', json={'email': email, 'role': role, 'permissions': {'retail': 'full'}})
    assert r.status_code == 200, r.get_json()
    reg = registry_conn()
    uid = reg.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    password = "TodayRoleUserPW1"
    reg.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?", (hash_password(password), uid))
    reg.commit(); reg.close()
    return uid, email, password


def _shop(branches=('Abdali', 'Irbid')):
    """A new company: owner logged in, Asia/Amman, JOD, the named branches
    created through the real route (so they carry a uid)."""
    cid, uid, email, password = _register('admin')
    rconn = get_retail_conn()
    rconn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,?,?)",
                  (cid, 'business_timezone', 'Asia/Amman'))
    for doc_type in ('sale', 'return'):
        rconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (cid, doc_type, random.randint(1, 5_000_000)))
    rconn.commit(); rconn.close()
    client = _login(email, password)
    r = client.post(f"{API}/settings/credit", json={"base_currency": "JOD"})
    assert r.status_code == 200, r.get_json()
    ids = {}
    for name in branches:
        r = client.post(f"{API}/branches", json={'name': name})
        assert r.status_code == 200, r.get_json()
        ids[name] = r.get_json()['data']['id']
        # REHEARSAL-1007 B8: the route now also creates a standard "Main Branch" first on a
        # company with no branch; this file counts the branches IT creates ("one branch
        # means no branch card"), so the empty placeholder is dropped. The placeholder is
        # owned by retail_rehearsal_b8_b9_branch_and_float_test.py.
        _c = get_retail_conn()
        _c.execute("DELETE FROM branches WHERE name='Main Branch' AND id<? AND company_id="
                   "(SELECT company_id FROM branches WHERE id=?)", (ids[name], ids[name]))
        _c.commit(); _c.close()
    return {'cid': cid, 'uid': uid, 'client': client, 'branch': ids}


def _product(cid, cost, price, level=0, name=None):
    pid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate,reorder_level) "
                 "VALUES (?,?,?,?,?,?,0,?)",
                 (pid, cid, f'T-{uuid.uuid4().hex[:8]}', name or f'Item {pid[:4]}', cost, price, level))
    conn.commit(); conn.close()
    return pid


def _stock(cid, pid, branch, qty):
    conn = get_retail_conn()
    conn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
                 (cid, pid, branch, qty))
    conn.commit(); conn.close()


def _sale(cid, branch, day, hhmm, total, method='cash', customer=None, paid=None, cost=None):
    """A completed sale with ONE line carrying a unit_cost snapshot (half the
    price unless told otherwise). Returns (sale_id, product_id)."""
    ca, cu = _local(day, hhmm)
    pid = _product(cid, (total / 2) if cost is None else cost, total)
    conn = get_retail_conn()
    cur = conn.execute(
        "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, subtotal, total, amount_paid, "
        "payment_method, status, created_at, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, f'TS-{uuid.uuid4().hex[:10]}', branch, customer, total, total,
         total if paid is None else paid, method, 'completed', ca, cu))
    sid = cur.lastrowid
    conn.execute("INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, line_total, unit_cost) "
                 "VALUES (?,?,?,?,?,?)", (sid, pid, 1, total, total, (total / 2) if cost is None else cost))
    conn.commit(); conn.close()
    return sid, pid


def _return(cid, branch, day, hhmm, sale_id, product_id, qty, refund, method='cash'):
    ca, cu = _local(day, hhmm)
    conn = get_retail_conn()
    cur = conn.execute(
        "INSERT INTO returns (company_id, return_number, sale_id, branch_id, refund_amount, refund_method, status, "
        "created_at, created_at_utc, tender_refund_amount) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, f'TR-{uuid.uuid4().hex[:10]}', sale_id, branch, refund, method, 'completed', ca, cu, refund))
    conn.execute("INSERT INTO return_items (return_id, product_id, quantity, unit_price, line_total) VALUES (?,?,?,?,?)",
                 (cur.lastrowid, product_id, qty, refund / qty, refund))
    conn.commit(); conn.close()


def _customer(cid, name):
    cust = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute("INSERT INTO customers (id,company_id,name,phone,credit_mode,credit_limit,credit_balance) "
                 "VALUES (?,?,?,?,'unlimited',0,0)", (cust, cid, name, '0790000000'))
    conn.commit(); conn.close()
    return cust


def _account_payment(cid, customer, amount, day, hhmm='10:00'):
    ca, _cu = _local(day, hhmm)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, currency, fx_rate, "
        "method, status, related_type, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, f'TP-{uuid.uuid4().hex[:10]}', 'customer', customer, 'in', amount, 'JOD', 1, 'cash', 'active',
         None, ca))
    conn.commit(); conn.close()


def _cheque(cid, branch, customer, direction, due_days, amount, deposited=False, number=None):
    cheque_id = str(uuid.uuid4())
    due = (TODAY + timedelta(days=due_days)).isoformat()
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO cheques (id, company_id, branch_id, direction, party_type, party_id, cheque_number, bank_name, "
        "amount, currency, due_date, created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cheque_id, cid, branch, direction, 'customer', customer, number or f'CH{uuid.uuid4().hex[:5]}',
         'Arab Bank', amount, 'JOD', due, '2026-10-01T08:00:00+00:00'))
    conn.execute(
        "INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_at_utc) "
        "VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), cheque_id, 'created', None, 'pending', '2026-10-01T08:00:00+00:00'))
    if deposited:
        conn.execute(
            "INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_at_utc) "
            "VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), cheque_id, 'deposited', 'pending', 'deposited', '2026-10-02T08:00:00+00:00'))
    conn.commit(); conn.close()
    return cheque_id


def _drawer(cid, branch, counted, expected, ended_today=True):
    sid = str(uuid.uuid4())
    ended = _local(TODAY, '15:30')[0]
    variance = None if counted is None else round(counted - expected, 3)
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO cash_sessions (id, company_id, branch_id, opened_by, opened_at, opening_float, "
        "closing_float_counted, closing_float_expected, variance, status, ended_at, ended_by, terminal_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, cid, branch, 'owner', _local(TODAY, '08:00')[0], 50.0, counted, expected, variance, 'ended',
         ended, 'owner', 'term-' + uuid.uuid4().hex[:4]))
    conn.commit(); conn.close()
    return sid


def _set_einv(cid, **kv):
    from commercial_runtime.einvoicing import settings as einv_settings
    conn = get_retail_conn()
    try:
        for k, v in kv.items():
            einv_settings.set_setting(conn, cid, k, v)
        conn.commit()
    finally:
        conn.close()


def _wire_provider(monkeypatch, provider):
    """Put a REAL `/api/einvoicing/status` view, built by the product's own
    blueprint factory around `provider`, where `_jofotara_state` looks (same
    technique as retail_p1b_backend_setup_checklist_test.py)."""
    from flask import Flask
    from commercial_runtime.einvoicing.routes import make_einvoicing_blueprint
    scratch = Flask('scratch-einvoicing-today')
    scratch.register_blueprint(make_einvoicing_blueprint(
        product_code='AURA_RETAIL', platform=_app_module.LICENSING_PLATFORM,
        app_data_dir=_app_module._EINVOICING_APP_DATA_DIR,
        conn_factory=get_retail_conn, provider=provider))
    monkeypatch.setitem(app.view_functions, 'einvoicing_api._status',
                        scratch.view_functions['einvoicing_api._status'])


def _outbox(cid, status, n=1, cleared_today=False, age=timedelta(0)):
    conn = get_retail_conn()
    for _ in range(n):
        ref = uuid.uuid4().hex
        stamp = (NOW_UTC - age).isoformat()
        conn.execute(
            "INSERT INTO einvoice_outbox (company_id, invoice_ref, source_type, source_id, einvoice_no, "
            "invoice_family, payment_type, status, created_at, updated_at, cleared_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, ref, 'sale', 1, ref[:12], 'income', 'cash', status, stamp, stamp,
             stamp if cleared_today else None))
    conn.commit(); conn.close()


def _today(client, query=''):
    with _frozen():
        return client.get(f"{API}/reports/today{query}")


def _data(client, query=''):
    r = _today(client, query)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()['data']


def _card(data, kind):
    hits = [c for c in data['cards'] if c['kind'] == kind]
    assert len(hits) == 1, (kind, [c['kind'] for c in data['cards']])
    return hits[0]


def _money_values(node, path=''):
    """Every (path, value) under a MONEY key, recursively."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in MONEY_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                yield f'{path}.{k}', v
            else:
                yield from _money_values(v, f'{path}.{k}')
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _money_values(v, f'{path}[{i}]')


# -- the full fixture ------------------------------------------------------------

@pytest.fixture(scope='module')
def full():
    shop = _shop()
    cid, A, B = shop['cid'], shop['branch']['Abdali'], shop['branch']['Irbid']
    s09, p09 = _sale(cid, A, TODAY, '09:00', 10.0)
    _sale(cid, A, TODAY, '11:00', 20.0)
    _sale(cid, A, TODAY, '15:00', 30.5)
    _sale(cid, B, TODAY, '10:00', 40.0, method='card')
    _return(cid, A, TODAY, '12:00', s09, p09, 0.5, 5.0)
    yday = TODAY - timedelta(days=1)
    _sale(cid, A, yday, '10:00', 50.0)
    _sale(cid, A, yday, '17:00', 60.0)

    hala, rania, omar = _customer(cid, 'Hala'), _customer(cid, 'Rania'), _customer(cid, 'Omar')
    _sale(cid, A, TODAY - timedelta(days=40), '10:00', 100.0, method='credit', customer=hala, paid=0)
    _account_payment(cid, hala, 30.0, TODAY - timedelta(days=10))
    _sale(cid, A, TODAY - timedelta(days=45), '10:00', 15.0, method='credit', customer=rania, paid=0)
    _sale(cid, A, TODAY - timedelta(days=30), '10:00', 20.0, method='credit', customer=omar, paid=0)

    _cheque(cid, A, hala, 'in', 2, 120.0, number='CH-DUE')
    _cheque(cid, A, hala, 'in', 1, 999.0, deposited=True)
    _cheque(cid, A, hala, 'in', 9, 200.0)
    _cheque(cid, A, hala, 'out', 3, 75.0)

    _stock(cid, _product(cid, 2.0, 5.0, level=10, name='P'), B, 3)
    _stock(cid, _product(cid, 1.5, 4.0, level=4, name='Q'), A, 4)
    _stock(cid, _product(cid, 1.0, 2.0, level=0, name='R'), A, 0)
    _stock(cid, _product(cid, 1.0, 2.0, level=5, name='S'), A, 6)

    _drawer(cid, A, 97.65, 100.0)
    _drawer(cid, A, None, 100.0)
    # Switched on, but the shipped build wires no provider that reaches JoFotara.
    _set_einv(cid, tax_regime='jordan', enabled='1')
    shop.update(A=A, B=B, hala=hala, rania=rania, omar=omar)
    return shop


# ═════════════════════════════════════════════════════════════════════════════
# A. the hand-computed figures
# ═════════════════════════════════════════════════════════════════════════════

def test_hero_is_the_hand_computed_day(full):
    d = _data(full['client'])
    h = d['hero']
    assert d['currency'] == 'JOD' and d['business_date'] == '2026-10-05' and d['as_of'] == '16:12'
    assert (h['revenue'], h['gross_sales'], h['refunds'], h['transactions']) == (95.5, 100.5, 5.0, 4)
    assert h['avg_ticket'] == 23.875
    assert h['yesterday_by_now'] == 50.0 and h['change_pct'] == 91.0
    assert (h['gross_profit'], h['margin_pct'], h['cost_known']) == (47.75, 50.0, True)
    assert h['tenders'] == [{'method': 'cash', 'revenue': 55.5, 'share_pct': 58.1},
                            {'method': 'card', 'revenue': 40.0, 'share_pct': 41.9}]


def test_trend_is_fourteen_zero_filled_days_ending_today(full):
    t = _data(full['client'])['trend']
    days = t['days']
    assert [x['day'] for x in days] == [(TODAY - timedelta(days=13 - i)).isoformat() for i in range(14)]
    assert days[-1] == {'day': '2026-10-05', 'revenue': 95.5, 'transactions': 4}
    assert days[-2] == {'day': '2026-10-04', 'revenue': 110.0, 'transactions': 2}
    assert t['average'] == 8.462 and t['yesterday_by_now'] == 50.0
    # the debts were rung 30+ days ago, outside the window: every other day is a real zero
    assert all(x['revenue'] == 0.0 and x['transactions'] == 0 for x in days[:-2])


def test_branches_strip_and_the_all_clear_row(full):
    d = _data(full['client'])
    a, b = d['branches']
    assert (a['name'], a['revenue'], a['transactions'], a['share_pct'], a['yesterday_by_now'], a['change_pct']) == \
        ('Abdali', 55.5, 3, 58.1, 50.0, 11.0)
    assert (b['name'], b['revenue'], b['transactions'], b['share_pct'], b['yesterday_by_now'], b['change_pct']) == \
        ('Irbid', 40.0, 1, 41.9, 0.0, None)
    assert d['scope'] == {'branch_id': None, 'branch_name': None, 'scoped': False, 'branch_count': 2}
    assert d['all_clear'] == [{'kind': 'branches', 'detail': {'trading': 2}}]


def test_cards_come_in_stake_order_with_the_tie_free_fixture(full):
    d = _data(full['client'])
    assert [c['kind'] for c in d['cards']] == ['cheques', 'collect', 'restock', 'drawers', 'tax']
    assert d['needs_count'] == 5
    assert [c['stake'] for c in d['cards']] == [120.0, 85.0, 26.0, 2.35, None]


def test_collect_card_is_what_the_aging_report_calls_over_thirty_days(full):
    client = full['client']
    d = _data(client)
    c = _card(d, 'collect')
    assert (c['count'], c['stake'], c['amount'], c['scope'], c['more']) == (2, 85.0, 85.0, 'shop', 0)
    assert [(r['name'], r['amount'], r['days_overdue'], r['has_phone']) for r in c['rows']] == \
        [('Hala', 70.0, 40, True), ('Rania', 15.0, 45, True)]
    with _frozen():
        aging = client.get(f"{API}/reports/aging").get_json()['data']
    assert round(aging['31_60'] + aging['61_90'] + aging['90_plus'], 3) == c['stake']
    # Omar's 20.000 is exactly 30 days old: still 1_30, never on this card
    assert 'Omar' not in [r['name'] for r in c['rows']]


def test_collect_survives_a_stale_credit_balance_cache(full):
    """A receive-only computer reads customers.credit_balance as 0. The card
    must come from the ledger, so zeroing the cache changes nothing."""
    from core.retail import party_balance
    conn = get_retail_conn()
    # First make the cache AGREE with the ledger (what the post-pull refresh
    # leaves behind) and prove it is non-zero, so that zeroing it below is a
    # real change and not a no-op on a column that was never populated.
    party_balance.refresh_cache(conn, full['cid'], 'customers', currency='JOD')
    conn.commit()
    stored = conn.execute("SELECT credit_balance FROM customers WHERE id=?", (full['hala'],)).fetchone()[0]
    assert stored == 70.0, stored
    conn.execute("UPDATE customers SET credit_balance=0 WHERE company_id=?", (full['cid'],))
    conn.commit(); conn.close()
    c = _card(_data(full['client']), 'collect')
    assert (c['count'], c['stake']) == (2, 85.0)


def test_cheques_card_folds_the_status_and_splits_the_weeks(full):
    c = _card(_data(full['client']), 'cheques')
    assert (c['count'], c['stake'], c['scope']) == (1, 120.0, 'shop')
    r = c['rows'][0]
    assert (r['party_name'], r['bank_name'], r['cheque_number'], r['due_date'], r['amount']) == \
        ('Hala', 'Arab Bank', 'CH-DUE', '2026-10-07', 120.0)
    assert c['extra'] == {'next_week_count': 1, 'next_week_amount': 200.0,
                          'out_due_count': 1, 'out_due_amount': 75.0}


def test_restock_card_counts_each_branch_and_prices_the_refill(full):
    c = _card(_data(full['client']), 'restock')
    assert (c['count'], c['stake'], c['scope']) == (2, 26.0, 'shop')
    assert [(r['name'], r['branch_name'], r['on_hand'], r['reorder_level']) for r in c['rows']] == \
        [('P', 'Irbid', 3.0, 10.0), ('Q', 'Abdali', 4.0, 4.0)]
    assert c['extra'] == {'supplier_count': 0, 'pending_requests': 0}


def test_drawers_card_is_this_computer_and_does_not_guess_an_uncounted_variance(full):
    c = _card(_data(full['client']), 'drawers')
    assert (c['count'], c['stake'], c['scope']) == (2, 2.35, 'this_computer')
    first, second = c['rows']
    assert (first['variance_status'], first['variance']) == ('unverified', -2.35)
    assert (second['variance_status'], second['variance']) == ('not_counted', None)
    assert c['extra']['not_counted'] == 1 and c['extra']['open_count'] == 0


def test_tax_card_is_honest_that_nothing_is_being_reported(full):
    c = _card(_data(full['client']), 'tax')
    assert c['scope'] == 'this_computer' and c['stake'] is None
    assert c['extra']['reason'] == 'not_connected'
    assert c['rows'][0] == {'key': 'sales_today', 'n': 4}
    assert c['rows'][1] == {'key': 'reported_today', 'n': 0}


def test_every_money_value_is_a_three_decimal_number_and_the_owner_sees_them(full):
    d = _data(full['client'])
    values = list(_money_values(d))
    paths = {p.rsplit('.', 1)[-1] for p, _ in values}
    assert {'revenue', 'amount'} <= paths
    for path, v in values:
        assert Decimal(str(v)) == Decimal(str(v)).quantize(Decimal('0.001')), (path, v)


def test_server_timing_has_one_entry_per_section(full):
    r = _today(full['client'])
    sections = [p.split(';')[0].strip() for p in r.headers['Server-Timing'].split(',')]
    assert sections == ['hero', 'trend', 'branches', 'collect', 'cheques', 'restock', 'drawers', 'tax', 'pin_locked', 'backup', 'aura_did']
    assert all('dur=' in p for p in r.headers['Server-Timing'].split(','))


def test_no_sentences_travel_from_the_server(full):
    """Every string in the payload is a kind, a key, a name or a date -- the
    client words everything (Arabic plurals cannot be a translated English
    string with a number inside)."""
    def strings(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, list):
            for v in node:
                yield from strings(v)
        elif isinstance(node, str):
            yield node
    for s in strings(_data(full['client'])):
        assert s.count(' ') <= 1, s


# ═════════════════════════════════════════════════════════════════════════════
# deny / scope / empty
# ═════════════════════════════════════════════════════════════════════════════

def test_a_cashier_is_refused(full):
    _uid, email, pw = _invite(full['client'], 'cashier')
    r = _today(_login(email, pw))
    assert r.status_code == 403, r.get_data(as_text=True)


def test_a_scoped_manager_gets_their_own_branch_and_no_branch_strip(full):
    uid, email, pw = _invite(full['client'], 'manager')
    conn = get_retail_conn()
    b_uid = conn.execute("SELECT uid FROM branches WHERE id=?", (full['B'],)).fetchone()['uid']
    conn.close()
    reg = registry_conn()
    reg.execute("UPDATE users SET branch_scope_uid=? WHERE id=?", (b_uid, uid))
    reg.commit(); reg.close()
    manager = _login(email, pw)
    # asking for branch A (the stronger branch) is silently coerced to B
    d = _data(manager, f"?branch_id={full['A']}")
    assert d['hero']['revenue'] == 40.0 and d['hero']['transactions'] == 1
    assert d['branches'] == []
    assert d['scope']['scoped'] is True and d['scope']['branch_id'] == full['B']
    assert d['scope']['branch_name'] == 'Irbid'
    assert 'branches' not in [c['kind'] for c in d['cards']]
    assert 'branches' not in [r['kind'] for r in d['all_clear']]
    # restock is the manager's own branch only: P@Irbid, not Q@Abdali
    assert [r['name'] for r in _card(d, 'restock')['rows']] == ['P']


def test_a_quiet_branch_becomes_a_card_when_the_shop_is_trading():
    shop = _shop()
    cid, A = shop['cid'], shop['branch']['Abdali']
    _sale(cid, A, TODAY, '11:00', 25.0)
    _sale(cid, shop['branch']['Irbid'], TODAY - timedelta(days=1), '10:00', 30.0)
    d = _data(shop['client'])
    c = _card(d, 'branches')
    assert (c['count'], c['stake'], c['scope']) == (1, None, 'shop')
    assert c['rows'] == [{'branch_id': shop['branch']['Irbid'], 'name': 'Irbid', 'yesterday_by_now': 30.0}]
    assert 'branches' not in [r['kind'] for r in d['all_clear']]


def test_one_branch_means_no_branch_card_and_no_strip():
    shop = _shop(branches=('Main',))
    _sale(shop['cid'], shop['branch']['Main'], TODAY, '11:00', 25.0)
    d = _data(shop['client'])
    assert d['branches'] == [] and d['scope']['branch_count'] == 1
    assert 'branches' not in [c['kind'] for c in d['cards']]
    assert 'branches' not in [r['kind'] for r in d['all_clear']]


def test_an_empty_shop_is_calm_and_says_why_and_never_shows_zero_percent():
    shop = _shop()
    d = _data(shop['client'])
    assert [c['kind'] for c in d['cards']] == ['tax']
    assert [r['kind'] for r in d['all_clear']] == ['collect', 'cheques', 'restock', 'drawers', 'branches']
    h = d['hero']
    assert (h['revenue'], h['transactions'], h['avg_ticket']) == (0.0, 0, 0.0)
    assert h['change_pct'] is None and h['margin_pct'] is None and h['tenders'] == []
    assert len(d['trend']['days']) == 14 and all(x['revenue'] == 0.0 for x in d['trend']['days'])
    assert d['trend']['average'] == 0.0
    # honest about WHY: nothing owed, nothing coming
    detail = {r['kind']: r['detail'] for r in d['all_clear']}
    assert detail['collect'] == {'owed': 0.0} and detail['branches'] == {'trading': 0}


def test_collect_is_all_clear_with_an_honest_total_when_debts_are_young():
    shop = _shop()
    cid, A = shop['cid'], shop['branch']['Abdali']
    cust = _customer(cid, 'Young')
    _sale(cid, A, TODAY - timedelta(days=5), '10:00', 80.0, method='credit', customer=cust, paid=0)
    d = _data(shop['client'])
    assert 'collect' not in [c['kind'] for c in d['cards']]
    assert {r['kind']: r['detail'] for r in d['all_clear']}['collect'] == {'owed': 80.0}


def test_aura_did_reads_the_real_logs_on_the_shops_clock(monkeypatch, tmp_path):
    shop = _shop(branches=('Main',))
    cid, client, main = shop['cid'], shop['client'], shop['branch']['Main']
    pid = _product(cid, 1.0, 2.0)
    conn = get_retail_conn()

    def reorder(stamp, n):
        conn.execute("INSERT INTO reorder_requests (id, company_id, branch_id, product_id, status, created_at) "
                     "VALUES (?,?,?,?,?,?)", (f'rr-{n}-{uuid.uuid4().hex[:6]}', cid, main, pid, 'declined', stamp))
    reorder('2026-10-04T22:30:00+00:00', 1)      # 01:30 on the 5th in Amman: TODAY
    reorder('2026-10-05T08:48:00+00:00', 2)      # 11:48 shop
    reorder('2026-10-04T10:00:00+00:00', 3)      # yesterday afternoon: not today
    conn.execute("INSERT INTO email_outbox (company_id, email_type, recipient, subject, body_text, status, "
                 "created_at, updated_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (cid, 'report_summary', 'a@b.c', 's', 'b', 'SENT', '2026-10-05T09:00:00+00:00',
                  '2026-10-05T09:00:00+00:00', '2026-10-05T09:01:00+00:00'))
    conn.execute("INSERT INTO whatsapp_outbox (company_id, message_type, recipient_phone_e164, template_name, "
                 "status, created_at, updated_at, sent_at) VALUES (?,?,?,?,?,?,?,?)",
                 (cid, 'report_summary', '+962790000000', 't', 'QUEUED', '2026-10-05T09:00:00+00:00',
                  '2026-10-05T09:00:00+00:00', None))                  # queued, never sent: not "sent"
    conn.commit(); conn.close()
    backups = tmp_path / 'backups'
    backups.mkdir()
    (backups / 'aura-retail-backup-test.zip').write_bytes(b'x')
    monkeypatch.setenv('AURA_BACKUP_DIR', str(backups))
    mtime = datetime(2026, 10, 5, 6, 5, tzinfo=timezone.utc).timestamp()     # 09:05 shop
    os.utime(backups / 'aura-retail-backup-test.zip', (mtime, mtime))
    did = {r['kind']: r for r in _data(client)['aura_did']}
    assert did['reorder_suggested'] == {'kind': 'reorder_suggested', 'count': 2, 'last_at': '11:48', 'scope': 'shop'}
    assert did['email_sent'] == {'kind': 'email_sent', 'count': 1, 'last_at': '12:01', 'scope': 'this_computer'}
    assert did['backup_made'] == {'kind': 'backup_made', 'count': 1, 'last_at': '09:05', 'scope': 'this_computer'}
    assert 'whatsapp_sent' not in did and 'einvoices_prepared' not in did


@pytest.fixture
def _killswitch_clean(monkeypatch):
    from commercial_runtime.einvoicing import killswitch
    app_dir = _app_module._EINVOICING_APP_DATA_DIR
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    killswitch.clear_disabled(app_dir)
    yield killswitch
    killswitch.clear_disabled(app_dir)


def _live_provider():
    from commercial_runtime.einvoicing.providers.direct_istd import DirectISTDProvider
    return DirectISTDProvider(base_url='https://jofotara.invalid', credentials=None)


def test_tax_reasons_and_the_connected_allow_half(monkeypatch, _killswitch_clean):
    shop = _shop(branches=('Main',))
    cid, client = shop['cid'], shop['client']
    _sale(cid, shop['branch']['Main'], TODAY, '11:00', 25.0)
    # default: e-invoicing never switched on
    assert _card(_data(client), 'tax')['extra']['reason'] == 'switched_off'
    # switched on, shipped provider -> not connected
    _set_einv(cid, tax_regime='jordan', enabled='1')
    assert _card(_data(client), 'tax')['extra']['reason'] == 'not_connected'
    # a real provider wired and nothing stuck: the card LEAVES, with an honest row
    _wire_provider(monkeypatch, _live_provider())
    _set_einv(cid, provider='direct_istd')
    _outbox(cid, 'CLEARED', n=1, cleared_today=True)
    d = _data(client)
    assert 'tax' not in [c['kind'] for c in d['cards']]
    assert {r['kind']: r['detail'] for r in d['all_clear']}['tax'] == {'sales_today': 1, 'reported_today': 1}
    # A QUEUED invoice younger than the grace window is a sale being submitted,
    # not a backlog: the card must stay away (allow half of the grace rule).
    _outbox(cid, 'QUEUED', n=1, age=timedelta(minutes=5))
    assert 'tax' not in [c['kind'] for c in _data(client)['cards']]
    # Queued for longer than the grace window IS a backlog -- and the fresh
    # one is still not counted; a permanently failed one is a rejection.
    _outbox(cid, 'QUEUED', n=2, age=timedelta(minutes=16))
    c = _card(_data(client), 'tax')
    assert (c['extra']['reason'], c['count']) == ('backlog', 2)
    conn = get_retail_conn()
    conn.execute("DELETE FROM einvoice_outbox WHERE company_id=? AND status='QUEUED'", (cid,))
    conn.commit(); conn.close()
    _outbox(cid, 'FAILED_PERMANENT', n=1)
    c = _card(_data(client), 'tax')
    assert (c['extra']['reason'], c['count']) == ('rejected', 1)
    # the kill switch pauses it (same switch the setup checklist honours)
    monkeypatch.setenv('AURA_EINVOICING_DISABLED', '1')
    assert _card(_data(client), 'tax')['extra']['reason'] == 'paused'


# ═════════════════════════════════════════════════════════════════════════════
# query count must not grow with the shop (no per-cheque / per-row query)
# ═════════════════════════════════════════════════════════════════════════════

def _count_queries(client):
    """Statements the route runs, counted through a trace callback on the very
    connections it opens."""
    counter = {'n': 0}
    real = retail_api_module.get_retail_conn

    def counting():
        conn = real()
        conn.set_trace_callback(lambda _sql: counter.__setitem__('n', counter['n'] + 1))
        return conn
    retail_api_module.get_retail_conn = counting
    try:
        _data(client)
    finally:
        retail_api_module.get_retail_conn = real
    return counter['n']


def test_query_count_does_not_grow_with_the_number_of_cheques():
    shop = _shop()
    cid, A = shop['cid'], shop['branch']['Abdali']
    cust = _customer(cid, 'Payer')
    for i in range(3):
        _cheque(cid, A, cust, 'in', 1 + i, 10.0)
    few = _count_queries(shop['client'])
    for i in range(60):
        _cheque(cid, A, cust, 'in', 1 + (i % 5), 10.0)
    many = _count_queries(shop['client'])
    assert many == few, f"{few} statements with 3 cheques, {many} with 63: a per-cheque query has crept in"

# ═════════════════════════════════════════════════════════════════════════════
# POLISH-1: two cards for things the owner used to find only on other screens
# ═════════════════════════════════════════════════════════════════════════════

def _lock_row(cid, cashier_id, approver_ref, stamp_utc):
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO audit_log (company_id, user_id, action, entity, entity_id, details, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        (cid, cashier_id, 'DISCOUNT_APPROVAL_LOCKED', 'discount_approval', approver_ref,
         f'approver={approver_ref} cashier={cashier_id} reason=too_many_wrong_pins', stamp_utc))
    conn.commit(); conn.close()


def test_pin_locked_card_names_who_and_when_inside_24_hours_only():
    shop = _shop()
    cid, client = shop['cid'], shop['client']
    reg = registry_conn()
    mgr = reg.execute("SELECT id, uid FROM users WHERE id=?", (shop['uid'],)).fetchone()
    reg.execute("UPDATE users SET display_name=? WHERE id=?", ('Hala Manager', shop['uid']))
    reg.commit(); reg.close()
    assert 'pin_locked' not in [c['kind'] for c in _data(client)['cards']]
    # 13:12 UTC "now": 10:00 UTC today is inside the window, yesterday 12:00 UTC is not
    _lock_row(cid, 'cashier-1', mgr['uid'] or mgr['id'], '2026-10-04 12:00:00')
    with _frozen():
        assert 'pin_locked' not in [c['kind'] for c in _data(client)['cards']], 'a lock older than 24 h must not show'
    _lock_row(cid, 'cashier-1', mgr['uid'] or mgr['id'], '2026-10-05 10:00:00')
    with _frozen():
        d = _data(client)
    c = _card(d, 'pin_locked')
    assert c['count'] == 1 and c['scope'] == 'this_computer' and c['stake'] is None
    assert c['rows'][0]['approver_name'] == 'Hala Manager'
    assert c['rows'][0]['at'] == '2026-10-05 13:00'           # 10:00 UTC on the Amman clock
    assert d['needs_count'] == len(d['cards'])


def test_pin_locked_card_is_company_scoped_and_unknown_approver_has_no_name():
    mine, other = _shop(), _shop()
    _lock_row(other['cid'], 'x', 'someone', '2026-10-05 10:00:00')
    with _frozen():
        assert 'pin_locked' not in [c['kind'] for c in _data(mine['client'])['cards']]
    _lock_row(mine['cid'], 'x', '*unknown*', '2026-10-05 11:00:00')
    with _frozen():
        c = _card(_data(mine['client']), 'pin_locked')
    assert c['rows'][0]['approver_name'] is None and c['rows'][0]['cashier_name'] is None


def _backup_dir(monkeypatch, tmp_path, *stamps):
    for st in stamps:
        (tmp_path / f'aura-retail-backup-{st}-app1.0-schema54.aurabak.zip').write_bytes(b'x')
    monkeypatch.setenv('AURA_BACKUP_DIR', str(tmp_path))


def test_backup_card_appears_after_48_hours_and_not_before(monkeypatch, tmp_path):
    shop = _shop()
    _backup_dir(monkeypatch, tmp_path, '20251001T000000Z')            # a year before "now"
    with _frozen():
        d = _data(shop['client'])
    c = _card(d, 'backup')
    assert c['extra']['reason'] == 'no_recent_backup' and c['extra']['last_backup_at'].startswith('2025-10-01')
    assert c['scope'] == 'this_computer' and c['stake'] is None
    # a backup made 6 hours before "now" clears it (the other direction)
    (tmp_path / 'aura-retail-backup-20261005T070000Z-app1.0-schema54.aurabak.zip').write_bytes(b'x')
    with _frozen():
        assert 'backup' not in [c['kind'] for c in _data(shop['client'])['cards']]


def test_backup_card_agrees_with_the_settings_status_on_the_47h_and_49h_edge(monkeypatch, tmp_path):
    shop = _shop()
    _backup_dir(monkeypatch, tmp_path, '20261003T141200Z')            # 47 h before 2026-10-05 13:12
    with _frozen():
        assert 'backup' not in [c['kind'] for c in _data(shop['client'])['cards']]
    for f in tmp_path.iterdir():
        f.unlink()
    _backup_dir(monkeypatch, tmp_path, '20261003T121200Z')            # 49 h before
    with _frozen():
        assert _card(_data(shop['client']), 'backup')['extra']['reason'] == 'no_recent_backup'
