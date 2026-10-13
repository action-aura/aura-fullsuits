"""Golden tests for the ISTD (JoFotara) UBL 2.1 document profile.

Rules asserted (ids match commercial_runtime/einvoicing/istd_ubl.py). Sources
are the PUBLIC documents catalogued, with fetch dates, in
docs/einvoicing/istd-profile.md -- chiefly Qoyod's walk-through of the ISTD
JoFotara technical guide v1.5:
  https://www.qoyod.com/en/learn/jofotara-cash-invoice-xml-example/
  https://www.qoyod.com/en/learn/jofotara-income-return-xml-example/
  https://www.qoyod.com/en/learn/jofotara-tax-categories/
  https://www.qoyod.com/en/learn/jofotara-invoice-header/
  https://www.qoyod.com/en/learn/jofotara-general-sales-tax-invoice/

  R-TYPE   InvoiceTypeCode 388 = invoice / 381 = return; @name = trade type +
           payment (1 cash / 2 receivable) + tax family; a return repeats the
           original's name.
  R-UUID   cbc:UUID present, valid, stable across retries.
  R-ICV    AdditionalDocumentReference ID='ICV', counter in cbc:UUID; here it
           is the dedicated einvoice_sequence number, NOT the local sale no.
  R-SELLER SellerSupplierParty/cbc:ID = activity sequence.
  R-BUYER  scheme TN|NIN|PN; name mandatory on receivable.
  R-LINE   S for rate>0, O for zero-rated, Z for exempt; rate in accepted list.
  R-TOTAL  header = sums of 3dp-rounded lines; AllowanceTotal = sum of line
           discounts; tolerance 0.001.
  R-RETURN 381 has BillingReference(original no + UUID), DocumentDescription =
           ORIGINAL total, PaymentMeans code 10 + InstructionNote reason.
  R-MIN    opening <Invoice> tag on one line.

Each "mutation" test builds a document that breaks exactly one rule and must
be refused (RED for the broken document), proving the guard fires.
"""
import dataclasses
import xml.etree.ElementTree as ET

import pytest

from commercial_runtime.einvoicing.document import (
    BuyerId, EInvoiceDocument, EInvoiceLine, document_uuid, icv_from_einvoice_no, istd_type_name,
    tax_category_for_rate,
)
from commercial_runtime.einvoicing.istd_ubl import (
    PROFILE_ID_ISTD, IstdProfileError, to_istd_xml, validate_istd_document,
)
from commercial_runtime.einvoicing.ubl import render_document_xml

NS = {
    'i': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}
ORIGINAL_UUID = document_uuid(1, 'AURA_RETAIL:sale:7')


def _line(desc, qty, price, disc_pct, rate, **kw):
    """Tax-after-discount line, rounded the way pricing.calculate_line does."""
    from decimal import Decimal, ROUND_HALF_UP
    q = Decimal('0.001')
    gross = (Decimal(str(qty)) * Decimal(str(price))).quantize(q, ROUND_HALF_UP)
    disc = (gross * Decimal(str(disc_pct)) / 100).quantize(q, ROUND_HALF_UP)
    taxable = gross - disc
    tax = (taxable * Decimal(str(rate)) / 100).quantize(q, ROUND_HALF_UP)
    return EInvoiceLine(desc, qty, price, float(disc), float(tax), float(taxable),
                        tax_rate=rate, tax_category=tax_category_for_rate(rate, **kw))


def _doc(lines, **over):
    total = sum(l.line_total + l.tax_amount for l in lines)
    base = dict(
        company_id=1, einvoice_no='GS-000003', local_document_no='SALE-000777',  # local no != ICV
        invoice_family='general_sales', payment_type='cash', currency='JOD',
        issue_datetime='2026-10-13T10:30:00', seller_name='Aura Test Shop', seller_tin='123456789',
        buyer_name='Walk-in Customer', buyer_id=None, lines=tuple(lines),
        subtotal=sum(l.quantity * l.unit_price for l in lines),
        discount_total=sum(l.discount_amount for l in lines),
        tax_total=sum(l.tax_amount for l in lines), grand_total=round(total, 3),
        profile=PROFILE_ID_ISTD, uuid=document_uuid(1, 'AURA_RETAIL:sale:7'), icv=3,
        type_name='012', activity_code='5390954',
    )
    base.update(over)
    return EInvoiceDocument(**base)


def _parse(doc):
    return ET.fromstring(to_istd_xml(doc))


def _t(root, path):
    el = root.find(path, NS)
    assert el is not None, path
    return el


def _cash_b2c():
    return _doc([_line('Widget', 2, 10.0, 0, 16)])


def _receivable_b2b():
    return _doc([_line('Chair', 3, 33.333, 10, 16)], payment_type='credit', type_name='022',
                buyer_name='Acme Trading', buyer_id=BuyerId('TIN', '987654321'),
                einvoice_no='GS-000004', uuid=document_uuid(1, 'AURA_RETAIL:sale:8'), icv=4)


def _mixed():
    return _doc([_line('Widget', 1, 10.0, 0, 16), _line('Bread', 4, 0.5, 0, 0)])


def _credit_note():
    # Partial return: 1 of the 2 widgets from the cash sale GS-000003.
    return _doc([_line('Widget', 1, 10.0, 0, 16)], document_kind='credit_note',
                einvoice_no='GS-000005', uuid=document_uuid(1, 'AURA_RETAIL:return:2'), icv=5,
                type_name='012', original_einvoice_no='GS-000003', original_uuid=ORIGINAL_UUID,
                original_total=23.2, return_reason='defective')


# ---- golden documents -------------------------------------------------------

def test_cash_b2c_invoice_golden():
    xml = to_istd_xml(_cash_b2c())
    assert xml.split('>', 2)[1].startswith('<Invoice') and '\n' not in xml          # R-MIN
    root = ET.fromstring(xml)
    assert _t(root, 'cbc:ProfileID').text == 'reporting:1.0'
    tc = _t(root, 'cbc:InvoiceTypeCode')
    assert (tc.text, tc.get('name')) == ('388', '012')                               # R-TYPE
    assert _t(root, 'cbc:UUID').text == ORIGINAL_UUID                                # R-UUID
    assert _t(root, 'cbc:IssueDate').text == '2026-10-13'
    icv = _t(root, 'cac:AdditionalDocumentReference')
    assert (icv.find('cbc:ID', NS).text, icv.find('cbc:UUID', NS).text) == ('ICV', '3')   # R-ICV
    assert _t(root, 'cac:SellerSupplierParty/cac:Party/cac:PartyIdentification/cbc:ID').text == '5390954'
    assert _t(root, 'cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID').text == '123456789'
    assert root.find('cac:BillingReference', NS) is None and root.find('cac:PaymentMeans', NS) is None
    # R-BUYER: scheme is mandatory, anonymous buyer has no number
    bid = _t(root, 'cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID')
    assert bid.get('schemeID') == 'PN' and not bid.text
    # R-LINE + R-TOTAL
    cat = _t(root, 'cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory')
    assert (cat.find('cbc:ID', NS).text, cat.find('cbc:Percent', NS).text) == ('S', '16')
    assert cat.find('cbc:ID', NS).get('schemeID') == 'UN/ECE 5305'
    assert cat.find('cac:TaxScheme/cbc:ID', NS).text == 'VAT'
    lmt = _t(root, 'cac:LegalMonetaryTotal')
    assert lmt.find('cbc:TaxExclusiveAmount', NS).text == '20.000'
    assert lmt.find('cbc:TaxInclusiveAmount', NS).text == '23.200'
    assert lmt.find('cbc:PayableAmount', NS).text == '23.200'
    assert _t(root, 'cac:TaxTotal/cbc:TaxAmount').text == '3.200'
    assert _t(root, 'cac:InvoiceLine/cac:TaxTotal/cbc:RoundingAmount').text == '23.200'


def test_receivable_b2b_invoice_golden():
    root = _parse(_receivable_b2b())
    tc = _t(root, 'cbc:InvoiceTypeCode')
    assert (tc.text, tc.get('name')) == ('388', '022')                               # receivable GST
    buyer = _t(root, 'cac:AccountingCustomerParty/cac:Party')
    ident = buyer.find('cac:PartyIdentification/cbc:ID', NS)
    assert (ident.get('schemeID'), ident.text) == ('TN', '987654321')                # our TIN == ISTD TN
    assert buyer.find('cac:PartyLegalEntity/cbc:RegistrationName', NS).text == 'Acme Trading'
    assert _t(root, 'cbc:UUID').text == document_uuid(1, 'AURA_RETAIL:sale:8')
    # 3 x 33.333 = 99.999; 10% discount 10.000; taxable 89.999; tax 14.400; incl 104.399
    lmt = _t(root, 'cac:LegalMonetaryTotal')
    assert lmt.find('cbc:TaxExclusiveAmount', NS).text == '99.999'
    assert lmt.find('cbc:AllowanceTotalAmount', NS).text == '10.000'
    assert lmt.find('cbc:PayableAmount', NS).text == '104.399'
    assert _t(root, 'cac:AllowanceCharge/cbc:Amount').text == '10.000'


def test_mixed_16_and_zero_rated_lines_golden():
    root = _parse(_mixed())
    cats = [(c.find('cbc:ID', NS).text, c.find('cbc:Percent', NS).text)
            for c in root.iterfind('cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory', NS)]
    assert cats == [('S', '16'), ('O', '0')]                                          # R-LINE
    lines = root.findall('cac:InvoiceLine', NS)
    assert [l.find('cbc:ID', NS).text for l in lines] == ['1', '2']
    assert _t(root, 'cac:TaxTotal/cbc:TaxAmount').text == '1.600'                     # only the 16% line taxes
    assert _t(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount').text == '13.600'


def test_credit_note_for_partial_return_golden():
    root = _parse(_credit_note())
    tc = _t(root, 'cbc:InvoiceTypeCode')
    assert (tc.text, tc.get('name')) == ('381', '012')                                # name == original's
    ref = _t(root, 'cac:BillingReference/cac:InvoiceDocumentReference')
    assert ref.find('cbc:ID', NS).text == 'GS-000003'                                 # R-RETURN
    assert ref.find('cbc:UUID', NS).text == ORIGINAL_UUID
    assert ref.find('cbc:DocumentDescription', NS).text == '23.200'                   # ORIGINAL total, not the return's
    assert _t(root, 'cbc:ID').text == 'GS-000005' and _t(root, 'cbc:UUID').text != ORIGINAL_UUID
    pm = _t(root, 'cac:PaymentMeans')
    assert (pm.find('cbc:PaymentMeansCode', NS).text, pm.find('cbc:PaymentMeansCode', NS).get('listID')) == ('10', 'UN/ECE 4461')
    assert pm.find('cbc:InstructionNote', NS).text == 'defective'
    assert root.find('cbc:Note', NS) is None                                          # GST return has no Note
    assert _t(root, 'cac:LegalMonetaryTotal/cbc:PayableAmount').text == '11.600'      # only the returned widget


def test_output_is_deterministic_and_dispatch_uses_profile():
    d = _credit_note()
    assert to_istd_xml(d) == to_istd_xml(d)
    assert render_document_xml(d) == to_istd_xml(d)
    with pytest.raises(ValueError):                                                    # no generic credit note
        render_document_xml(dataclasses.replace(d, profile='GENERIC_UBL_2_1'))


def test_income_family_has_no_tax_total():
    d = _doc([_line('Service', 1, 50.0, 0, 0)], invoice_family='income', type_name='011')
    root = _parse(d)
    assert root.find('cac:TaxTotal', NS) is None
    assert root.find('cac:InvoiceLine/cac:TaxTotal', NS) is None                      # R-INCOME


def test_uuid_is_stable_per_ref_and_icv_comes_from_the_dedicated_number():
    assert document_uuid(1, 'AURA_RETAIL:sale:7') == document_uuid(1, 'AURA_RETAIL:sale:7')
    assert document_uuid(1, 'AURA_RETAIL:sale:7') != document_uuid(2, 'AURA_RETAIL:sale:7')
    assert icv_from_einvoice_no('INC-000042') == 42
    assert istd_type_name('cash', 'income') == '011' and istd_type_name('credit', 'general_sales') == '022'


# ---- mutation proofs: each breaks exactly one rule and must be refused -------

def _refused(doc, rule):
    errs = validate_istd_document(doc)
    assert any(e.startswith(rule) for e in errs), errs
    with pytest.raises(IstdProfileError):
        to_istd_xml(doc)


def test_mutation_wrong_type_name_for_receivable():
    _refused(dataclasses.replace(_receivable_b2b(), type_name='012'), 'R-TYPE')       # cash digit on a credit sale


def test_mutation_wrong_family_digit():
    _refused(dataclasses.replace(_cash_b2c(), type_name='011'), 'R-TYPE')


def test_mutation_standard_category_on_zero_rate():
    bad = dataclasses.replace(_mixed().lines[1], tax_category='S')
    _refused(dataclasses.replace(_mixed(), lines=(_mixed().lines[0], bad)), 'R-LINE')


def test_mutation_zero_category_on_taxed_line():
    bad = dataclasses.replace(_cash_b2c().lines[0], tax_category='O')
    _refused(dataclasses.replace(_cash_b2c(), lines=(bad,)), 'R-LINE')


def test_mutation_unaccepted_rate():
    bad = dataclasses.replace(_cash_b2c().lines[0], tax_rate=15.0)
    _refused(dataclasses.replace(_cash_b2c(), lines=(bad,)), 'R-LINE')


def test_mutation_icv_missing_or_zero():
    _refused(dataclasses.replace(_cash_b2c(), icv=0), 'R-ICV')


def test_mutation_credit_note_without_original_reference():
    _refused(dataclasses.replace(_credit_note(), original_einvoice_no=''), 'R-RETURN')
    _refused(dataclasses.replace(_credit_note(), original_uuid=''), 'R-RETURN')
    _refused(dataclasses.replace(_credit_note(), return_reason=' '), 'R-RETURN')
    _refused(dataclasses.replace(_credit_note(), original_total=0.0), 'R-RETURN')


def test_mutation_missing_uuid_activity_and_seller():
    _refused(dataclasses.replace(_cash_b2c(), uuid=''), 'R-UUID')
    _refused(dataclasses.replace(_cash_b2c(), activity_code=''), 'R-SELLER')
    _refused(dataclasses.replace(_cash_b2c(), seller_tin=''), 'R-SELLER')


def test_mutation_receivable_without_buyer_name():
    _refused(dataclasses.replace(_receivable_b2b(), buyer_name=''), 'R-BUYER')


def test_cash_over_10000_jod_needs_buyer_name_but_small_cash_does_not():
    big = _doc([_line('Machine', 1, 12000.0, 0, 16)], buyer_name='')
    _refused(big, 'R-BUYER')
    assert validate_istd_document(dataclasses.replace(_cash_b2c(), buyer_name='')) == []


def test_mutation_totals_that_do_not_reconcile():
    _refused(dataclasses.replace(_cash_b2c(), grand_total=23.5), 'R-TOTAL')
    ok = dataclasses.replace(_cash_b2c(), grand_total=23.2004)                          # inside 0.001
    assert validate_istd_document(ok) == []


def test_mutation_negative_quantity():
    bad = dataclasses.replace(_cash_b2c().lines[0], quantity=-1)
    _refused(dataclasses.replace(_cash_b2c(), lines=(bad,)), 'R-LINE')


def test_exempt_category_is_z_not_o():
    assert tax_category_for_rate(0, exempt=True) == 'Z' and tax_category_for_rate(0) == 'O'
    assert tax_category_for_rate(16) == 'S'
    d = _doc([_line('Exempt thing', 1, 5.0, 0, 0, exempt=True)])
    cat = _t(_parse(d), 'cac:InvoiceLine/cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID')
    assert cat.text == 'Z'


def test_mutation_income_family_with_tax_is_refused():
    _refused(dataclasses.replace(_cash_b2c(), invoice_family='income', type_name='011'), 'R-INCOME')
