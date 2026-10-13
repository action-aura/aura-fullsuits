"""JoFotara e-invoicing -- the canonical, product-agnostic document model.

Built by a per-product adapter (products/retail/backend/core/retail/
einvoice_adapter.py, products/clinic/backend/core/clinic/einvoice_adapter.py
-- Step 13/14) from that product's own sale/invoice rows, so nothing in
commercial_runtime/einvoicing ever needs to know what a `sale_item` or a
`clinic_invoice_item` is. Every provider (MockProvider, and eventually
DirectISTDProvider) and ubl.py consume only this type.
"""
from __future__ import annotations

import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Literal, Optional

InvoiceFamily = Literal['income', 'general_sales']
PaymentType = Literal['cash', 'credit']
IdScheme = Literal['TIN', 'NIN', 'PN']

# Precedence when a buyer has more than one identifier on file -- TIN (tax
# identification number) is the strongest/most specific, then NIN (national
# ID), then PN (phone-linked). Matches JoFotara's own "buyer needs at least
# one of TIN/NIN/PN" requirement -- see the Phase 1 research this module was
# designed from (docs/einvoicing/phase1/jofotara-integration-architecture.md).
ID_SCHEME_PRECEDENCE = ('TIN', 'NIN', 'PN')


class MissingBuyerIdError(ValueError):
    pass


@dataclass(frozen=True)
class BuyerId:
    scheme: IdScheme
    value: str


@dataclass(frozen=True)
class EInvoiceLine:
    description: str
    quantity: float
    unit_price: float
    discount_amount: float
    tax_amount: float
    line_total: float
    # ISTD profile only (ubl.to_istd_xml); the generic profile ignores both.
    # tax_rate is a percentage (16 = 16%); tax_category is 'S'|'Z'|'O' (see
    # tax_category_for_rate). Defaults keep every pre-existing positional
    # constructor call (including providers/base.py's selftest) valid.
    tax_rate: float = 0.0
    tax_category: str = ''


@dataclass(frozen=True)
class EInvoiceDocument:
    company_id: int
    einvoice_no: str
    local_document_no: str
    invoice_family: InvoiceFamily
    payment_type: PaymentType
    currency: str
    issue_datetime: str  # ISO 8601, UTC
    seller_name: str
    seller_tin: str
    buyer_name: str
    buyer_id: Optional[BuyerId]
    lines: tuple = field(default_factory=tuple)
    subtotal: float = 0.0
    discount_total: float = 0.0
    tax_total: float = 0.0
    grand_total: float = 0.0
    # ---- ISTD profile fields (all defaulted: the generic profile and every
    # existing caller are unaffected). See docs/einvoicing/istd-profile.md.
    profile: str = 'GENERIC_UBL_2_1'      # 'GENERIC_UBL_2_1' | 'ISTD_JO_1_0'
    document_kind: str = 'invoice'        # 'invoice' (388) | 'credit_note' (381)
    uuid: str = ''                        # cbc:UUID -- stable across retries
    icv: int = 0                          # invoice counter value, >= 1
    type_name: str = ''                   # 3-digit InvoiceTypeCode@name, e.g. '012'
    activity_code: str = ''               # income-source sequence (SellerSupplierParty)
    original_einvoice_no: str = ''        # credit note only
    original_uuid: str = ''               # credit note only
    original_total: float = 0.0           # credit note only: the ORIGINAL's total
    return_reason: str = ''               # credit note only (mandatory)


def select_buyer_id(candidates: dict) -> Optional[BuyerId]:
    """candidates: a mapping like {'TIN': '...', 'NIN': '...', 'PN': '...'}
    with only the schemes the buyer actually has on file present (and
    non-empty). Returns the highest-precedence one, or None if the buyer has
    no identifier at all."""
    for scheme in ID_SCHEME_PRECEDENCE:
        value = candidates.get(scheme)
        if value:
            return BuyerId(scheme=scheme, value=value)
    return None


def require_buyer_id(candidates: dict) -> BuyerId:
    buyer_id = select_buyer_id(candidates)
    if buyer_id is None:
        raise MissingBuyerIdError(
            "Buyer has no TIN, NIN, or PN on file -- JoFotara requires at least one "
            "identifier per invoice when buyer_id_required is enabled."
        )
    return buyer_id


def derive_payment_type(payment_method: str, amount_paid: float, total: float) -> PaymentType:
    """cash unless the sale/invoice is genuinely on credit (unpaid or
    partially paid, or explicitly a credit payment method) -- mirrors how
    Retail already distinguishes a credit sale (see
    retail_api.py::create_sale's 'Credit sales require a customer' check)
    without importing anything product-specific into this module."""
    if payment_method == 'credit':
        return 'credit'
    if amount_paid + 0.005 < total:
        return 'credit'
    return 'cash'


# ---------------------------------------------------------------------------
# ISTD profile helpers (docs/einvoicing/istd-profile.md)
# ---------------------------------------------------------------------------

TAX_CATEGORY_STANDARD = 'S'      # rate > 0
TAX_CATEGORY_EXEMPT = 'Z'        # 0%, exempt            (ISTD guide v1.5 p.42, via Qoyod)
TAX_CATEGORY_ZERO_RATED = 'O'    # 0%, zero-rated/export (ISTD guide v1.5 p.42, via Qoyod)

# The rates the ISTD interface accepts (a validation list, NOT a tax table).
ISTD_ACCEPTED_RATES = (0, 1, 2, 3, 4, 5, 7, 8, 10, 16)

_DOC_UUID_NAMESPACE = uuid_module.UUID('5d0c6b1e-7a54-4c35-9d3e-1b0f6c2a8e47')  # fixed, arbitrary, stable


def tax_category_for_rate(rate, exempt: bool = False) -> str:
    """S for any rate > 0; at 0% Z when the line is exempt, else O (zero-rated).

    Retail has no per-product exempt flag (and a schema change is out of
    scope), so callers pass exempt=False and every 0% line becomes O. That is
    a recorded gap, not a decision that exempt goods are zero-rated -- see
    the open questions in docs/einvoicing/istd-profile.md."""
    if float(rate or 0) > 0:
        return TAX_CATEGORY_STANDARD
    return TAX_CATEGORY_EXEMPT if exempt else TAX_CATEGORY_ZERO_RATED


def document_uuid(company_id: int, invoice_ref: str) -> str:
    """cbc:UUID for one document. uuid5 of (company, outbox invoice_ref), so
    it is generated once per document and is IDENTICAL on every retry without
    needing a stored column (no schema change): the spec says the taxpayer
    system must store the UUID before first send and reuse it on resend."""
    return str(uuid_module.uuid5(_DOC_UUID_NAMESPACE, f"{company_id}:{invoice_ref}"))


def icv_from_einvoice_no(einvoice_no: str) -> int:
    """ICV = the numeric part of the number minted by the DEDICATED
    einvoice_sequence ('INC-000042' -> 42). Never derived from the shop's
    local document number (docs/einvoicing/phase1/invoice-numbering-audit.md)."""
    digits = ''.join(ch for ch in str(einvoice_no).rsplit('-', 1)[-1] if ch.isdigit())
    return int(digits) if digits else 0


_PAYMENT_DIGIT = {'cash': '1', 'credit': '2'}
_FAMILY_DIGIT = {'income': '1', 'general_sales': '2'}


def istd_type_name(payment_type: str, invoice_family: str) -> str:
    """The 3-digit InvoiceTypeCode@name: trade type ('0' = local; export etc.
    are out of scope), payment ('1' cash / '2' receivable), tax family
    ('1' income / '2' general sales tax; '3' special tax is not modelled)."""
    return '0' + _PAYMENT_DIGIT[payment_type] + _FAMILY_DIGIT[invoice_family]
