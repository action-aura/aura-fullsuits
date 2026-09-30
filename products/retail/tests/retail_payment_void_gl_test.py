"""Aura Retail -- voiding a customer/supplier payment must reverse the BOOKS too.

THE DEFECT (found by the Track S money verifier, probe test_probe_money_routes):
`void_payment` un-did the SUBLEDGER (customers.credit_balance / suppliers.
credit_balance through `_adjust_credit`) and flagged the payments row 'voided',
but never touched the general ledger. Pay 20, void it: the customer balance is
back to 0 while GL Accounts Receivable still carried the 20 payment, so the
books stopped tying back to the subledger and the trial balance no longer
equalled what it was before the payment.

THE FIX: the void posts a REVERSING journal (`general_ledger.reverse_journal`;
the ledger is append-only, the original is never edited) of the very journal the
payment posted -- same accounts, same figures, sides swapped, `reversed_journal_id`
pointing at the original, `reference` = the payment's own receipt reference.

WHAT EACH TEST PROVES (all through the real routes, then read back from the
real database):
  * customer payment  -> void: GL AR ties to the subledger and the cash/AR
    accounts equal their value BEFORE the payment;
  * supplier payment  -> void: same, on AP;
  * PO instalment     -> void: same, on AP (source_type 'po_payment');
  * a second void is refused (409) and exactly ONE reversing journal exists;
  * the trial balance stays balanced throughout.

MUTATION PROOF (quoted in the crew hand-back): delete the `_reverse_payment_
journal(...)` call in `void_payment` and every ties-after-void test goes RED.

Run (one file per process):
    pytest products/retail/tests/retail_payment_void_gl_test.py -v
"""
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

# `import ... as H` (not `from ... import test_*`) so this file collects only
# ITS OWN tests; the module's import builds the isolated app + data dir.
import retail_accountant_regate_test as H  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402

API = H.API


@pytest.fixture(autouse=True)
def _reset_device_cache():
    H.device_context._cached_device = None
    yield
    H.device_context._cached_device = None


def _books(cid):
    """(trial balance, subledger tie) read from a fresh connection."""
    conn = H.get_retail_conn()
    try:
        tb = gl.trial_balance(conn, cid)
        return tb, gl.subledger_tie(conn, cid, tb)
    finally:
        conn.close()


def _balance(tb, code):
    for row in tb['accounts']:
        if row['code'] == code:
            return row['balance']
    return 0.0


def _payment_id(cid, ref):
    conn = H.get_retail_conn()
    try:
        return conn.execute('SELECT id FROM payments WHERE company_id=? AND reference=?',
                            (cid, ref)).fetchone()['id']
    finally:
        conn.close()


def _reversals_of(cid, ref):
    """Reversing journals whose ORIGINAL carries this payment reference."""
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT r.id, r.reversed_journal_id, r.source_type, r.reference, r.source_uid, "
            "       o.uid AS original_uid "
            "FROM gl_journals r JOIN gl_journals o ON o.id = r.reversed_journal_id "
            "WHERE r.company_id=? AND o.reference=?", (cid, ref)).fetchall()]
    finally:
        conn.close()


def _assert_same_as_before(tb_before, tb_after):
    for code in ('1000', '1100', '2000'):
        assert _balance(tb_after, code) == _balance(tb_before, code), (
            code, _balance(tb_before, code), _balance(tb_after, code))
    assert tb_after['total_debit'] == pytest.approx(
        tb_after['total_credit']), 'trial balance no longer balanced'


def test_voiding_a_customer_payment_reverses_the_books_as_well_as_the_subledger():
    admin, cid, _pid = H._new_shop()
    cust = H._customer(admin)
    tb0, _ = _books(cid)

    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    ref = r.get_json()['data']['reference']
    tb1, tie1 = _books(cid)
    assert tie1['ar_ties'], tie1
    assert _balance(tb1, '1100') != _balance(tb0, '1100'), 'precondition: the payment moved AR'

    r = admin.post(f'{API}/payments/{_payment_id(cid, ref)}/void', json={'reason': 'entered twice'})
    assert r.status_code == 200, r.get_json()

    tb2, tie2 = _books(cid)
    assert tie2['ar_ties'], f'void left GL AR {tie2["ar_gl"]} vs subledger {tie2["ar_subledger"]}'
    _assert_same_as_before(tb0, tb2)
    revs = _reversals_of(cid, ref)
    assert len(revs) == 1, revs
    assert revs[0]['source_type'] == 'reversal'
    assert revs[0]['reference'] == ref, 'the reversal must name the payment it undoes'
    assert revs[0]['source_uid'] == revs[0]['original_uid'], 'synced reversal must link to its original'


def test_voiding_a_supplier_payment_reverses_the_books_as_well_as_the_subledger():
    admin, cid, _pid = H._new_shop()
    sup = H._supplier(admin)
    tb0, _ = _books(cid)

    r = admin.post(f'{API}/suppliers/{sup}/payments', json={'amount': 20})
    assert r.status_code == 200, r.get_json()
    ref = r.get_json()['data']['reference']
    tb1, tie1 = _books(cid)
    assert tie1['ap_ties'], tie1
    assert _balance(tb1, '2000') != _balance(tb0, '2000'), 'precondition: the payment moved AP'

    r = admin.post(f'{API}/payments/{_payment_id(cid, ref)}/void', json={'reason': 'entered twice'})
    assert r.status_code == 200, r.get_json()

    tb2, tie2 = _books(cid)
    assert tie2['ap_ties'], f'void left GL AP {tie2["ap_gl"]} vs subledger {tie2["ap_subledger"]}'
    _assert_same_as_before(tb0, tb2)
    assert len(_reversals_of(cid, ref)) == 1


def test_voiding_a_po_instalment_reverses_the_books_as_well_as_the_subledger():
    admin, cid, pid = H._new_shop()
    sup = H._supplier(admin)
    po = H._purchase_order(admin, pid, sup)
    tb0, tie0 = _books(cid)
    assert tie0['ap_ties'], tie0

    r = admin.post(f'{API}/purchase-orders/{po}/pay', json={'amount': 5})
    assert r.status_code == 200, r.get_json()
    ref = r.get_json()['data']['reference']
    tb1, tie1 = _books(cid)
    assert tie1['ap_ties'], tie1

    r = admin.post(f'{API}/payments/{_payment_id(cid, ref)}/void', json={'reason': 'wrong PO'})
    assert r.status_code == 200, r.get_json()

    tb2, tie2 = _books(cid)
    assert tie2['ap_ties'], f'void left GL AP {tie2["ap_gl"]} vs subledger {tie2["ap_subledger"]}'
    _assert_same_as_before(tb0, tb2)
    assert len(_reversals_of(cid, ref)) == 1


def test_a_payment_cannot_be_voided_twice_and_is_reversed_exactly_once():
    admin, cid, _pid = H._new_shop()
    cust = H._customer(admin)
    tb0, _ = _books(cid)
    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    ref = r.get_json()['data']['reference']
    pay = _payment_id(cid, ref)

    assert admin.post(f'{API}/payments/{pay}/void', json={'reason': 'x'}).status_code == 200
    second = admin.post(f'{API}/payments/{pay}/void', json={'reason': 'x again'})
    assert second.status_code == 409, second.get_json()

    tb2, tie2 = _books(cid)
    assert tie2['ar_ties'], tie2
    _assert_same_as_before(tb0, tb2)
    assert len(_reversals_of(cid, ref)) == 1, 'a second void must not reverse the books a second time'


def test_the_reversal_is_a_new_journal_the_original_is_never_edited():
    """Append-only ledger: voiding adds a journal, it does not change or
    remove the one the payment posted."""
    admin, cid, _pid = H._new_shop()
    cust = H._customer(admin)
    r = admin.post(f'{API}/customers/{cust}/payments', json={'amount': 20})
    ref = r.get_json()['data']['reference']

    def snapshot():
        conn = H.get_retail_conn()
        try:
            return [tuple(x) for x in conn.execute(
                "SELECT j.id, l.account_id, l.debit, l.credit FROM gl_journals j "
                "JOIN gl_journal_lines l ON l.journal_id=j.id "
                "WHERE j.company_id=? AND j.reference=? AND j.reversed_journal_id IS NULL "
                "ORDER BY j.id, l.id", (cid, ref)).fetchall()]
        finally:
            conn.close()

    before = snapshot()
    assert before, 'precondition: the payment posted a journal'
    assert admin.post(f'{API}/payments/{_payment_id(cid, ref)}/void', json={'reason': 'x'}).status_code == 200
    assert snapshot() == before
