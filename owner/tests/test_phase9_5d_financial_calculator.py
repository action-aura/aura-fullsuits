"""Phase 9.5D Milestone 3 -- table-driven tests for the one authoritative
financial calculation service. No DB fixtures needed: this module is pure
Decimal arithmetic, deliberately request-independent (Non-Negotiable
Principle 17)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.commercial_sales.calculator import (
    LineInput,
    allocate_document_discount,
    calculate_document,
    calculate_line,
    calculate_refundable_balance,
    resolve_invoice_status,
    validate_allocation_amount,
    validate_currency,
    validate_refund_amount,
)
from app.commercial_sales.errors import CommercialSalesError


def test_simple_line_calculation():
    result = calculate_line(LineInput(quantity=3, unit_price=Decimal("10.00")))
    assert result.line_gross == Decimal("30.00")
    assert result.line_net == Decimal("30.00")


def test_line_with_own_discount():
    result = calculate_line(LineInput(quantity=2, unit_price=Decimal("50.00"), discount_amount=Decimal("15.00")))
    assert result.line_gross == Decimal("100.00")
    assert result.line_net == Decimal("85.00")


def test_line_price_override_used_over_catalog_price():
    result = calculate_line(LineInput(quantity=1, unit_price=Decimal("99.00"), override_unit_price=Decimal("50.00")))
    assert result.effective_unit_price == Decimal("50.00")
    assert result.line_gross == Decimal("50.00")


@pytest.mark.parametrize("quantity", [0, -1, -5])
def test_zero_or_negative_quantity_rejected(quantity):
    with pytest.raises(CommercialSalesError) as exc:
        calculate_line(LineInput(quantity=quantity, unit_price=Decimal("10.00")))
    assert exc.value.code == "INVALID_QUANTITY"


def test_discount_exceeding_line_gross_rejected():
    with pytest.raises(CommercialSalesError) as exc:
        calculate_line(LineInput(quantity=1, unit_price=Decimal("10.00"), discount_amount=Decimal("10.01")))
    assert exc.value.code == "DISCOUNT_EXCEEDS_GROSS"


def test_negative_unit_price_rejected():
    with pytest.raises(CommercialSalesError) as exc:
        calculate_line(LineInput(quantity=1, unit_price=Decimal("-5.00")))
    assert exc.value.code == "NEGATIVE_DOCUMENT_TOTAL"


@pytest.mark.parametrize("bad_value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nan_and_infinity_rejected_as_unit_price(bad_value):
    with pytest.raises(CommercialSalesError) as exc:
        calculate_line(LineInput(quantity=1, unit_price=bad_value))
    assert exc.value.code == "NON_FINITE_AMOUNT"


def test_document_discount_allocation_proportional_even_split():
    lines = [calculate_line(LineInput(quantity=1, unit_price=Decimal("100.00"))) for _ in range(2)]
    allocated = allocate_document_discount(lines, Decimal("20.00"))
    assert allocated == [Decimal("10.00"), Decimal("10.00")]
    assert sum(allocated, Decimal("0")) == Decimal("20.00")


def test_document_discount_allocation_deterministic_remainder():
    """Three equal lines splitting a discount that doesn't divide evenly by
    3 must still sum exactly to the requested discount, with the remainder
    cent(s) assigned deterministically by stable sort_order."""
    lines = [
        calculate_line(LineInput(quantity=1, unit_price=Decimal("10.00"), sort_order=i)) for i in range(3)
    ]
    allocated = allocate_document_discount(lines, Decimal("1.00"))
    assert sum(allocated, Decimal("0")) == Decimal("1.00")
    # 1.00 / 3 = 0.333... -> each line's raw share quantizes to 0.33,
    # leaving a 1-cent remainder assigned to the first line by sort_order.
    assert allocated == [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]


def test_document_discount_allocation_repeatable_for_same_input():
    lines = [calculate_line(LineInput(quantity=1, unit_price=Decimal("7.00"), sort_order=i)) for i in range(3)]
    first = allocate_document_discount(lines, Decimal("1.00"))
    second = allocate_document_discount(lines, Decimal("1.00"))
    assert first == second


def test_document_discount_exceeding_eligible_total_rejected():
    lines = [calculate_line(LineInput(quantity=1, unit_price=Decimal("10.00")))]
    with pytest.raises(CommercialSalesError) as exc:
        allocate_document_discount(lines, Decimal("10.01"))
    assert exc.value.code == "DISCOUNT_EXCEEDS_GROSS"


def test_calculate_document_full_flow():
    result = calculate_document(
        [
            LineInput(quantity=2, unit_price=Decimal("100.00"), sort_order=0),
            LineInput(quantity=1, unit_price=Decimal("50.00"), sort_order=1),
        ],
        document_discount=Decimal("25.00"),
        tax_total=Decimal("10.00"),
    )
    assert result.subtotal == Decimal("250.00")
    assert result.discount_total == Decimal("25.00")
    assert result.tax_total == Decimal("10.00")
    assert result.total == Decimal("235.00")
    # line totals must sum consistently
    assert sum((l.line_net for l in result.lines), Decimal("0")) == Decimal("225.00")


def test_calculate_document_awkward_prices_still_reconciles():
    """Odd per-unit price/quantity combination that doesn't divide evenly --
    the whole point of the deterministic remainder rule."""
    result = calculate_document(
        [
            LineInput(quantity=3, unit_price=Decimal("9.99"), sort_order=0),
            LineInput(quantity=7, unit_price=Decimal("3.33"), sort_order=1),
        ],
        document_discount=Decimal("5.00"),
    )
    assert result.subtotal == Decimal("53.28")
    assert result.discount_total == Decimal("5.00")
    assert result.total == Decimal("48.28")


def test_empty_document_rejected():
    with pytest.raises(CommercialSalesError) as exc:
        calculate_document([])
    assert exc.value.code == "EMPTY_DOCUMENT"


def test_document_total_never_negative():
    with pytest.raises(CommercialSalesError) as exc:
        calculate_document([LineInput(quantity=1, unit_price=Decimal("-1.00"))])
    assert exc.value.code == "NEGATIVE_DOCUMENT_TOTAL"


def test_price_snapshot_immutability_property():
    """The calculation service has no catalog dependency at all -- it only
    ever sees the price value it was given. This is the structural proof
    that changing a live catalog price cannot retroactively alter an
    already-computed document: recomputing with the *same* LineInput always
    reproduces the *same* result, regardless of what the catalog says now."""
    line = LineInput(quantity=2, unit_price=Decimal("19.99"))
    first = calculate_line(line)
    second = calculate_line(line)
    assert first == second


class TestRefundValidation:
    def test_refundable_balance_computation(self):
        assert calculate_refundable_balance(Decimal("100.00"), Decimal("30.00")) == Decimal("70.00")

    def test_refundable_balance_floors_at_zero(self):
        assert calculate_refundable_balance(Decimal("100.00"), Decimal("100.00")) == Decimal("0.00")

    def test_refund_within_balance_accepted(self):
        assert validate_refund_amount(Decimal("50.00"), Decimal("70.00")) == Decimal("50.00")

    def test_refund_exceeding_balance_rejected(self):
        with pytest.raises(CommercialSalesError) as exc:
            validate_refund_amount(Decimal("70.01"), Decimal("70.00"))
        assert exc.value.code == "REFUND_EXCEEDS_REFUNDABLE"

    def test_zero_or_negative_refund_rejected(self):
        with pytest.raises(CommercialSalesError):
            validate_refund_amount(Decimal("0"), Decimal("100.00"))


class TestAllocationValidation:
    def test_allocation_within_both_limits_accepted(self):
        amount = validate_allocation_amount(
            Decimal("50.00"), unallocated_payment_balance=Decimal("100.00"), invoice_outstanding_balance=Decimal("80.00")
        )
        assert amount == Decimal("50.00")

    def test_allocation_exceeding_payment_balance_rejected(self):
        with pytest.raises(CommercialSalesError) as exc:
            validate_allocation_amount(
                Decimal("101.00"), unallocated_payment_balance=Decimal("100.00"), invoice_outstanding_balance=Decimal("200.00")
            )
        assert exc.value.code == "ALLOCATION_EXCEEDS_PAYMENT"

    def test_allocation_exceeding_invoice_outstanding_rejected(self):
        with pytest.raises(CommercialSalesError) as exc:
            validate_allocation_amount(
                Decimal("81.00"), unallocated_payment_balance=Decimal("100.00"), invoice_outstanding_balance=Decimal("80.00")
            )
        assert exc.value.code == "ALLOCATION_EXCEEDS_OUTSTANDING"


class TestInvoiceStatusResolution:
    def test_void_never_overridden(self):
        assert resolve_invoice_status(current_status="VOID", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("100")) == "VOID"

    def test_draft_never_overridden(self):
        assert resolve_invoice_status(current_status="DRAFT", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("100")) == "DRAFT"

    def test_zero_allocated_is_issued(self):
        assert resolve_invoice_status(current_status="ISSUED", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("0")) == "ISSUED"

    def test_partial_allocation(self):
        assert resolve_invoice_status(current_status="ISSUED", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("40")) == "PARTIALLY_PAID"

    def test_full_allocation(self):
        assert resolve_invoice_status(current_status="ISSUED", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("100")) == "PAID"

    def test_overpayment_still_paid_not_an_error(self):
        assert resolve_invoice_status(current_status="ISSUED", invoice_total=Decimal("100"), allocated_payment_sum=Decimal("150")) == "PAID"


class TestCurrencyValidation:
    def test_valid_currency_uppercased(self):
        assert validate_currency("usd") == "USD"

    @pytest.mark.parametrize("bad", [None, "", "US", "USDD", "1JD"])
    def test_invalid_currency_rejected(self, bad):
        with pytest.raises(CommercialSalesError) as exc:
            validate_currency(bad)
        assert exc.value.code == "INVALID_CURRENCY_CODE"
