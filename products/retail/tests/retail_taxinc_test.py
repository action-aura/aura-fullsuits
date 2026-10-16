"""
Aura Retail -- TAXINC: tax-inclusive pricing (Jordan shelf prices include tax).

THE DEFECT THIS CLOSES: `sell_price` used to be ex-tax only, so a shop whose shelf
says 0.250 had to type 0.216 (0.250 / 1.16, rounded) and the till then charged
0.216 + round(0.216 * 0.16) = 0.251. Every third price was a fil off on the
receipt and on the e-invoice. TAX_INCLUSIVE stores the shelf price and carves the
tax out of it.

Part A -- pure unit tests of core/retail/pricing.py:
  * the property: for EVERY 3-dp shelf price 0.001..10.000 x qty {1,2,3,7} x rates
    {0,4,8,10,16}%, total == round(price*qty) and taxable + tax == total;
  * the two pre-existing modes are byte-identical to the module as it stood before
    TAXINC (432 recorded rows in retail_taxinc_exclusive_table.json, produced by
    running the PRE-TAXINC pricing.py -- not by this one);
  * historical_line_mode / header_is_tax_inclusive.
Part B -- the real routes: sale, receipt, return after a mode flip, e-invoice, GL.

Run:  pytest products/retail/tests/retail_taxinc_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import pricing  # noqa: E402

D = Decimal
FIL = D('0.001')


def _round_fil(x):
    return x.quantize(FIL, rounding=ROUND_HALF_UP)


# =============================================================================
# Part A -- pure
# =============================================================================

def test_inclusive_total_is_the_shelf_price_for_every_3dp_price():
    """THE property. 10,000 prices x 4 quantities x 5 rates = 200,000 lines.

    The pass condition is NOT the bug signature: the old exclusive route fails the
    same kind of loop for ~1/3 of prices (see the control test below), so a green
    run cannot be 'nothing was checked'. `checked` is asserted so an empty loop
    cannot pass either."""
    checked = 0
    for fils in range(1, 10001):
        price = float(D(fils) / 1000)
        for qty in (1, 2, 3, 7):
            expected_total = _round_fil(D(fils) / 1000 * qty)
            for rate in (0, 4, 8, 10, 16):
                r = pricing.calculate_line(price, qty, 0, rate, mode=pricing.TAX_INCLUSIVE, currency='JOD')
                total, taxable, tax = D(str(r['total'])), D(str(r['taxable_amount'])), D(str(r['tax']))
                assert total == expected_total, (price, qty, rate, r)
                assert taxable + tax == total, (price, qty, rate, r)
                assert tax >= 0, (price, qty, rate, r)
                checked += 1
    assert checked == 10000 * 4 * 5


def test_old_route_really_does_drift_on_the_same_prices():
    """Control for the property above: it must be CAPABLE of failing. Entering the
    ex-tax price the shop used to have to type (shelf / 1.16, rounded to a fil)
    and pricing it the old way misses the shelf price for a large share of prices."""
    missed = 0
    for fils in range(1, 2001):
        shelf = D(fils) / 1000
        ex_price = float(_round_fil(shelf / D('1.16')))
        r = pricing.calculate_line(ex_price, 1, 0, 16, mode=pricing.TAX_AFTER_DISCOUNT, currency='JOD')
        if D(str(r['total'])) != shelf:
            missed += 1
    # Measured: 276 of the first 2,000 shelf prices (0.250 -> 0.251 is one of them). An
    # earlier draft of this threshold said 500 ('a third of prices') from memory; the
    # measurement is ~14%, so the bound is set from the measurement, not the other way.
    assert missed > 200, missed


@pytest.mark.parametrize('discount_pct', [0, 7.5, 10, 33.3333, 100])
def test_inclusive_discount_is_taken_off_the_inclusive_figure(discount_pct):
    r = pricing.calculate_line(0.250, 3, discount_pct, 16, mode=pricing.TAX_INCLUSIVE, currency='JOD')
    gross = D('0.750')
    disc = _round_fil(gross * D(str(discount_pct)) / 100)
    assert r['gross'] == float(gross)
    assert r['discount_amount'] == float(disc)
    assert r['total'] == float(gross - disc)                       # tax is INSIDE the total
    assert D(str(r['taxable_amount'])) + D(str(r['tax'])) == D(str(r['total']))
    assert r['taxable_amount'] == float(_round_fil((gross - disc) / D('1.16')))


def test_inclusive_known_shelf_price():
    r = pricing.calculate_line(0.250, 1, 0, 16, mode=pricing.TAX_INCLUSIVE, currency='JOD')
    assert (r['total'], r['taxable_amount'], r['tax']) == (0.25, 0.216, 0.034)


def test_calculate_invoice_inclusive_reconciles():
    r = pricing.calculate_invoice(10.0, 1.0, 16, mode=pricing.TAX_INCLUSIVE, currency='JOD')
    assert r['total'] == 9.0
    assert D(str(r['taxable_amount'])) + D(str(r['tax'])) == D('9.000')


def test_inclusive_is_a_valid_mode_and_old_defaults_unchanged():
    assert pricing.TAX_INCLUSIVE in pricing.VALID_MODES
    assert pricing.normalize_mode('inclusive') == pricing.TAX_INCLUSIVE
    assert pricing.DEFAULT_MODE == pricing.TAX_AFTER_DISCOUNT
    assert pricing.normalize_mode('garbage') == pricing.TAX_AFTER_DISCOUNT


def test_exclusive_modes_are_byte_identical_to_pre_taxinc():
    """432 rows recorded from the PRE-TAXINC module (git c095c7d8). Any change to
    either exclusive formula -- including a 'harmless' refactor while adding the
    third mode -- turns this red. What it cannot catch: a change in a combination
    of price/qty/pct/rate/currency that is not in the table."""
    rows = json.loads((Path(__file__).with_name('retail_taxinc_exclusive_table.json')).read_text())
    assert len(rows) == 432
    for price, qty, pct, rate, mode, cur, gross, disc, taxable, tax, total in rows:
        r = pricing.calculate_line(price, qty, pct, rate, mode=mode, currency=cur)
        assert (r['gross'], r['discount_amount'], r['taxable_amount'], r['tax'], r['total']) == \
            (gross, disc, taxable, tax, total), (price, qty, pct, rate, mode, cur, r)


def test_calculation_version_was_bumped():
    assert pricing.CALCULATION_VERSION == 'retail-pricing-v4-tax-inclusive'


# -- historical_line_mode ----------------------------------------------------

def _stored_taxable(mode, price=0.250, qty=3, pct=0, rate=16):
    return pricing.calculate_line(price, qty, pct, rate, mode=mode, currency='JOD')['taxable_amount']


def test_historical_mode_recovers_inclusive_after_switch_to_exclusive():
    st = _stored_taxable(pricing.TAX_INCLUSIVE)
    for cur in (pricing.TAX_AFTER_DISCOUNT, pricing.TAX_BEFORE_DISCOUNT):
        assert pricing.historical_line_mode(cur, 0.250, 3, 0, 16, st, 'JOD') == pricing.TAX_INCLUSIVE


def test_historical_mode_recovers_exclusive_after_switch_to_inclusive():
    for sold_as in (pricing.TAX_AFTER_DISCOUNT, pricing.TAX_BEFORE_DISCOUNT):
        st = _stored_taxable(sold_as, pct=10)
        assert pricing.historical_line_mode(
            pricing.TAX_INCLUSIVE, 0.250, 3, 10, 16, st, 'JOD') == sold_as


def test_historical_mode_never_separates_the_two_exclusive_modes():
    """Pre-TAXINC behaviour for installs that never use inclusive: the CURRENT mode
    wins, exactly as before."""
    st = _stored_taxable(pricing.TAX_BEFORE_DISCOUNT, pct=10)
    assert pricing.historical_line_mode(pricing.TAX_AFTER_DISCOUNT, 0.250, 3, 10, 16, st, 'JOD') \
        == pricing.TAX_AFTER_DISCOUNT


def test_historical_mode_zero_rate_none_and_unmatched_fall_back_to_current():
    assert pricing.historical_line_mode('after_discount', 1.0, 1, 0, 0, 1.0, 'JOD') == 'after_discount'
    assert pricing.historical_line_mode('after_discount', 1.0, 1, 0, 16, None, 'JOD') == 'after_discount'
    assert pricing.historical_line_mode('inclusive', 1.0, 1, 0, 16, 123.456, 'JOD') == 'inclusive'


# -- header_is_tax_inclusive -------------------------------------------------

def test_header_inference_distinguishes_inclusive_from_both_exclusive_modes():
    for mode, expect in (('inclusive', True), ('after_discount', False), ('before_discount', False)):
        r = pricing.calculate_line(0.250, 3, 10, 16, mode=mode, currency='JOD')
        assert pricing.header_is_tax_inclusive(
            r['gross'], r['discount_amount'], r['tax'], r['total'], 'JOD') is expect, mode
    # zero tax: nothing depends on the answer, and it must not claim inclusive
    r = pricing.calculate_line(0.250, 1, 0, 0, mode='inclusive', currency='JOD')
    assert pricing.header_is_tax_inclusive(r['gross'], r['discount_amount'], r['tax'], r['total'], 'JOD') is False


# =============================================================================
# Part B -- real routes
# =============================================================================

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_taxinc_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ["AURA_EINVOICING_ALLOW_MOCK"] = "1"

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    for worker in _app_module._einvoicing_workers.values():
        worker.stop()
    shutil.rmtree(DATA, ignore_errors=True)


def _make_shop(price=0.250, tax_rate=16.0, stock=500):
    email = f"taxinc-{uuid.uuid4().hex[:10]}@test.local"
    password = "TaxincPW1"
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
    branch_id = rconn.execute("SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1", (company_id,)).fetchone()[0]
    product_id = str(uuid.uuid4())
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price,tax_rate) VALUES (?,?,'TI-1','Shelf Item',0.1,?,?)",
        (product_id, company_id, price, tax_rate))
    rconn.execute("INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) VALUES (?,?,?,?)",
                  (company_id, product_id, branch_id, stock))
    rconn.commit()
    rconn.close()
    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    client.get("/api/sub/retail/settings/tax")
    import random as _random
    dconn = get_retail_conn()
    dconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
                  (company_id, _random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()
    return client, company_id, product_id


def _set_mode(client, mode):
    r = client.post("/api/sub/retail/settings/tax", json={"tax_calculation_mode": mode})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["tax_calculation_mode"] == mode


def _sell(client, pid, quantity=1, extra=None):
    payload = {'items': [{'product_id': pid, 'quantity': quantity}], 'amount_paid': 999999,
               'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4())}
    if extra:
        payload.update(extra)
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _return_all(client, sale, pid, quantity):
    r = client.post('/api/sub/retail/returns', json={
        'sale_id': sale['id'], 'items': [{'product_id': pid, 'quantity': quantity}], 'reason': 'test'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def test_tax_settings_offers_inclusive():
    client, _c, _p = _make_shop()
    r = client.get("/api/sub/retail/settings/tax").get_json()["data"]
    assert set(r["available_modes"]) == {"after_discount", "before_discount", "inclusive"}
    _set_mode(client, "inclusive")


def test_inclusive_sale_charges_the_shelf_price_to_the_fil():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    for qty in (1, 2, 3, 7):
        sale = _sell(client, pid, qty)
        assert D(str(sale['total'])) == _round_fil(D('0.250') * qty), sale
        assert D(str(sale['subtotal'])) - D(str(sale['discount_amount'])) == D(str(sale['total']))
    conn = get_retail_conn()
    row = conn.execute(
        "SELECT si.unit_price, si.line_total, s.tax_amount, s.total FROM sale_items si JOIN sales s ON s.id=si.sale_id "
        "WHERE s.id=?", (sale['id'],)).fetchone()
    conn.close()
    # unit_price keeps what the product row says; line_total stays the TAXABLE figure.
    assert row['unit_price'] == 0.25
    assert D(str(row['line_total'])) + D(str(row['tax_amount'])) == D(str(row['total']))
    assert D(str(row['line_total'])) == _round_fil(D('1.750') / D('1.16'))


def test_gl_books_revenue_and_tax_that_sum_to_the_total():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 3)
    conn = get_retail_conn()
    rows = conn.execute(
        "SELECT a.code AS code, SUM(l.credit) AS cr FROM gl_journal_lines l "
        "JOIN gl_journals j ON j.id=l.journal_id JOIN gl_accounts a ON a.id=l.account_id "
        "WHERE j.company_id=? AND j.source_type='sale' AND j.source_id=? GROUP BY a.code", (cid, sale['id'])).fetchall()
    conn.close()
    cr = {r['code']: D(str(r['cr'])) for r in rows}
    assert cr['4000'] + cr['2100'] == D(str(sale['total']))        # revenue + tax == total, not total + tax
    assert cr['2100'] == D(str(sale['tax_amount']))


def test_return_after_switching_mode_refunds_exactly_what_was_paid():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 3)
    paid = D(str(sale['total']))
    assert paid == D('0.750')
    _set_mode(client, "after_discount")               # the owner flips the setting
    ret = _return_all(client, sale, pid, 3)
    assert D(str(ret['refund_amount'])) == paid, ret


def test_partial_returns_after_switch_sum_to_the_paid_line():
    client, cid, pid = _make_shop(price=0.333)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 3)
    paid = D(str(sale['total']))
    _set_mode(client, "before_discount")
    parts = [_return_all(client, sale, pid, 1) for _ in range(3)]
    # EACH part, not just the sum: the completing part takes the remainder, so a sum alone
    # hides a mispriced first/second part (the mutation 'return re-prices with the current
    # mode' passed a sum-only version of this test).
    assert [D(str(p['refund_amount'])) for p in parts] == [D('0.333')] * 3
    assert sum(D(str(p['refund_amount'])) for p in parts) == paid
    # and the tax each part gives back is the inclusive tax inside it (0.333 - 0.287)
    conn = get_retail_conn()
    taxes = [D(str(r[0])) for r in conn.execute(
        "SELECT tax_refunded FROM returns WHERE sale_id=? ORDER BY id", (sale['id'],)).fetchall()]
    conn.close()
    assert taxes[:2] == [D('0.046')] * 2, taxes


def test_exclusive_sale_returned_after_switch_to_inclusive():
    client, cid, pid = _make_shop(price=0.216)
    sale = _sell(client, pid, 3)                       # default after_discount
    paid = D(str(sale['total']))
    _set_mode(client, "inclusive")
    ret = _return_all(client, sale, pid, 3)
    assert D(str(ret['refund_amount'])) == paid


def test_receipt_model_says_tax_is_included():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 2)
    r = client.get(f"/api/sub/retail/printer/receipt-model?sale_id={sale['id']}")
    assert r.status_code == 200, r.get_json()
    text = json.dumps(r.get_json(), ensure_ascii=False)
    assert 'Tax (included)' in text
    # and a plain exclusive sale does NOT claim it
    _set_mode(client, "after_discount")
    sale2 = _sell(client, pid, 2)
    text2 = json.dumps(client.get(f"/api/sub/retail/printer/receipt-model?sale_id={sale2['id']}").get_json(),
                       ensure_ascii=False)
    assert 'Tax (included)' not in text2


def test_cart_amount_discount_applies_to_the_inclusive_figure():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 2, extra={'cart_discount_amount': 0.100, 'discount_reason': 'Damaged'})
    assert D(str(sale['discount_amount'])) == D('0.100')
    assert D(str(sale['total'])) == D('0.400')
    assert D(str(sale['subtotal'])) - D(str(sale['discount_amount'])) == D(str(sale['total']))


def _einvoice_doc(cid, sale_id=None, return_id=None):
    import core.retail.einvoice_adapter as adapter
    conn = get_retail_conn()
    try:
        st, sid = ('return', return_id) if return_id else ('sale', sale_id)
        row = conn.execute("SELECT * FROM einvoice_outbox WHERE company_id=? AND source_type=? AND source_id=?",
                           (cid, st, sid)).fetchone()
        assert row is not None, f"no outbox row for {st} {sid}"
        return adapter.build_document(conn, row)
    finally:
        conn.close()


def _enable_einvoicing(client):
    r = client.post('/api/einvoicing/settings', json={'enabled': '1', 'provider': 'mock', 'tax_regime': 'jordan',
                                                      'currency': 'JOD'})
    assert r.status_code == 200, r.get_json()


def test_einvoice_totals_are_consistent_for_an_inclusive_sale():
    from commercial_runtime.einvoicing.istd_ubl import validate_istd_document
    client, cid, pid = _make_shop(price=0.250)
    _enable_einvoicing(client)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 3)
    _set_mode(client, "after_discount")               # filed AFTER the owner flipped the setting
    doc = _einvoice_doc(cid, sale_id=sale['id'])
    assert abs(doc.subtotal - doc.discount_total + doc.tax_total - doc.grand_total) < 0.0005
    assert abs(doc.grand_total - 0.750) < 1e-9
    line = doc.lines[0]
    # extension = line_total - allowance = taxable 0.647; tax 0.103
    assert abs(line.line_total - line.discount_amount - 0.647) < 1e-9 and abs(line.tax_amount - 0.103) < 1e-9, line
    assert abs(sum(l.line_total - l.discount_amount + l.tax_amount for l in doc.lines) - doc.grand_total) < 0.0005
    doc_istd = type(doc)(**{**doc.__dict__, 'profile': 'ISTD_JO_1_0', 'icv': 1, 'uuid': str(uuid.uuid4()),
                            'type_name': '011', 'activity_code': '1'})
    errs = [e for e in validate_istd_document(doc_istd) if e.startswith('R-TOTAL') or e.startswith('R-LINE')]
    assert errs == [], errs


def test_credit_note_for_an_inclusive_sale_returned_after_a_mode_flip_sums_to_the_refund():
    client, cid, pid = _make_shop(price=0.250)
    _enable_einvoicing(client)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, 3)
    _set_mode(client, "after_discount")
    ret = _return_all(client, sale, pid, 3)
    import core.retail.einvoice_adapter as adapter
    assert adapter.enqueue_return(get_retail_conn, company_id=cid, return_id=ret['id'],
                                  return_number=ret.get('return_number') or 'R-1') is True
    doc = _einvoice_doc(cid, return_id=ret['id'])
    assert abs(doc.grand_total - float(ret['refund_amount'])) < 0.0005
    assert abs(sum(l.line_total - l.discount_amount + l.tax_amount for l in doc.lines) - doc.grand_total) < 0.0005
    # The inclusive shape, not merely consistent totals: the exclusive builder reaches the same
    # sums by inventing a discount equal to the TAX (gross 0.750, discount 0.103), which is
    # self-consistent and WRONG on a credit note. Here the allowance is only the sub-fil
    # rounding remainder of the price (< qty fils).
    line = doc.lines[0]
    assert line.discount_amount < line.quantity * 0.001, line


# -- the EMITTED XML (not the in-memory document) must reconcile ---------------------------
# The serializers quantize PriceAmount to the currency's minor unit, so a document can be
# self-consistent in memory and wrong on the wire (JOD 16%, 0.250 x 7: extension 1.509,
# PriceAmount 0.216, 7 x 0.216 = 1.512). These tests PARSE the generated XML and recompute.

def _xml_lines(xml):
    import xml.etree.ElementTree as ET

    def local(e):
        return e.tag.split('}')[-1]

    def find(e, name):
        return next((c for c in e.iter() if local(c) == name), None)

    root = ET.fromstring(xml)
    out = []
    for ln in [e for e in root.iter() if local(e) == 'InvoiceLine']:
        price_el = find(ln, 'Price')
        allow = find(price_el, 'AllowanceCharge')
        out.append({
            'qty': D(find(ln, 'InvoicedQuantity').text),
            'ext': D(find(ln, 'LineExtensionAmount').text),
            'price': D(find(price_el, 'PriceAmount').text),
            'allow': D(find(allow, 'Amount').text) if allow is not None else D(0),
        })
    return root, out


def _emit(doc):
    from commercial_runtime.einvoicing import ubl
    from commercial_runtime.einvoicing.istd_ubl import to_istd_xml
    generic = ubl.to_ubl_xml(doc)
    istd = type(doc)(**{**doc.__dict__, 'profile': 'ISTD_JO_1_0', 'icv': 1, 'uuid': str(uuid.uuid4()),
                        'type_name': '012', 'invoice_family': 'general_sales', 'activity_code': '1', 'seller_tin': doc.seller_tin or '1',
                        'seller_name': doc.seller_name or 'S'})
    if doc.document_kind == 'credit_note':
        istd = type(doc)(**{**istd.__dict__, 'original_einvoice_no': 'INC-000001', 'original_uuid': str(uuid.uuid4()),
                            'original_total': doc.grand_total, 'return_reason': 'x'})
    return generic, to_istd_xml(istd)


@pytest.mark.parametrize('qty', [1, 7, 100])
def test_emitted_xml_reconciles_qty_times_price_for_inclusive_sale_and_credit_note(qty):
    client, cid, pid = _make_shop(price=0.250)
    _enable_einvoicing(client)
    _set_mode(client, "inclusive")
    sale = _sell(client, pid, qty)
    _set_mode(client, "after_discount")
    ret = _return_all(client, sale, pid, qty)
    import core.retail.einvoice_adapter as adapter
    assert adapter.enqueue_return(get_retail_conn, company_id=cid, return_id=ret['id'],
                                  return_number=ret.get('return_number') or 'R-1') is True
    for doc in (_einvoice_doc(cid, sale_id=sale['id']), _einvoice_doc(cid, return_id=ret['id'])):
        generic, istd = _emit(doc)
        _root, g_lines = _xml_lines(generic)
        _root, i_lines = _xml_lines(istd)
        assert g_lines and i_lines
        for l in g_lines:                       # generic: no per-line allowance, header carries it
            assert abs(l['qty'] * l['price'] - l['ext']) <= D('0.001'), ('generic', qty, l)
        for l in i_lines:                       # ISTD: standard UBL identity, to the fil
            assert abs(l['qty'] * l['price'] - l['allow'] - l['ext']) <= D('0.001'), ('istd', qty, l)
            assert l['allow'] >= 0 and l['allow'] < l['qty'] * D('0.001') + D('0.0005'), l
        # and the total payable still equals the shelf price / refund
        assert abs(doc.grand_total - float(D('0.250') * qty)) < 1e-9


def test_sales_csv_flags_inclusive_sales_so_the_four_sale_columns_are_interpretable():
    import csv
    import io
    from datetime import date, timedelta
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    inc = _sell(client, pid, 3)
    _set_mode(client, "after_discount")
    exc = _sell(client, pid, 3)
    d0, d1 = (date.today() - timedelta(days=1)).isoformat(), (date.today() + timedelta(days=1)).isoformat()
    r = client.get(f"/api/sub/retail/reports/export/sales?date_from={d0}&date_to={d1}")
    assert r.status_code == 200, r.data[:300]
    rows = list(csv.reader(io.StringIO(r.data.decode('utf-8-sig'))))
    header, body = rows[0], rows[1:]
    flag = {int(row[header.index('sale_id')]): row[header.index('sale_tax_inclusive')] for row in body}
    assert flag[inc['id']] == '1' and flag[exc['id']] == '0', flag
    # and the flag really does select the identity that holds
    for row in body:
        sub_, dis, tax, tot = (D(row[header.index(c)]) for c in
                               ('sale_subtotal', 'sale_discount_amount', 'sale_tax_amount', 'sale_total'))
        if row[header.index('sale_tax_inclusive')] == '1':
            assert sub_ - dis == tot
        else:
            assert sub_ - dis + tax == tot


def test_cart_discount_over_the_total_error_is_mode_correct():
    client, cid, pid = _make_shop(price=0.250)
    _set_mode(client, "inclusive")
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'amount_paid': 9, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()), 'cart_discount_amount': 5.0, 'discount_reason': 'x'})
    assert r.status_code == 400, r.get_json()
    assert 'before tax' not in r.get_json()['message'], r.get_json()
    _set_mode(client, "after_discount")
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}], 'amount_paid': 9, 'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()), 'cart_discount_amount': 5.0, 'discount_reason': 'x'})
    assert 'before tax' in r.get_json()['message'], r.get_json()


def test_demo_seeder_totals_reconcile_in_inclusive_mode():
    """The demo seeder summed subtotal - discount + tax for the header total, which is false in
    inclusive mode. Seed a company already set to inclusive and check every seeded sale."""
    client, cid, pid = _make_shop()
    conn = get_retail_conn()
    conn.execute("INSERT OR REPLACE INTO retail_settings (company_id, skey, svalue) VALUES (?, 'tax_calculation_mode', 'inclusive')", (cid,))
    conn.commit()
    from database import schema as _schema
    seed = getattr(_schema, '_seed_retail', None)
    assert seed is not None
    seed(conn, conn.cursor(), cid)
    conn.commit()
    rows = conn.execute("SELECT subtotal, discount_amount, tax_amount, total FROM sales WHERE company_id=?", (cid,)).fetchall()
    conn.close()
    assert len(rows) > 5
    for sub_, dis, tax, tot in rows:
        assert D(str(sub_)) - D(str(dis)) == D(str(tot)), (sub_, dis, tax, tot)
