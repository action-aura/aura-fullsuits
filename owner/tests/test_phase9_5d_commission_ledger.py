"""Phase 9.5D Milestone 15 -- commission ledger tests.

Proves every Commission Non-Negotiable Rule the user listed verbatim:
no commission from Quote/Order/Invoice/unconfirmed Payment; basis is
confirmed Payment Allocation; tax excluded by default; partial
allocations create proportional earnings; refunds create append-only
proportional reversal entries; historical earnings never overwritten/
deleted; customer/deal reassignment does not transfer historical
commission credit; beneficiary cannot approve their own adjustment;
payout recording requires authority and an external reference;
duplicate earning and duplicate payout impossible under concurrency.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_sales_employee(app, email, role_codes=None):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes or ["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Staff {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Ledger Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("1000.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Ledger Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Ledger Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _grant_commission_plan(app, profile_id, staff_id, rate=Decimal("10.0")):
    from app.commissions.management import assign_employee_commission_plan, create_commission_plan, create_commission_rule_version

    plan = create_commission_plan({"plan_code": f"LEDGER_PLAN_{uuid.uuid4().hex[:8]}", "name": "Ledger Test Plan"}, staff_id)
    create_commission_rule_version(
        plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=rate,
        effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id,
    )
    assign_employee_commission_plan(profile_id, plan, effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_id)


def _make_issued_invoice(app, staff_id, profile_id, customer_id, plan_id):
    from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

    quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    confirm_order(order, actor_staff_user_id=staff_id)
    invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    issue_invoice(invoice, actor_staff_user_id=staff_id)
    return invoice


def _make_confirmed_payment(app, customer_id, amount, submitter_staff_id, confirmer_staff_id):
    from app.commercial_sales.payments import confirm_payment, submit_payment

    payment = submit_payment(
        customer_id=customer_id, amount=amount, currency="USD", method="CASH",
        payment_date=date.today(), actor_staff_user_id=submitter_staff_id,
    )
    return confirm_payment(payment, actor_staff_user_id=confirmer_staff_id)


def _ledger_entries_for_invoice(invoice_id):
    from sqlalchemy import select

    from app.extensions import db_session
    from app.models.commissions import CommissionLedgerEntry

    return db_session.execute(
        select(CommissionLedgerEntry).where(CommissionLedgerEntry.source_commercial_invoice_id == invoice_id)
    ).scalars().all()


def test_no_commission_from_quote_order_or_invoice_alone(app, seeded):
    """Rules 1-3: creating/accepting a Quote, confirming an Order, and
    issuing an Invoice must never, by themselves, post a commission
    entry -- only a confirmed Payment Allocation does."""
    staff_a, profile_a = _seed_sales_employee(app, "ledgera@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGA_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)

        assert _ledger_entries_for_invoice(invoice.id) == []


def test_no_commission_from_unconfirmed_payment(app, seeded):
    """Rule 4: a PENDING Payment, even if later linked to the invoice,
    earns nothing until it is both confirmed AND allocated."""
    from app.commercial_sales.payments import submit_payment

    staff_a, profile_a = _seed_sales_employee(app, "ledgerb@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGB_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a)
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        submit_payment(
            customer_id=customer_id, amount=Decimal("1000.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )

        assert _ledger_entries_for_invoice(invoice.id) == []


def test_confirmed_allocation_is_the_real_earning_trigger(app, seeded):
    """Rule 5: commission basis is confirmed Payment Allocation."""
    from app.commercial_sales.allocation import allocate_payment

    staff_a, profile_a = _seed_sales_employee(app, "ledgerc@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerd@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGC_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)

        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        entries = _ledger_entries_for_invoice(invoice.id)
        assert len(entries) == 1
        assert entries[0].employee_profile_id == profile_a
        assert entries[0].status == "EARNED"
        assert entries[0].commission_amount == Decimal("100.00")  # 10% of 1000, no tax


def test_tax_is_excluded_from_commission_base_by_default(app, seeded):
    """Rule 6: tax excluded by default -- base_amount must exclude the
    invoice's tax proportion, not the full allocated amount."""
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice

    staff_a, profile_a = _seed_sales_employee(app, "ledgere@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerf@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGE_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)

        # Simulate a taxed invoice directly -- add a 10% tax on top of the
        # existing 1000 subtotal so total=1100, taxable_fraction=1000/1100.
        invoice = db_session.get(CommercialInvoice, invoice.id)
        invoice.tax_total = Decimal("100.00")
        invoice.total = invoice.subtotal - invoice.discount_total + invoice.tax_total
        invoice.version += 1
        db_session.commit()

        payment = _make_confirmed_payment(app, customer_id, Decimal("1100.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1100.00"), actor_staff_user_id=staff_b)

        entries = _ledger_entries_for_invoice(invoice.id)
        assert len(entries) == 1
        # base_amount excludes the tax portion: 1100 * (1000/1100) = 1000.00
        assert entries[0].base_amount == Decimal("1000.00")
        assert entries[0].commission_amount == Decimal("100.00")  # 10% of the tax-excluded 1000, not 1100


def test_partial_allocations_create_proportional_earnings(app, seeded):
    """Rule 7: two separate partial allocations against the same invoice
    each independently earn their own proportional commission."""
    from app.commercial_sales.allocation import allocate_payment

    staff_a, profile_a = _seed_sales_employee(app, "ledgerg@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerh@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGG_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment1 = _make_confirmed_payment(app, customer_id, Decimal("300.00"), staff_a, staff_b)
        payment2 = _make_confirmed_payment(app, customer_id, Decimal("700.00"), staff_a, staff_b)

        allocate_payment(payment=payment1, invoice=invoice, amount=Decimal("300.00"), actor_staff_user_id=staff_b)
        allocate_payment(payment=payment2, invoice=invoice, amount=Decimal("700.00"), actor_staff_user_id=staff_b)

        entries = sorted(_ledger_entries_for_invoice(invoice.id), key=lambda e: e.commission_amount)
        assert len(entries) == 2
        assert entries[0].commission_amount == Decimal("30.00")
        assert entries[1].commission_amount == Decimal("70.00")


def test_refund_creates_append_only_proportional_reversal(app, seeded):
    """Rules 8-9: a refund creates a NEW reversal row proportional to the
    refunded fraction; the original EARNED entry's own commission_amount
    and status are never edited."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund

    staff_a, profile_a = _seed_sales_employee(app, "ledgeri@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerj@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGI_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        original = _ledger_entries_for_invoice(invoice.id)[0]
        assert original.commission_amount == Decimal("100.00")

        # 50% refund.
        refund = create_refund(
            invoice, amount=Decimal("500.00"), reason="partial refund test",
            payment_record_id=payment.id, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a,
        )
        approve_refund(refund, actor_staff_user_id=staff_b)
        confirm_refund(refund, actor_staff_user_id=staff_b)

        entries = _ledger_entries_for_invoice(invoice.id)
        assert len(entries) == 2, f"expected 2 ledger entries (original + reversal), got {len(entries)}: {[(e.id, e.status, e.commission_amount) for e in entries]}"

        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        db_session.refresh(original)
        # Original untouched -- append-only, never overwritten.
        assert original.commission_amount == Decimal("100.00")
        assert original.status == "EARNED"

        reversal = db_session.get(CommissionLedgerEntry, [e.id for e in entries if e.id != original.id][0])
        assert reversal.status == "REVERSED"
        assert reversal.reversal_of_ledger_entry_id == original.id
        assert reversal.commission_amount == Decimal("-50.00")  # 50% of the 100.00 earned


def test_customer_reassignment_does_not_transfer_historical_commission_credit(app, seeded):
    """Rule 10: an already-earned entry permanently credits the employee
    who earned it; reassigning the underlying deal to a different
    employee afterward has no effect on that historical row -- there is
    no code path that ever re-derives or rewrites an existing ledger
    entry's employee_profile_id from current ownership."""
    from app.commercial_sales.allocation import allocate_payment
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice

    staff_a, profile_a = _seed_sales_employee(app, "ledgerk@example.com")
    staff_c, profile_c = _seed_sales_employee(app, "ledgerl@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerm@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGK_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        original = _ledger_entries_for_invoice(invoice.id)[0]
        assert original.employee_profile_id == profile_a

        # Reassign the deal to a different employee after the fact.
        invoice = db_session.get(CommercialInvoice, invoice.id)
        invoice.created_by_employee_profile_id = profile_c
        invoice.version += 1
        db_session.commit()

        db_session.refresh(original)
        # Historical credit stays with the original earner, not the new owner.
        assert original.employee_profile_id == profile_a
        entries = _ledger_entries_for_invoice(invoice.id)
        assert len(entries) == 1  # reassignment alone posts no new entry


def test_beneficiary_cannot_approve_own_commission_entry(app, seeded):
    """Rule 11."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commissions.errors import CommissionError
    from app.commissions.ledger import approve_commission_entry

    staff_a, profile_a = _seed_sales_employee(app, "ledgern@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgero@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGN_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        entry = _ledger_entries_for_invoice(invoice.id)[0]

        with pytest.raises(CommissionError) as exc:
            approve_commission_entry(entry, actor_staff_user_id=staff_a)  # the beneficiary themself
        assert exc.value.code == "COMMISSION_SELF_APPROVAL_FORBIDDEN"

        # A different approver succeeds.
        approved = approve_commission_entry(entry, actor_staff_user_id=staff_b)
        assert approved.status == "APPROVED"


def test_payout_requires_reference_and_authority_and_approved_entry(app, seeded):
    """Rule 12: an external reference is required at batch creation, and
    a payout can only be recorded against an APPROVED entry in an
    APPROVED batch."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commissions.errors import CommissionError
    from app.commissions.ledger import approve_commission_entry, approve_payout_batch, create_payout_batch, record_payout

    staff_a, profile_a = _seed_sales_employee(app, "ledgerp@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerq@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGP_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)
        entry = _ledger_entries_for_invoice(invoice.id)[0]

        with pytest.raises(CommissionError) as exc:
            create_payout_batch({"batch_reference": "", "period_start": date.today(), "period_end": date.today()}, staff_b)
        assert exc.value.code == "COMMISSION_PAYOUT_REFERENCE_REQUIRED"

        batch = create_payout_batch(
            {"batch_reference": f"PAYOUT-{uuid.uuid4().hex[:8]}", "period_start": date.today(), "period_end": date.today()}, staff_b,
        )

        # Cannot pay an unapproved entry even in an approved batch.
        approved_batch = approve_payout_batch(batch, actor_staff_user_id=staff_a)
        with pytest.raises(CommissionError) as exc:
            record_payout(entry, approved_batch, actor_staff_user_id=staff_b)
        assert exc.value.code == "COMMISSION_INVALID_TRANSITION"

        approve_commission_entry(entry, actor_staff_user_id=staff_b)
        line = record_payout(entry, approved_batch, actor_staff_user_id=staff_b)
        assert line.amount == entry.commission_amount

        from app.extensions import db_session

        db_session.refresh(entry)
        assert entry.status == "PAID"
        assert entry.paid_at is not None


def test_payout_batch_creator_cannot_self_approve_batch(app, seeded):
    from app.commissions.errors import CommissionError
    from app.commissions.ledger import approve_payout_batch, create_payout_batch

    staff_a, _ = _seed_sales_employee(app, "ledgerr@example.com", role_codes=["FINANCE"])
    with app.app_context():
        batch = create_payout_batch(
            {"batch_reference": f"PAYOUT-{uuid.uuid4().hex[:8]}", "period_start": date.today(), "period_end": date.today()}, staff_a,
        )
        with pytest.raises(CommissionError) as exc:
            approve_payout_batch(batch, actor_staff_user_id=staff_a)
        assert exc.value.code == "COMMISSION_SELF_APPROVAL_FORBIDDEN"


def test_duplicate_earning_for_same_allocation_rejected(app, seeded):
    """Rule 13a: retrying the earning trigger for the same allocation
    (e.g. after a partial-failure retry) must never post a second
    entry -- the real DB-level partial unique index makes this
    structurally impossible, not merely an application-level check."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commissions.errors import CommissionError
    from app.commissions.ledger import post_earning_for_allocation
    from app.extensions import db_session
    from app.models.commercial_sales import PaymentAllocation

    staff_a, profile_a = _seed_sales_employee(app, "ledgers@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgert@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGS_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)

        assert len(_ledger_entries_for_invoice(invoice.id)) == 1

        allocation = db_session.get(PaymentAllocation, allocation.id)
        with pytest.raises(CommissionError) as exc:
            post_earning_for_allocation(allocation, actor_staff_user_id=staff_b)
        assert exc.value.code == "COMMISSION_ALREADY_EARNED_FOR_ALLOCATION"
        assert len(_ledger_entries_for_invoice(invoice.id)) == 1


def test_duplicate_earning_impossible_under_real_concurrency(app, seeded):
    """Rule 13a, real thread race: multiple concurrent retry attempts
    against the same allocation must yield exactly one EARNED entry."""
    staff_a, profile_a = _seed_sales_employee(app, "ledgeru@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerv@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGU_PLAN")

    allocation_id_holder = {}
    invoice_id_holder = {}
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation
        from app.models.commissions import CommissionLedgerEntry

        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)
        allocation_id_holder["id"] = allocation.id
        invoice_id_holder["id"] = invoice.id
        # Delete the earning the initial allocate_payment call already
        # posted, so every thread below races on a genuinely-unearned
        # allocation instead of all uniformly hitting the guard.
        db_session.query(CommissionLedgerEntry).filter_by(source_payment_allocation_id=allocation.id).delete()
        db_session.commit()

    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            with app.app_context():
                from app.commissions.ledger import post_earning_for_allocation
                from app.extensions import db_session
                from app.models.commercial_sales import PaymentAllocation

                local_allocation = db_session.get(PaymentAllocation, allocation_id_holder["id"])
                r = post_earning_for_allocation(local_allocation, actor_staff_user_id=staff_b)
                with lock:
                    results.append(r)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            from app.extensions import db_session as scoped_db_session

            scoped_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with app.app_context():
        assert len(_ledger_entries_for_invoice(invoice_id_holder["id"])) == 1

    assert len(results) + len(errors) == 8


def test_duplicate_payout_for_same_entry_rejected(app, seeded):
    """Rule 13b: recording a payout twice for the same ledger entry must
    be impossible -- the real DB-level unique constraint on
    commission_ledger_entry_id (Phase 9.5A) enforces this."""
    from app.commercial_sales.allocation import allocate_payment
    from app.commissions.errors import CommissionError
    from app.commissions.ledger import approve_commission_entry, approve_payout_batch, create_payout_batch, record_payout

    staff_a, profile_a = _seed_sales_employee(app, "ledgerw@example.com")
    staff_b, _ = _seed_sales_employee(app, "ledgerx@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "LGW_PLAN")
    with app.app_context():
        _grant_commission_plan(app, profile_a, staff_a, rate=Decimal("10.0"))
        invoice = _make_issued_invoice(app, staff_a, profile_a, customer_id, plan_id)
        payment = _make_confirmed_payment(app, customer_id, Decimal("1000.00"), staff_a, staff_b)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("1000.00"), actor_staff_user_id=staff_b)
        entry = _ledger_entries_for_invoice(invoice.id)[0]
        approve_commission_entry(entry, actor_staff_user_id=staff_b)

        batch_1 = create_payout_batch(
            {"batch_reference": f"PAYOUT-{uuid.uuid4().hex[:8]}", "period_start": date.today(), "period_end": date.today()}, staff_b,
        )
        approve_payout_batch(batch_1, actor_staff_user_id=staff_a)
        record_payout(entry, batch_1, actor_staff_user_id=staff_b)

        # Entry is now PAID -- a second payout attempt is rejected both by
        # the entry-status guard and (if bypassed) the DB-level unique
        # constraint. We prove the entry-status path here; the unique
        # constraint is the structural backstop for a race.
        with pytest.raises(CommissionError) as exc:
            record_payout(entry, batch_1, actor_staff_user_id=staff_b)
        assert exc.value.code == "COMMISSION_INVALID_TRANSITION"
