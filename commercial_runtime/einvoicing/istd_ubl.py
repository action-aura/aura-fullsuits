"""JoFotara e-invoicing -- the ISTD (Jordan) UBL 2.1 document profile.

PROFILE_ID_ISTD = "ISTD_JO_1_0" is OUR name for this profile. The value that
goes on the wire in cbc:ProfileID is "reporting:1.0" (WIRE_PROFILE_ID), which
is what ISTD's technical guide v1.5 fixes. Every rule below is taken from the
PUBLIC documentation listed, with fetch dates, in docs/einvoicing/istd-profile.md
(which also lists the open questions where public sources are silent or
contradict each other -- those are NOT guessed at here, they are flagged).

Nothing in this module talks to ISTD. Transport (providers/direct_istd.py)
still raises NotImplementedError until the owner has real credentials; this
module only turns an EInvoiceDocument into XML and refuses (validate_istd_
document) to build XML for a document that breaks a documented rule.

Rules enforced, with their short ids (tests cite the same ids):

  R-TYPE   388 = invoice, 381 = return. InvoiceTypeCode@name is 3 digits:
           trade type, payment (1 cash / 2 receivable), tax family (1 income /
           2 general sales). A return carries the ORIGINAL's name.
  R-UUID   cbc:UUID is mandatory, valid, generated once and reused on retry.
  R-ICV    AdditionalDocumentReference ID='ICV', counter in cbc:UUID, from 1.
  R-SELLER SellerSupplierParty/cbc:ID = income-source (activity) sequence;
           seller tax number + name are mandatory.
  R-BUYER  buyer id scheme TN|NIN|PN; buyer NAME mandatory on a receivable
           invoice and on cash above 10,000 JOD.
  R-LINE   per line: category S (rate>0) / Z (exempt, 0) / O (zero-rated, 0);
           rate must be one of the accepted values; no negative quantity/price.
  R-TOTAL  header totals are SUMS of the (3dp-rounded) lines; header discount
           = sum of line discounts; no invoice-level discount; tolerance 0.001.
  R-INCOME the income family (name digit 3 = '1') has NO TaxTotal.
  R-RETURN 381 needs BillingReference(original number + UUID), the ORIGINAL's
           total in DocumentDescription, and the reason in PaymentMeans/
           InstructionNote (PaymentMeansCode 10, listID UN/ECE 4461).
  R-MIN    the opening <Invoice ...> tag is on ONE line (ISTD rejects
           non-minified invoices: "Invalid Invoice Minification").
"""
from __future__ import annotations

import re
import uuid as uuid_module
from decimal import Decimal
from xml.sax.saxutils import escape, quoteattr

from ..currency import currency_quantum, format_money_plain, quantize_money
from .document import (
    ISTD_ACCEPTED_RATES, TAX_CATEGORY_EXEMPT, TAX_CATEGORY_STANDARD, TAX_CATEGORY_ZERO_RATED,
    EInvoiceDocument,
)

PROFILE_ID_ISTD = "ISTD_JO_1_0"
WIRE_PROFILE_ID = "reporting:1.0"

TYPE_CODE_INVOICE = "388"
TYPE_CODE_CREDIT_NOTE = "381"

#: Cash invoices above this (JOD, or its foreign-currency equivalent) need the
#: buyer's name. Only enforced for JOD -- the foreign-currency equivalence rule
#: is an open question (docs/einvoicing/istd-profile.md).
BUYER_NAME_CASH_THRESHOLD_JOD = Decimal('10000')

#: Header/line reconciliation tolerance from the guide ("difference of no more
#: than 0.001").
TOTAL_TOLERANCE = Decimal('0.001')

_SCHEME_FOR_BUYER_ID = {'TIN': 'TN', 'NIN': 'NIN', 'PN': 'PN'}  # our TIN == ISTD "TN"
_CATEGORY_RULES = {
    TAX_CATEGORY_STANDARD: 'rate > 0',
    TAX_CATEGORY_EXEMPT: 'rate == 0 (exempt)',
    TAX_CATEGORY_ZERO_RATED: 'rate == 0 (zero-rated)',
}
_TYPE_NAME_RE = re.compile(r'^[0-9]{3}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}')


class IstdProfileError(ValueError):
    """The document breaks a documented ISTD rule; carries every violation."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _line_amounts(line, currency):
    """(gross, discount, extension, tax, rounding) for one line, each rounded
    ONCE to the currency's minor unit; extension and rounding are exact sums
    of the already-rounded parts so a line can never disagree with itself."""
    gross = quantize_money(_d(line.quantity) * _d(line.unit_price), currency)
    discount = quantize_money(line.discount_amount, currency)
    tax = quantize_money(line.tax_amount, currency)
    extension = gross - discount
    return gross, discount, extension, tax, extension + tax


def _totals(document):
    gross = discount = tax = rounding = Decimal(0)
    for line in document.lines:
        g, d, _e, t, r = _line_amounts(line, document.currency)
        gross += g
        discount += d
        tax += t
        rounding += r
    return gross, discount, tax, rounding


def validate_istd_document(document: EInvoiceDocument) -> list:
    """Every violated rule as a readable string; [] means the document is
    buildable. Pure -- no I/O."""
    errs = []
    credit = document.document_kind == 'credit_note'
    if document.document_kind not in ('invoice', 'credit_note'):
        errs.append(f"R-TYPE: document_kind {document.document_kind!r} is neither 'invoice' nor 'credit_note'")

    name = document.type_name
    if not _TYPE_NAME_RE.match(name or ''):
        errs.append(f"R-TYPE: InvoiceTypeCode@name {name!r} is not 3 digits")
    else:
        want_pay = '2' if document.payment_type == 'credit' else '1'
        if name[1] != want_pay:
            errs.append(f"R-TYPE: name {name} payment digit disagrees with payment_type {document.payment_type!r}")
        want_family = {'income': '1', 'general_sales': '2'}.get(document.invoice_family)
        if want_family is None or name[2] != want_family:
            errs.append(f"R-TYPE: name {name} tax-family digit disagrees with invoice_family {document.invoice_family!r}")

    try:
        uuid_module.UUID(document.uuid)
    except (ValueError, AttributeError, TypeError):
        errs.append("R-UUID: cbc:UUID is missing or not a valid UUID")
    if not isinstance(document.icv, int) or document.icv < 1:
        errs.append("R-ICV: invoice counter value must be an integer >= 1")

    if not (document.seller_tin or '').strip():
        errs.append("R-SELLER: seller tax number is empty")
    elif not document.seller_tin.strip().isdigit():
        errs.append("R-SELLER: seller tax number must be digits only")
    if not (document.seller_name or '').strip():
        errs.append("R-SELLER: seller (registered) name is empty")
    if not (document.activity_code or '').strip():
        errs.append("R-SELLER: income-source sequence (activity code) is empty")

    if not _DATE_RE.match(document.issue_datetime or ''):
        errs.append("R-HEADER: issue date is not yyyy-mm-dd")

    gross, _discount, _tax, rounding = _totals(document)
    if document.buyer_id is not None and not (document.buyer_id.value or '').isdigit():
        errs.append("R-BUYER: buyer identifier must be digits only")
    needs_name = document.payment_type == 'credit' or (
        (document.currency or '').upper() == 'JOD' and rounding > BUYER_NAME_CASH_THRESHOLD_JOD)
    if needs_name and not (document.buyer_name or '').strip():
        errs.append("R-BUYER: buyer name is required on a receivable invoice and on cash above 10,000 JOD")

    if not document.lines:
        errs.append("R-LINE: a document needs at least one line")
    for i, line in enumerate(document.lines, 1):
        cat, rate = line.tax_category, _d(line.tax_rate)
        if cat not in _CATEGORY_RULES:
            errs.append(f"R-LINE {i}: tax category {cat!r} is not S, Z or O")
        elif (cat == TAX_CATEGORY_STANDARD) != (rate > 0):
            errs.append(f"R-LINE {i}: category {cat} requires {_CATEGORY_RULES[cat]}, got rate {rate}")
        if rate not in [Decimal(r) for r in ISTD_ACCEPTED_RATES]:
            errs.append(f"R-LINE {i}: rate {rate} is not an ISTD-accepted value {ISTD_ACCEPTED_RATES}")
        if _d(line.quantity) <= 0 or _d(line.unit_price) < 0 or _d(line.discount_amount) < 0:
            errs.append(f"R-LINE {i}: negative/zero quantity, negative price or negative discount is rejected by ISTD")

    if document.invoice_family == 'income' and _totals(document)[2] != 0:
        errs.append("R-INCOME: the income family carries no tax elements, but this document has tax; "
                    "a shop charging general sales tax must file under the general_sales family")

    if abs(rounding - quantize_money(document.grand_total, document.currency)) > TOTAL_TOLERANCE:
        errs.append(f"R-TOTAL: sum of lines {rounding} differs from document total "
                    f"{quantize_money(document.grand_total, document.currency)} by more than 0.001 "
                    f"(an invoice-level discount must be spread across the lines)")

    if credit:
        if not document.original_einvoice_no:
            errs.append("R-RETURN: credit note without the original invoice number (BillingReference)")
        try:
            uuid_module.UUID(document.original_uuid)
        except (ValueError, AttributeError, TypeError):
            errs.append("R-RETURN: credit note without the original invoice UUID (BillingReference)")
        if _d(document.original_total) <= 0:
            errs.append("R-RETURN: DocumentDescription must carry the original invoice total")
        if not (document.return_reason or '').strip():
            errs.append("R-RETURN: the return reason is mandatory (PaymentMeans/InstructionNote)")
    return errs


def _amount(tag: str, value, currency: str) -> str:
    return f'<cbc:{tag} currencyID={quoteattr(currency)}>{format_money_plain(value, currency)}</cbc:{tag}>'


def _rate_text(rate) -> str:
    r = _d(rate).normalize()
    return format(r.quantize(Decimal(1)) if r == r.to_integral() else r, 'f')


def _party_buyer(document) -> str:
    bid = document.buyer_id
    scheme = _SCHEME_FOR_BUYER_ID[bid.scheme] if bid else 'PN'
    value = bid.value if bid else ''
    ident = (f'<cbc:ID schemeID={quoteattr(scheme)}>{escape(value)}</cbc:ID>' if value
             else f'<cbc:ID schemeID={quoteattr(scheme)}/>')
    return (
        '<cac:AccountingCustomerParty><cac:Party>'
        f'<cac:PartyIdentification>{ident}</cac:PartyIdentification>'
        '<cac:PostalAddress><cac:Country><cbc:IdentificationCode>JO</cbc:IdentificationCode></cac:Country></cac:PostalAddress>'
        '<cac:PartyTaxScheme><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>'
        f'<cac:PartyLegalEntity><cbc:RegistrationName>{escape(document.buyer_name or "")}</cbc:RegistrationName></cac:PartyLegalEntity>'
        '</cac:Party></cac:AccountingCustomerParty>'
    )


def _party_seller(document) -> str:
    return (
        '<cac:AccountingSupplierParty><cac:Party>'
        '<cac:PostalAddress><cac:Country><cbc:IdentificationCode>JO</cbc:IdentificationCode></cac:Country></cac:PostalAddress>'
        f'<cac:PartyTaxScheme><cbc:CompanyID>{escape(document.seller_tin)}</cbc:CompanyID>'
        '<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>'
        f'<cac:PartyLegalEntity><cbc:RegistrationName>{escape(document.seller_name)}</cbc:RegistrationName></cac:PartyLegalEntity>'
        '</cac:Party></cac:AccountingSupplierParty>'
    )


def _tax_total_with_subtotal(line, tax, rounding, currency) -> str:
    return (
        '<cac:TaxTotal>'
        f'{_amount("TaxAmount", tax, currency)}{_amount("RoundingAmount", rounding, currency)}'
        '<cac:TaxSubtotal>'
        f'{_amount("TaxAmount", tax, currency)}'
        '<cac:TaxCategory>'
        f'<cbc:ID schemeAgencyID="6" schemeID="UN/ECE 5305">{line.tax_category}</cbc:ID>'
        f'<cbc:Percent>{_rate_text(line.tax_rate)}</cbc:Percent>'
        '<cac:TaxScheme><cbc:ID schemeAgencyID="6" schemeID="UN/ECE 5153">VAT</cbc:ID></cac:TaxScheme>'
        '</cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>'
    )


def _line_xml(index, line, currency, with_tax) -> str:
    _gross, discount, extension, tax, rounding = _line_amounts(line, currency)
    parts = [
        '<cac:InvoiceLine>',
        f'<cbc:ID>{index}</cbc:ID>',
        f'<cbc:InvoicedQuantity unitCode="PCE">{format(_d(line.quantity).normalize(), "f")}</cbc:InvoicedQuantity>',
        _amount('LineExtensionAmount', extension, currency),
    ]
    if with_tax:
        parts.append(_tax_total_with_subtotal(line, tax, rounding, currency))
    parts.append(f'<cac:Item><cbc:Name>{escape(line.description)}</cbc:Name></cac:Item>')
    parts.append(
        '<cac:Price>' + _amount('PriceAmount', line.unit_price, currency) +
        '<cac:AllowanceCharge><cbc:ChargeIndicator>false</cbc:ChargeIndicator>'
        '<cbc:AllowanceChargeReason>DISCOUNT</cbc:AllowanceChargeReason>' +
        _amount('Amount', discount, currency) + '</cac:AllowanceCharge></cac:Price>'
    )
    parts.append('</cac:InvoiceLine>')
    return ''.join(parts)


def to_istd_xml(document: EInvoiceDocument) -> str:
    """Deterministic ISTD-profile UBL 2.1 XML (same input -> same bytes, so
    document_sha256 stays a reproducible fingerprint). Raises IstdProfileError
    listing every violated rule; never emits a document it knows is invalid."""
    errors = validate_istd_document(document)
    if errors:
        raise IstdProfileError(errors)

    currency = document.currency
    credit = document.document_kind == 'credit_note'
    with_tax = document.type_name[2] != '1'  # R-INCOME: no TaxTotal for the income family
    gross, discount, tax, rounding = _totals(document)
    issue_date = document.issue_datetime[:10]

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        # R-MIN: ONE line. The ext namespace is declared because the guide
        # speaks of a root with four namespaces (the 4th is unnamed in the
        # public text -- open question; ext is the UBL-standard fourth).
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
        'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2">',
        f'<cbc:ProfileID>{WIRE_PROFILE_ID}</cbc:ProfileID>',
        f'<cbc:ID>{escape(document.einvoice_no)}</cbc:ID>',
        f'<cbc:UUID>{escape(document.uuid)}</cbc:UUID>',
        f'<cbc:IssueDate>{escape(issue_date)}</cbc:IssueDate>',
        f'<cbc:InvoiceTypeCode name={quoteattr(document.type_name)}>'
        f'{TYPE_CODE_CREDIT_NOTE if credit else TYPE_CODE_INVOICE}</cbc:InvoiceTypeCode>',
    ]
    if not credit and document.local_document_no:
        parts.append(f'<cbc:Note>{escape("local_document_no=" + document.local_document_no)}</cbc:Note>')
    parts += [
        f'<cbc:DocumentCurrencyCode>{escape(currency)}</cbc:DocumentCurrencyCode>',
        f'<cbc:TaxCurrencyCode>{escape(currency)}</cbc:TaxCurrencyCode>',
    ]
    if credit:
        parts.append(
            '<cac:BillingReference><cac:InvoiceDocumentReference>'
            f'<cbc:ID>{escape(document.original_einvoice_no)}</cbc:ID>'
            f'<cbc:UUID>{escape(document.original_uuid)}</cbc:UUID>'
            f'<cbc:DocumentDescription>{format_money_plain(document.original_total, currency)}</cbc:DocumentDescription>'
            '</cac:InvoiceDocumentReference></cac:BillingReference>'
        )
    parts += [
        '<cac:AdditionalDocumentReference><cbc:ID>ICV</cbc:ID>'
        f'<cbc:UUID>{int(document.icv)}</cbc:UUID></cac:AdditionalDocumentReference>',
        _party_seller(document),
        _party_buyer(document),
        '<cac:SellerSupplierParty><cac:Party><cac:PartyIdentification>'
        f'<cbc:ID>{escape(document.activity_code)}</cbc:ID>'
        '</cac:PartyIdentification></cac:Party></cac:SellerSupplierParty>',
    ]
    if credit:
        parts.append(
            '<cac:PaymentMeans><cbc:PaymentMeansCode listID="UN/ECE 4461">10</cbc:PaymentMeansCode>'
            f'<cbc:InstructionNote>{escape(document.return_reason)}</cbc:InstructionNote></cac:PaymentMeans>'
        )
    parts.append(
        '<cac:AllowanceCharge><cbc:ChargeIndicator>false</cbc:ChargeIndicator>'
        '<cbc:AllowanceChargeReason>DISCOUNT</cbc:AllowanceChargeReason>'
        + _amount('Amount', discount, currency) + '</cac:AllowanceCharge>'
    )
    if with_tax:
        parts.append('<cac:TaxTotal>' + _amount('TaxAmount', tax, currency) + '</cac:TaxTotal>')
    parts.append(
        '<cac:LegalMonetaryTotal>'
        + _amount('TaxExclusiveAmount', gross, currency)
        + _amount('TaxInclusiveAmount', rounding, currency)
        + _amount('AllowanceTotalAmount', discount, currency)
        + _amount('PayableAmount', rounding, currency)
        + '</cac:LegalMonetaryTotal>'
    )
    parts.extend(_line_xml(i + 1, line, currency, with_tax) for i, line in enumerate(document.lines))
    parts.append('</Invoice>')
    return ''.join(parts)
