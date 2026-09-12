import xml.etree.ElementTree as ET
from decimal import Decimal

from commercial_runtime.einvoicing.document import BuyerId, EInvoiceDocument, EInvoiceLine
from commercial_runtime.einvoicing.ubl import PROFILE_ID, document_sha256, to_ubl_xml

_NS = {
    '': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}


def _sample_document(**overrides):
    defaults = dict(
        company_id=1,
        einvoice_no='INC-000001',
        local_document_no='SALE-000042',
        invoice_family='income',
        payment_type='cash',
        currency='JOD',
        issue_datetime='2026-08-04T12:30:00',
        seller_name='Aura Test Shop',
        seller_tin='123456789',
        buyer_name='Walk-in Customer',
        buyer_id=BuyerId('TIN', '987654321'),
        lines=(
            EInvoiceLine(description='Widget', quantity=2, unit_price=10.0,
                         discount_amount=0.0, tax_amount=3.0, line_total=20.0),
        ),
        subtotal=20.0, discount_total=0.0, tax_total=3.0, grand_total=23.0,
    )
    defaults.update(overrides)
    return EInvoiceDocument(**defaults)


def test_output_is_well_formed_xml():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)  # raises on malformed XML
    assert root is not None


def test_correct_namespaces_present():
    xml = to_ubl_xml(_sample_document())
    assert 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2' in xml
    assert 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2' in xml
    assert 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2' in xml


def test_profile_id_is_generic_not_istd_specific():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)
    profile_id = root.find('cbc:ProfileID', _NS)
    assert profile_id is not None
    assert profile_id.text == PROFILE_ID == 'GENERIC_UBL_2_1'


def test_byte_identical_output_for_identical_input():
    doc = _sample_document()
    assert to_ubl_xml(doc) == to_ubl_xml(doc)
    # A structurally-identical-but-separately-constructed document must also
    # match -- proves determinism is not an artifact of object identity.
    doc2 = _sample_document()
    assert to_ubl_xml(doc) == to_ubl_xml(doc2)


def test_document_sha256_is_stable_for_identical_input():
    doc = _sample_document()
    assert document_sha256(to_ubl_xml(doc)) == document_sha256(to_ubl_xml(doc))


def test_document_sha256_changes_when_document_changes():
    doc_a = _sample_document(grand_total=23.0)
    doc_b = _sample_document(grand_total=99.0)
    assert document_sha256(to_ubl_xml(doc_a)) != document_sha256(to_ubl_xml(doc_b))


def test_einvoice_number_and_amounts_present_in_output():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)
    assert root.find('cbc:ID', _NS).text == 'INC-000001'
    payable = root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', _NS)
    # CHANGED from '23.00' when _money() became currency-aware. WHAT THIS
    # ASSERTION CAN NO LONGER CATCH: a renderer that ignores the currency and
    # emits two decimals for everything. Such a renderer would produce
    # '23.00' here and FAIL rather than pass -- so this line no longer pins
    # the 2-decimal default at all, in either direction. The two tests below
    # exist to replace exactly that coverage: one pins the 2dp ALLOW-half on
    # a USD document (which a JOD-only 3dp renderer would break), and one
    # pins the DENY-half with a JOD value carrying real fils, which a 2dp
    # renderer is arithmetically incapable of producing.
    assert payable.text == '23.000'
    assert payable.get('currencyID') == 'JOD'


def test_a_two_decimal_currency_still_renders_two_decimals():
    """The allow-half. Making JOD correct must not make USD wrong -- a fix
    that simply hardcoded 3 decimals instead of 2 passes every JOD assertion
    in this file and is just as broken for the currencies that have 2."""
    xml = to_ubl_xml(_sample_document(currency='USD'))
    root = ET.fromstring(xml)
    payable = root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', _NS)
    assert payable.text == '23.00'
    assert payable.get('currencyID') == 'USD'


def test_jod_fils_survive_and_the_document_reconciles_against_itself():
    """The deny-half, pinned by values a 2-decimal renderer cannot produce.

    Measured against the old renderer, this exact document came out as
    LineExtensionAmount 12.35, TaxAmount 1.98, PayableAmount 14.32 -- so the
    document contradicted itself (12.35 + 1.98 = 14.33), and document_sha256
    was a stable fingerprint of a wrong document. Jordan's minor unit is the
    fils, 1/1000 of a dinar; 12.345 JOD is a real, ordinary price.
    """
    doc = _sample_document(
        currency='JOD',
        lines=(EInvoiceLine(description='Consultation', quantity=1, unit_price=12.345,
                            discount_amount=0.0, tax_amount=1.975, line_total=12.345),),
        subtotal=12.345, discount_total=0.0, tax_total=1.975, grand_total=14.320,
    )
    root = ET.fromstring(to_ubl_xml(doc))

    line_ext = root.find('cac:LegalMonetaryTotal/cbc:LineExtensionAmount', _NS).text
    tax = root.find('cac:TaxTotal/cbc:TaxAmount', _NS).text
    tax_excl = root.find('cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount', _NS).text
    payable = root.find('cac:LegalMonetaryTotal/cbc:PayableAmount', _NS).text
    price = root.find('cac:InvoiceLine/cac:Price/cbc:PriceAmount', _NS).text

    assert (line_ext, tax, tax_excl, payable, price) == (
        '12.345', '1.975', '12.345', '14.320', '12.345')
    # The self-reconciliation the old renderer failed: exclusive + tax must
    # equal payable, read back out of the document as the tax authority
    # would read it.
    assert Decimal(tax_excl) + Decimal(tax) == Decimal(payable)


def test_local_document_no_and_family_carried_in_note_not_fabricated_istd_field():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)
    note = root.find('cbc:Note', _NS).text
    assert 'local_document_no=SALE-000042' in note
    assert 'invoice_family=income' in note
    assert 'payment_type=cash' in note


def test_buyer_id_scheme_and_value_present():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)
    buyer_id_el = root.find('cac:AccountingCustomerParty/cac:Party/cac:PartyIdentification/cbc:ID', _NS)
    assert buyer_id_el.text == '987654321'
    assert buyer_id_el.get('schemeID') == 'TIN'


def test_line_items_serialized():
    xml = to_ubl_xml(_sample_document())
    root = ET.fromstring(xml)
    lines = root.findall('cac:InvoiceLine', _NS)
    assert len(lines) == 1
    assert lines[0].find('cac:Item/cbc:Name', _NS).text == 'Widget'


def test_special_characters_are_escaped_not_broken():
    doc = _sample_document(seller_name='Aura & Sons <Ltd>', buyer_name='O\'Brien "Traders"')
    xml = to_ubl_xml(doc)
    root = ET.fromstring(xml)  # would raise if unescaped & / < broke the XML
    seller_name = root.find('cac:AccountingSupplierParty/cac:Party/cac:PartyName/cbc:Name', _NS)
    assert seller_name.text == 'Aura & Sons <Ltd>'


def test_no_credential_like_substring_in_output():
    xml = to_ubl_xml(_sample_document())
    lowered = xml.lower()
    for forbidden in ('client_secret', 'client_id', 'password', 'api_key'):
        assert forbidden not in lowered
