import xml.etree.ElementTree as ET

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
    assert payable.text == '23.00'
    assert payable.get('currencyID') == 'JOD'


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
