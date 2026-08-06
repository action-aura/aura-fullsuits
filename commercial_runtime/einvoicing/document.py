"""JoFotara e-invoicing -- the canonical, product-agnostic document model.

Built by a per-product adapter (products/retail/backend/core/retail/
einvoice_adapter.py, products/clinic/backend/core/clinic/einvoice_adapter.py
-- Step 13/14) from that product's own sale/invoice rows, so nothing in
commercial_runtime/einvoicing ever needs to know what a `sale_item` or a
`clinic_invoice_item` is. Every provider (MockProvider, and eventually
DirectISTDProvider) and ubl.py consume only this type.
"""
from __future__ import annotations

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
