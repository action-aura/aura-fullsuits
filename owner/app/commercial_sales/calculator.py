"""Phase 9.5D Milestone 3 -- the one authoritative server-side financial
calculation service for Quote/SalesOrder/CommercialInvoice line and
document totals.

Decimal only, ROUND_HALF_UP at 2 decimal places -- the same rounding rule
already established across this codebase (products/retail/backend/core/
retail/pricing.py, products/clinic/backend/api/clinic_api.py,
owner/app/commissions/services.py::calculate_commission()). Unlike
Retail/Clinic (which round-trip through float for their own DB-layer
reasons), every commercial_sales model column is a real Numeric(12,2) --
Decimal is never converted to float anywhere in this module or its
callers.

Every Quote/SalesOrder/CommercialInvoice line total and document total in
this phase is computed by calling into this module -- never reimplemented
per-route or per-form (Non-Negotiable Principles 8/9). See
docs/owner/phase9_5d/financial-calculation-contract.md,
rounding-and-allocation-policy.md, price-snapshot-contract.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.commercial_sales.errors import CommercialSalesError

CENT = Decimal("0.01")
ISO_4217_LEN = 3


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _require_finite(value: Decimal, code: str = "NON_FINITE_AMOUNT") -> Decimal:
    try:
        if not value.is_finite():
            raise CommercialSalesError(code)
    except (InvalidOperation, AttributeError) as exc:
        raise CommercialSalesError(code) from exc
    return value


def validate_currency(currency: str | None) -> str:
    """Stricter than app/leads/validation.py's length-only check (that
    field is a non-financial estimate; this one gates real money
    movement) -- also requires the code to be alphabetic, matching real
    ISO 4217 codes (never digits/symbols)."""
    cleaned = (currency or "").strip()
    if len(cleaned) != ISO_4217_LEN or not cleaned.isalpha():
        raise CommercialSalesError("INVALID_CURRENCY_CODE")
    return cleaned.upper()


@dataclass(frozen=True)
class LineInput:
    """One proposed line, before calculation. `unit_price` is always the
    catalog/snapshot price; `override_unit_price` is set only when a
    price-override exception applies (Milestone 6 gates whether this is
    permitted -- this module only computes the arithmetic, it never
    decides whether an override is authorized)."""

    quantity: int
    unit_price: Decimal
    override_unit_price: Decimal | None = None
    discount_amount: Decimal = Decimal("0")
    sort_order: int = 0


@dataclass(frozen=True)
class LineResult:
    quantity: int
    effective_unit_price: Decimal
    line_gross: Decimal
    line_discount: Decimal
    line_net: Decimal
    sort_order: int


@dataclass(frozen=True)
class DocumentResult:
    lines: list[LineResult]
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal


def calculate_line(line: LineInput) -> LineResult:
    """Line-level arithmetic: quantity * effective unit price, minus this
    line's own discount. Document-level discount allocation (spread across
    lines proportionally) is a separate step -- see
    calculate_document_discount_allocation()."""
    if line.quantity <= 0:
        raise CommercialSalesError("INVALID_QUANTITY")

    effective_unit_price = line.override_unit_price if line.override_unit_price is not None else line.unit_price
    _require_finite(effective_unit_price)
    if effective_unit_price < 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")

    line_gross = _quantize(effective_unit_price * Decimal(line.quantity))

    discount = _require_finite(line.discount_amount or Decimal("0"))
    if discount < 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    if discount > line_gross:
        raise CommercialSalesError("DISCOUNT_EXCEEDS_GROSS")

    line_net = _quantize(line_gross - discount)

    return LineResult(
        quantity=line.quantity,
        effective_unit_price=effective_unit_price,
        line_gross=line_gross,
        line_discount=discount,
        line_net=line_net,
        sort_order=line.sort_order,
    )


def allocate_document_discount(lines: list[LineResult], document_discount: Decimal) -> list[Decimal]:
    """Deterministic proportional allocation of a document-level discount
    across lines, by each line's own net amount (after its own line-level
    discount has already been applied). Returns one additional-discount
    Decimal per line, in the same order as `lines`.

    Rounding remainder handling: each line's raw proportional share is
    quantized to the cent; whatever cent(s) remain after quantization
    (due to rounding) are assigned to the lines in stable `sort_order`
    (ties broken by list position) until the remainder is exhausted --
    deterministic and reproducible for the exact same input every time.
    """
    if document_discount < 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    if document_discount == 0 or not lines:
        return [Decimal("0.00") for _ in lines]

    eligible_total = sum((l.line_net for l in lines), Decimal("0"))
    if document_discount > eligible_total:
        raise CommercialSalesError("DISCOUNT_EXCEEDS_GROSS")
    if eligible_total == 0:
        return [Decimal("0.00") for _ in lines]

    ordered = sorted(range(len(lines)), key=lambda i: (lines[i].sort_order, i))

    raw_shares = [
        (document_discount * lines[i].line_net / eligible_total) if eligible_total else Decimal("0")
        for i in range(len(lines))
    ]
    quantized = [_quantize(share) for share in raw_shares]

    allocated_sum = sum(quantized, Decimal("0"))
    remainder_cents = int((document_discount - allocated_sum) / CENT)

    if remainder_cents > 0:
        for idx in ordered[:remainder_cents]:
            quantized[idx] = quantized[idx] + CENT
    elif remainder_cents < 0:
        for idx in reversed(ordered[:(-remainder_cents)]):
            quantized[idx] = quantized[idx] - CENT

    return quantized


def calculate_document(
    line_inputs: list[LineInput],
    *,
    document_discount: Decimal = Decimal("0"),
    tax_total: Decimal = Decimal("0"),
) -> DocumentResult:
    """The one function that computes a Quote/SalesOrder/CommercialInvoice's
    full totals from its lines. Never call this with client-supplied
    subtotal/discount_total/total -- those fields are always the *output*
    of this function, never an input (Non-Negotiable Principle 8)."""
    if not line_inputs:
        raise CommercialSalesError("EMPTY_DOCUMENT")

    line_results = [calculate_line(li) for li in line_inputs]

    extra_discounts = allocate_document_discount(line_results, document_discount)
    final_lines = [
        LineResult(
            quantity=lr.quantity,
            effective_unit_price=lr.effective_unit_price,
            line_gross=lr.line_gross,
            line_discount=_quantize(lr.line_discount + extra),
            line_net=_quantize(lr.line_net - extra),
            sort_order=lr.sort_order,
        )
        for lr, extra in zip(line_results, extra_discounts)
    ]

    subtotal = _quantize(sum((lr.line_gross for lr in line_results), Decimal("0")))
    total_line_discount = _quantize(sum((fl.line_discount for fl in final_lines), Decimal("0")))
    tax = _require_finite(tax_total or Decimal("0"))
    if tax < 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")

    total = _quantize(subtotal - total_line_discount + tax)
    if total < 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")

    return DocumentResult(
        lines=final_lines,
        subtotal=subtotal,
        discount_total=total_line_discount,
        tax_total=_quantize(tax),
        total=total,
    )


def calculate_refundable_balance(invoice_total: Decimal, already_refunded: Decimal) -> Decimal:
    _require_finite(invoice_total)
    _require_finite(already_refunded)
    balance = _quantize(invoice_total - already_refunded)
    return balance if balance > 0 else Decimal("0.00")


def validate_refund_amount(amount: Decimal, refundable_balance: Decimal) -> Decimal:
    amount = _require_finite(amount)
    if amount <= 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    if amount > refundable_balance:
        raise CommercialSalesError("REFUND_EXCEEDS_REFUNDABLE", amount=amount, refundable=refundable_balance)
    return _quantize(amount)


def validate_allocation_amount(
    amount: Decimal,
    *,
    unallocated_payment_balance: Decimal,
    invoice_outstanding_balance: Decimal,
) -> Decimal:
    amount = _require_finite(amount)
    if amount <= 0:
        raise CommercialSalesError("NEGATIVE_DOCUMENT_TOTAL")
    if amount > unallocated_payment_balance:
        raise CommercialSalesError("ALLOCATION_EXCEEDS_PAYMENT", amount=amount, available=unallocated_payment_balance)
    if amount > invoice_outstanding_balance:
        raise CommercialSalesError("ALLOCATION_EXCEEDS_OUTSTANDING", amount=amount, outstanding=invoice_outstanding_balance)
    return _quantize(amount)


def resolve_invoice_status(
    *,
    current_status: str,
    invoice_total: Decimal,
    allocated_payment_sum: Decimal,
) -> str:
    """Matches docs/owner/phase9_5a/payment-and-fulfillment-contract.md's
    resolve_invoice_status(), extended (Milestone 11) to sum
    PaymentAllocation rows rather than raw PaymentRecord amounts. VOID/DRAFT
    are never overridden by a payment-sum computation."""
    if current_status in ("VOID", "DRAFT"):
        return current_status
    if allocated_payment_sum <= 0:
        return "ISSUED"
    if allocated_payment_sum < invoice_total:
        return "PARTIALLY_PAID"
    return "PAID"
