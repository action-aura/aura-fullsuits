"""Aura Retail -- T1 of the v44 party-balance work: a MIXED DAY through the real
routes, every party's balance read EIGHT ways, and all eight must be one number.

WHY THIS TEST EXISTS. Before v44 a customer's/supplier's balance lived in a
per-computer cache that each writer moved by a delta, and every screen read a
different mixture of that cache and the statement's events. Each of those
disagreed with another in some corner (a PO instalment void, a store-credit
return, a bounced cheque, a loyalty-points sale). The day driven here touches
every writer of a balance:

  customers  credit sale + partial payment + a payment that is then VOIDED;
             cash sale to a named customer (the tender must NOT count);
             a sale paid partly with loyalty points; a partial return that
             forgives debt; a return of a sale already paid down (store credit,
             the shop now owes the customer); a cheque received, and one
             received then BOUNCED; a split-tender sale with part on account.
  suppliers  PO with a down-payment + an instalment + an instalment that is
             VOIDED; a direct supplier payment; a supplier cheque.

For EVERY party the same figure must come out of: the derivation
(party_balance), the stored display cache, the statement's last running
balance, the receivables/payables row, the aging report, and (in total) the
trial-balance subledger tie. The expected figures are also written out by hand
below, so a formula that is wrong the same way everywhere cannot pass.

Mutations this guards (see the plan's M1/M2/M3/M9): dropping the store-credit
term, a bare `<>` on related_type (NULL drops every direct payment), dropping
the 'sale' exclusion (a cash sale's tender counted twice), and excluding 'po'
payments from the supplier formula (a PO's instalments vanish from the books).

Run:
    pytest products/retail/tests/retail_party_balance_mixed_day_test.py -v
"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_party_balance_mixed_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_SITE_RELAY_ENABLED="0",
)
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import party_balance as pb  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin(price=100.0, stock=500):
    email = f"mix-{uuid.uuid4().hex[:10]}@test.local"
    password = "MixedDayPW1x"
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, "EMP-0001", email, hash_password(password), "admin", "active"))
    conn.commit()
    conn.close()
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'Main')", (company_id,))
    branch_id = rconn.execute(
        "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]
    sku = f'MX-{uuid.uuid4().hex[:8]}'
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, sku, sku, price / 2, price))
    pid = rconn.execute("SELECT id FROM products WHERE company_id=? AND sku=?", (company_id, sku)).fetchone()[0]
    rconn.execute(
        "INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
        (company_id, pid, branch_id, stock))
    rconn.commit()
    rconn.close()
    client = app.test_client()
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    client.get(f"{API}/settings/tax")
    dconn = get_retail_conn()
    for _doc_type in ('sale', 'return'):
        dconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,?,?)",
                      (company_id, _doc_type, random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()
    return client, company_id, pid


def _customer(cid, name):
    rconn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO customers (id,company_id,name,credit_mode,credit_limit,credit_balance) "
        "VALUES (?,?,?,'unlimited',0,0)", (customer_id, cid, name))
    rconn.commit()
    rconn.close()
    return customer_id


def _supplier(cid, name):
    rconn = get_retail_conn()
    sid = str(uuid.uuid4())
    rconn.execute("INSERT INTO suppliers (id,company_id,name,credit_balance) VALUES (?,?,?,0)", (sid, cid, name))
    rconn.commit()
    rconn.close()
    return sid


def _sell(client, customer_id, pid, amount_paid, method='credit', qty=1, **extra):
    body = {'items': [{'product_id': pid, 'quantity': qty}], 'customer_id': customer_id,
            'amount_paid': amount_paid, 'payment_method': method, 'idempotency_key': str(uuid.uuid4())}
    body.update(extra)
    r = client.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return(client, sale_id, pid, qty=1):
    r = client.post(f'{API}/returns', json={
        'sale_id': sale_id, 'items': [{'product_id': pid, 'quantity': qty}],
        'reason': 'mixed day', 'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _cheque(client, direction, party_id, amount):
    r = client.post(f'{API}/cheques', json={
        'direction': direction, 'party_id': party_id, 'amount': amount,
        'cheque_number': f'MX-{uuid.uuid4().hex[:8]}', 'due_date': '2026-12-01'})
    assert r.status_code == 201, r.get_json()
    return r.get_json()['data']['id']


def _payment_id(cid, party_id, related_type, amount):
    conn = get_retail_conn()
    try:
        if related_type is None:
            row = conn.execute(
                "SELECT id FROM payments WHERE company_id=? AND party_id=? AND related_type IS NULL AND amount=? "
                "ORDER BY id DESC LIMIT 1", (cid, party_id, amount)).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM payments WHERE company_id=? AND party_id=? AND related_type=? AND amount=? "
                "ORDER BY id DESC LIMIT 1", (cid, party_id, related_type, amount)).fetchone()
        assert row, (party_id, related_type, amount)
        return row[0]
    finally:
        conn.close()


@pytest.fixture(scope='module')
def day():
    client, cid, pid = _make_admin(price=100.0)
    names = ['A_credit_paid_voided', 'B_cash_named', 'C_points', 'D_partial_return', 'E_store_credit',
             'F_cheques', 'G_split']
    cust = {n: _customer(cid, n) for n in names}
    rconn = get_retail_conn()
    rconn.execute("INSERT INTO loyalty_ledger (uid,company_id,customer_id,points_delta,entry_type) VALUES (?,?,?,?,'opening')",
                  (str(uuid.uuid4()), cid, cust['C_points'], 30))
    rconn.execute("INSERT OR REPLACE INTO retail_settings (company_id,skey,svalue) VALUES (?,'loyalty_point_value','1.0')", (cid,))
    rconn.commit()
    rconn.close()

    # A: credit sale 100 with 40 paid -> 60; pays 25 -> 35; pays 10 then that payment is VOIDED -> 35.
    _sell(client, cust['A_credit_paid_voided'], pid, 40.0)
    assert client.post(f"{API}/customers/{cust['A_credit_paid_voided']}/payments", json={'amount': 25}).status_code == 200
    assert client.post(f"{API}/customers/{cust['A_credit_paid_voided']}/payments", json={'amount': 10}).status_code == 200
    vid = _payment_id(cid, cust['A_credit_paid_voided'], None, 10.0)
    rv = client.post(f'{API}/payments/{vid}/void', json={'reason': 'mixed day'})
    assert rv.status_code == 200, rv.get_json()
    # B: fully paid cash sale to a named customer: the tender must NOT count.
    _sell(client, cust['B_cash_named'], pid, 100.0, method='cash')
    # C: 30 loyalty points redeemed on a credit sale of 100 -> owes 70.
    _sell(client, cust['C_points'], pid, 0.0, points_redeemed=30)
    # D: credit sale of 2 units (200), then return 1 -> the debt for it is forgiven.
    sd = _sell(client, cust['D_partial_return'], pid, 0.0, qty=2)
    _return(client, sd['id'], pid, qty=1)
    # E: credit sale 100, paid down on account, THEN returned -> store credit, the shop owes the customer.
    se = _sell(client, cust['E_store_credit'], pid, 0.0)
    assert client.post(f"{API}/customers/{cust['E_store_credit']}/payments", json={'amount': 100}).status_code == 200
    _return(client, se['id'], pid, qty=1)
    # F: credit sale 100; cheque 60 received; a second cheque 30 received then BOUNCED.
    _sell(client, cust['F_cheques'], pid, 0.0)
    _cheque(client, 'in', cust['F_cheques'], 60.0)
    c2 = _cheque(client, 'in', cust['F_cheques'], 30.0)
    rb = client.post(f'{API}/cheques/{c2}/bounce', json={'reason': 'Insufficient funds'})
    assert rb.status_code == 200, rb.get_json()
    # G: split tender: 30 cash + 70 on account.
    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'customer_id': cust['G_split'],
        'tenders': [{'method': 'cash', 'amount': 30}, {'method': 'credit', 'amount': 70}],
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()

    sup = {n: _supplier(cid, n) for n in ('S1_po_instalments', 'S2_direct', 'S3_cheque')}
    # S1: PO 200, 50 down, 30 instalment, a 20 instalment that is then VOIDED -> owes 120.
    po = client.post(f'{API}/purchase-orders', json={
        'supplier_id': sup['S1_po_instalments'], 'amount_paid': 50,
        'items': [{'product_id': pid, 'quantity': 4, 'unit_cost': 50}]})
    assert po.status_code == 200, po.get_json()
    po_id = po.get_json()['data']['id']
    assert client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 30}).status_code == 200
    assert client.post(f'{API}/purchase-orders/{po_id}/pay', json={'amount': 20}).status_code == 200
    pv = _payment_id(cid, sup['S1_po_instalments'], 'po', 20.0)
    rv2 = client.post(f'{API}/payments/{pv}/void', json={'reason': 'mixed day'})
    assert rv2.status_code == 200, rv2.get_json()
    # S2: a direct payment with no PO -> the shop is owed 12.
    assert client.post(f"{API}/suppliers/{sup['S2_direct']}/payments", json={'amount': 12}).status_code == 200
    # S3: PO 100 unpaid, then a cheque issued to the supplier for 40 -> owes 60.
    po3 = client.post(f'{API}/purchase-orders', json={
        'supplier_id': sup['S3_cheque'], 'amount_paid': 0,
        'items': [{'product_id': pid, 'quantity': 2, 'unit_cost': 50}]})
    assert po3.status_code == 200, po3.get_json()
    _cheque(client, 'out', sup['S3_cheque'], 40.0)
    return {'client': client, 'cid': cid, 'cust': cust, 'sup': sup}


EXPECTED_CUSTOMERS = {
    'A_credit_paid_voided': Decimal('35.000'),
    'B_cash_named': Decimal('0.000'),
    'C_points': Decimal('70.000'),
    'D_partial_return': Decimal('100.000'),
    'E_store_credit': Decimal('-100.000'),
    'F_cheques': Decimal('40.000'),
    'G_split': Decimal('70.000'),
}
EXPECTED_SUPPLIERS = {
    'S1_po_instalments': Decimal('120.000'),
    'S2_direct': Decimal('-12.000'),
    'S3_cheque': Decimal('60.000'),
}


def _stored(table, pid):
    conn = get_retail_conn()
    try:
        return Decimal(str(conn.execute(f"SELECT credit_balance FROM {table} WHERE id=?", (pid,)).fetchone()[0]))
    finally:
        conn.close()


def test_every_customer_balance_reads_the_same_eight_ways(day):
    client, cid = day['client'], day['cid']
    conn = get_retail_conn()
    conn.row_factory = sqlite3.Row
    try:
        derived = pb.customer_balances(conn, cid, 'JOD')
    finally:
        conn.close()
    receivables = {r['id']: Decimal(str(r['credit_balance']))
                   for r in client.get(f'{API}/customers/receivables').get_json()['data']}
    aging = {c['id']: c for c in client.get(f'{API}/reports/aging?type=receivable').get_json()['customers']}
    for name, cust in day['cust'].items():
        want = EXPECTED_CUSTOMERS[name]
        assert derived[cust] == want, f"{name}: derived {derived[cust]} != expected {want}"
        assert _stored('customers', cust) == want, f"{name}: stored cache != expected {want}"
        st = client.get(f'{API}/customers/{cust}/statement').get_json()['data']
        last = Decimal(str(st['events'][-1]['running_balance'])) if st['events'] else Decimal('0')
        assert last == want, f"{name}: statement last running balance {last} != {want}"
        assert Decimal(str(st['balance'])) == want, f"{name}: statement balance != {want}"
        assert Decimal(str(st['unbooked_amount'])) == 0, f"{name}: unbooked must be 0 by construction"
        if want > 0:
            assert receivables.get(cust) == want, f"{name}: receivables row {receivables.get(cust)} != {want}"
            row = aging[cust]
            assert Decimal(str(row['balance'])) == want
            assert sum(Decimal(str(v)) for v in row['buckets'].values()) == want, f"{name}: aging buckets {row['buckets']}"
        else:
            assert cust not in receivables, f"{name}: a customer not owing must not be on the debtor book"
            assert cust not in aging


def test_every_supplier_balance_reads_the_same_ways(day):
    client, cid = day['client'], day['cid']
    conn = get_retail_conn()
    try:
        derived = pb.supplier_balances(conn, cid, 'JOD')
    finally:
        conn.close()
    pay = client.get(f'{API}/suppliers/payables').get_json()
    payables = {r['id']: Decimal(str(r['credit_balance'])) for r in pay['data']}
    for name, sup in day['sup'].items():
        want = EXPECTED_SUPPLIERS[name]
        assert derived[sup] == want, f"{name}: derived {derived[sup]} != expected {want}"
        assert _stored('suppliers', sup) == want, f"{name}: stored cache != expected {want}"
        st = client.get(f'{API}/suppliers/{sup}/statement').get_json()['data']
        last = Decimal(str(st['events'][-1]['running_balance'])) if st['events'] else Decimal('0')
        assert last == want, f"{name}: statement last running balance {last} != {want}"
        assert Decimal(str(st['balance'])) == want
        if want > 0:
            assert payables.get(sup) == want, f"{name}: payables row {payables.get(sup)} != {want}"
        else:
            assert sup not in payables
    assert Decimal(str(pay['total_payable'])) == sum(v for v in EXPECTED_SUPPLIERS.values() if v > 0)
    ag = client.get(f'{API}/reports/aging?type=payable').get_json()['data']
    assert sum(Decimal(str(v)) for v in ag.values()) == Decimal(str(pay['total_payable']))


def test_the_trial_balance_subledger_ties_on_a_mixed_day(day):
    """AR/AP in the books equal the SUM of the derived per-party balances. A
    one-computer shop only: the books and the party balances are both
    shop-wide, so they must agree here exactly."""
    sub = day['client'].get(f'{API}/reports/trial-balance').get_json()['data']['subledger']
    # The subledger side is exactly the sum of the derived balances.
    assert Decimal(str(sub['ar_subledger'])) == sum(EXPECTED_CUSTOMERS.values())
    assert Decimal(str(sub['ap_subledger'])) == sum(EXPECTED_SUPPLIERS.values())
    # PRE-EXISTING GAP, measured here, not caused by v44 and not hidden: a
    # cheque's money leg (a `payments` row, related_type='cheque') moves the
    # party balance but is never posted to the general ledger
    # (general_ledger.py has no cheque posting). On this day the books
    # therefore sit above the subledger by exactly the NET cheque money:
    # AR by 60 (cheque 60 received; 30 received then bounced nets 0) and AP by
    # 40 (the supplier cheque). Everything that is NOT a cheque ties. The
    # difference is asserted EXACTLY, so this goes red the day cheque posting
    # lands (update CHEQUE_NET_* then) and also if any other term drifts.
    CHEQUE_NET_AR, CHEQUE_NET_AP = Decimal('60'), Decimal('40')
    assert Decimal(str(sub['ar_gl'])) - Decimal(str(sub['ar_subledger'])) == CHEQUE_NET_AR, sub
    assert Decimal(str(sub['ap_gl'])) - Decimal(str(sub['ap_subledger'])) == CHEQUE_NET_AP, sub


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
