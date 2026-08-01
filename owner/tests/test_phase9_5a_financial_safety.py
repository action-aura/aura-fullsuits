"""Phase 9.5A Milestone 24 -- financial-safety tests.

Only what is real and testable at this foundation milestone: the DB-level
duplicate-commission guard (a real, migrated partial unique index), the
Decimal-exactness of calculate_commission(), historical price-snapshot
preservation, and a structural check that Expense has no relationship to
CommercialInvoice at all (so an expense entry can never alter one -- there
is no code path, not merely an untested one). Refund-creates-reversal and
no-self-approval are NOT tested here -- no service layer posts a
CommissionLedgerEntry or CommercialRefund yet (reserved, see
audit-event-catalog.md); those require the real posting service a later
milestone/phase builds.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_staff


def _make_profile(app, staff_id, employee_number):
    from app.employees.services import create_employee_profile

    return create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )


def _make_invoice_and_payment(staff_id, profile, invoice_number):
    """Real CommercialInvoice + PaymentRecord rows -- CommissionLedgerEntry's
    FKs are enforced, so a duplicate-guard test needs real referenced rows,
    not arbitrary UUIDs."""
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice
    from app.models.customers import Customer
    from app.models.subscriptions import PaymentRecord

    customer = Customer(legal_name=f"Fin Test Co {invoice_number}")
    db_session.add(customer)
    db_session.flush()
    invoice = CommercialInvoice(
        customer_id=customer.id, created_by_employee_profile_id=profile.id, invoice_number=invoice_number,
        currency="USD", subtotal=Decimal("100.00"), total=Decimal("100.00"),
    )
    db_session.add(invoice)
    db_session.flush()
    payment = PaymentRecord(
        customer_id=customer.id, amount=Decimal("100.00"), currency="USD", payment_date=date(2026, 6, 1),
        commercial_invoice_id=invoice.id,
    )
    db_session.add(payment)
    db_session.flush()
    return invoice, payment


def test_duplicate_payment_event_cannot_create_two_earned_entries(app, seeded):
    """The real, migrated partial unique index
    uq_commission_ledger_one_entry_per_payment (postgresql_where
    reversal_of_ledger_entry_id IS NULL) is the DB-level guard against a
    duplicate payment-confirmation webhook/event creating a second EARNED
    commission for the same real payment."""
    staff_id = make_staff(app, "fin1@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry, CommissionPlan, CommissionRuleVersion

        profile = _make_profile(app, staff_id, "EMP-FIN1")
        plan = CommissionPlan(plan_code="FIN1-PLAN", name="Fin1 Plan")
        db_session.add(plan)
        db_session.flush()
        rule = CommissionRuleVersion(
            commission_plan_id=plan.id, rule_type="FIXED_AMOUNT", fixed_amount=Decimal("10.00"),
            effective_from=date(2026, 1, 1),
        )
        db_session.add(rule)
        db_session.flush()

        invoice, payment = _make_invoice_and_payment(staff_id, profile, "FIN1-INV-001")
        entry1 = CommissionLedgerEntry(
            employee_profile_id=profile.id, commission_rule_version_id=rule.id,
            source_commercial_invoice_id=invoice.id, source_payment_record_id=payment.id,
            base_amount=Decimal("100.00"), rate_or_fixed_applied=Decimal("10.00"), commission_amount=Decimal("10.00"),
            currency="USD",
        )
        db_session.add(entry1)
        db_session.commit()

        entry2 = CommissionLedgerEntry(
            employee_profile_id=profile.id, commission_rule_version_id=rule.id,
            source_commercial_invoice_id=invoice.id, source_payment_record_id=payment.id,
            base_amount=Decimal("100.00"), rate_or_fixed_applied=Decimal("10.00"), commission_amount=Decimal("10.00"),
            currency="USD",
        )
        db_session.add(entry2)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


def test_reversal_entry_is_exempt_from_the_duplicate_guard(app, seeded):
    """A reversal legitimately shares its original's source_payment_record_id
    -- the partial index excludes rows with reversal_of_ledger_entry_id set,
    so a real reversal never collides with the guard meant for duplicate
    EARNED entries."""
    staff_id = make_staff(app, "fin2@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry, CommissionPlan, CommissionRuleVersion

        profile = _make_profile(app, staff_id, "EMP-FIN2")
        plan = CommissionPlan(plan_code="FIN2-PLAN", name="Fin2 Plan")
        db_session.add(plan)
        db_session.flush()
        rule = CommissionRuleVersion(
            commission_plan_id=plan.id, rule_type="FIXED_AMOUNT", fixed_amount=Decimal("10.00"),
            effective_from=date(2026, 1, 1),
        )
        db_session.add(rule)
        db_session.flush()

        invoice, payment = _make_invoice_and_payment(staff_id, profile, "FIN2-INV-001")
        original = CommissionLedgerEntry(
            employee_profile_id=profile.id, commission_rule_version_id=rule.id,
            source_commercial_invoice_id=invoice.id, source_payment_record_id=payment.id,
            base_amount=Decimal("100.00"), rate_or_fixed_applied=Decimal("10.00"), commission_amount=Decimal("10.00"),
            currency="USD",
        )
        db_session.add(original)
        db_session.commit()

        reversal = CommissionLedgerEntry(
            employee_profile_id=profile.id, commission_rule_version_id=rule.id,
            source_commercial_invoice_id=invoice.id, source_payment_record_id=payment.id,
            base_amount=Decimal("100.00"), rate_or_fixed_applied=Decimal("10.00"), commission_amount=Decimal("-10.00"),
            currency="USD", reversal_of_ledger_entry_id=original.id,
        )
        db_session.add(reversal)
        db_session.commit()  # must not raise
        assert reversal.commission_amount == Decimal("-10.00")


def test_historical_plan_price_row_never_overwritten(app, seeded):
    staff_id = make_staff(app, "fin3@example.com")
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan
        from app.extensions import db_session
        from app.models.catalog import PlanPrice, Product
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        plan = create_plan(
            {"plan_code": "FIN3-PLAN", "product_id": product.id, "name": "Fin3 Plan", "billing_model": "MONTHLY", "currency": "USD"},
            actor_staff_user_id=staff_id,
        )
        first_price = add_plan_price(plan, base_price="100.00", currency="USD", effective_from=date(2026, 1, 1), actor_staff_user_id=staff_id)
        add_plan_price(plan, base_price="150.00", currency="USD", effective_from=date(2026, 6, 1), actor_staff_user_id=staff_id)

        refreshed = db_session.get(PlanPrice, first_price.id)
        assert refreshed is not None  # still exists, never deleted
        assert refreshed.base_price == Decimal("100.00")  # never overwritten
        assert refreshed.effective_until == date(2026, 6, 1)  # closed out, not erased


def test_expense_model_has_no_relationship_to_commercial_invoice(app):
    """Structural, not behavioral: proves 'expense doesn't alter invoice' by
    showing there is no code path at all, not merely an untested one --
    Expense has zero columns referencing owner_commercial_invoices."""
    with app.app_context():
        from app.models.expenses import Expense

        invoice_fks = [
            fk.target_fullname for col in Expense.__table__.columns for fk in col.foreign_keys
            if "commercial_invoice" in fk.target_fullname
        ]
        assert invoice_fks == []


def test_commission_amount_is_decimal_exact_not_float(app):
    from decimal import ROUND_HALF_UP

    with app.app_context():
        from app.commissions.services import calculate_commission
        from app.models.commissions import CommissionRuleVersion

        rule = CommissionRuleVersion(rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("7.5"), effective_from=date(2026, 1, 1))
        result = calculate_commission(rule, Decimal("33.33"))
        assert isinstance(result, Decimal)
        assert result == (Decimal("33.33") * Decimal("7.5") / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
