"""JoFotara e-invoicing -- generic OASIS UBL 2.1 Invoice XML serializer.

PROFILE_ID = "GENERIC_UBL_2_1": every element name used below is a real,
publicly documented UBL 2.1 element (ProfileID, ID, IssueDate, IssueTime,
InvoiceTypeCode, DocumentCurrencyCode, Note, AccountingSupplierParty,
AccountingCustomerParty, PartyTaxScheme, PartyName, PartyIdentification,
LegalMonetaryTotal, TaxTotal, InvoiceLine, etc. -- see the OASIS UBL 2.1
Invoice schema). This is deliberately NOT the ISTD-specific profile: JoFotara's
exact required subset, cardinalities, code lists, and JSON encryption
envelope are not publicly available outside the taxpayer portal (see
docs/einvoicing/phase1/invoice-numbering-audit.md and
docs/einvoicing/phase2/phase2-seam.md) -- nothing here guesses at them.
Phase 2 adds an ISTD-profile branch (PROFILE_ID="ISTD_JO_1_0") once real
integration docs exist; this generic profile stays as the tested default and
is what MockProvider round-trips against today.

Output is fully deterministic: the same EInvoiceDocument always serializes
to byte-identical XML, so document_sha256 (stored alongside document_xml in
einvoice_outbox) is a stable, reproducible fingerprint of exactly what was
submitted -- the compliance evidence this feature exists to produce.
"""
from __future__ import annotations

import hashlib
from xml.sax.saxutils import escape, quoteattr

from ..currency import format_money_plain
from .document import EInvoiceDocument

PROFILE_ID = "GENERIC_UBL_2_1"

# UBL/UNCL1001 standard type code -- 380 = Commercial invoice. Deliberately
# the one and only value used in Phase 1: inventing per-invoice-family codes
# here would be exactly the kind of ISTD-specific guess this module exists
# to avoid. invoice_family/payment_type are carried as plain, human-readable
# annotations in cbc:Note instead (see _build_note below).
_INVOICE_TYPE_CODE = "380"


def _money(value: float, currency: str) -> str:
    """Render one amount at the DOCUMENT'S OWN currency's minor unit.

    `currency` is mandatory, not defaulted: this was `f"{value:.2f}"` and
    every amount in the document -- TaxAmount, LineExtensionAmount,
    TaxExclusiveAmount, TaxInclusiveAmount, PayableAmount, PriceAmount --
    was coarsened to two decimals while <cbc:DocumentCurrencyCode> right
    above them said JOD, which has three (see
    commercial_runtime/currency.py's measurements). Measured on a real JOD
    document carrying subtotal 12.345 / tax 1.975 / total 14.320, the old
    renderer emitted 12.35 + 1.98 = 14.33 against a PayableAmount of 14.32:
    a document that does not reconcile against ITSELF, fingerprinted by
    document_sha256 as the compliance evidence of what was filed.

    A default would have quietly reintroduced exactly that bug the next time
    someone added an amount element and forgot to pass the currency through,
    so there isn't one -- every call site already has `currency` in scope.
    """
    return format_money_plain(value, currency)


def _build_note(document: EInvoiceDocument) -> str:
    return (
        f"local_document_no={document.local_document_no}; "
        f"invoice_family={document.invoice_family}; "
        f"payment_type={document.payment_type}"
    )


def _party_xml(role_tag: str, name: str, tin: str = None, buyer_id=None) -> str:
    inner = []
    if buyer_id is not None:
        inner.append(
            f'<cac:PartyIdentification><cbc:ID schemeID={quoteattr(buyer_id.scheme)}>'
            f'{escape(buyer_id.value)}</cbc:ID></cac:PartyIdentification>'
        )
    if tin is not None:
        inner.append(f'<cac:PartyTaxScheme><cbc:CompanyID>{escape(tin)}</cbc:CompanyID></cac:PartyTaxScheme>')
    inner.append(f'<cac:PartyName><cbc:Name>{escape(name)}</cbc:Name></cac:PartyName>')
    return f'<{role_tag}><cac:Party>{"".join(inner)}</cac:Party></{role_tag}>'


def _line_xml(index: int, line, currency: str) -> str:
    return (
        f'<cac:InvoiceLine>'
        f'<cbc:ID>{index}</cbc:ID>'
        f'<cbc:InvoicedQuantity>{line.quantity}</cbc:InvoicedQuantity>'
        f'<cbc:LineExtensionAmount currencyID={quoteattr(currency)}>{_money(line.line_total, currency)}</cbc:LineExtensionAmount>'
        f'<cac:Item><cbc:Name>{escape(line.description)}</cbc:Name></cac:Item>'
        f'<cac:Price><cbc:PriceAmount currencyID={quoteattr(currency)}>{_money(line.unit_price, currency)}</cbc:PriceAmount></cac:Price>'
        f'</cac:InvoiceLine>'
    )


def to_ubl_xml(document: EInvoiceDocument) -> str:
    issue_date, _, issue_time = document.issue_datetime.partition('T')
    currency = document.currency

    lines_xml = ''.join(_line_xml(i + 1, line, currency) for i, line in enumerate(document.lines))
    taxable_amount = document.subtotal - document.discount_total

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">',
        f'<cbc:ProfileID>{PROFILE_ID}</cbc:ProfileID>',
        f'<cbc:ID>{escape(document.einvoice_no)}</cbc:ID>',
        f'<cbc:IssueDate>{escape(issue_date)}</cbc:IssueDate>',
        f'<cbc:IssueTime>{escape(issue_time)}</cbc:IssueTime>',
        f'<cbc:InvoiceTypeCode>{_INVOICE_TYPE_CODE}</cbc:InvoiceTypeCode>',
        f'<cbc:Note>{escape(_build_note(document))}</cbc:Note>',
        f'<cbc:DocumentCurrencyCode>{escape(currency)}</cbc:DocumentCurrencyCode>',
        _party_xml('cac:AccountingSupplierParty', document.seller_name, tin=document.seller_tin),
        _party_xml('cac:AccountingCustomerParty', document.buyer_name, buyer_id=document.buyer_id),
        '<cac:TaxTotal>'
        f'<cbc:TaxAmount currencyID={quoteattr(currency)}>{_money(document.tax_total, currency)}</cbc:TaxAmount>'
        '</cac:TaxTotal>',
        '<cac:LegalMonetaryTotal>'
        f'<cbc:LineExtensionAmount currencyID={quoteattr(currency)}>{_money(document.subtotal, currency)}</cbc:LineExtensionAmount>'
        f'<cbc:AllowanceTotalAmount currencyID={quoteattr(currency)}>{_money(document.discount_total, currency)}</cbc:AllowanceTotalAmount>'
        f'<cbc:TaxExclusiveAmount currencyID={quoteattr(currency)}>{_money(taxable_amount, currency)}</cbc:TaxExclusiveAmount>'
        f'<cbc:TaxInclusiveAmount currencyID={quoteattr(currency)}>{_money(document.grand_total, currency)}</cbc:TaxInclusiveAmount>'
        f'<cbc:PayableAmount currencyID={quoteattr(currency)}>{_money(document.grand_total, currency)}</cbc:PayableAmount>'
        '</cac:LegalMonetaryTotal>',
        lines_xml,
        '</Invoice>',
    ]
    return ''.join(parts)


def document_sha256(xml: str) -> str:
    return hashlib.sha256(xml.encode('utf-8')).hexdigest()
