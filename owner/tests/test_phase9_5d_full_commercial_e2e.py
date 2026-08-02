"""Phase 9.5D Milestone 27 -- full local commercial E2E.

One continuous scenario walking the entire authoritative commercial path
in a single test, proving the whole system works together as a unit (not
just pairwise, which every earlier milestone's own test file already
proves): Lead -> qualify -> Lead-based Quote -> pricing exception ->
approval -> customer acceptance -> Lead-to-Customer conversion boundary
-> Sales Order -> Commercial Invoice -> Payment -> Allocation -> real
commission earning -> commission approval -> payout batch -> fulfillment
(real Subscription + License) -> partial Refund -> proportional commission
reversal -> entitlement consequence.

Matches the "Enterprise Acceptance Test" precedent already used to close
Accounting Phase 16 -- a real, deployment-shaped end-to-end proof, not a
restatement of unit coverage.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import make_staff


def test_full_commercial_lifecycle_end_to_end(app, seeded):
    staff_sales = make_staff(app, "e2e-sales@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "e2e-finance@example.com", role_codes=["FINANCE"])

    with app.app_context():
        from app.employees.services import create_employee_profile

        profile_sales = create_employee_profile(
            {"staff_user_id": staff_sales, "employee_number": "EMP-E2E-SALES", "full_name": "E2E Sales", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_sales,
        )
        create_employee_profile(
            {"staff_user_id": staff_finance, "employee_number": "EMP-E2E-FIN", "full_name": "E2E Finance", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_finance,
        )
        profile_sales_id = profile_sales.id

    # ---- Catalog + commission plan setup ----
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
        from app.commissions.management import assign_employee_commission_plan, create_commission_plan, create_commission_rule_version
        from app.extensions import db_session
        from app.models.catalog import Product

        seed_canonical_catalog()
        product = Product(product_code="PROD_E2E", name="E2E Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {"plan_code": "E2E_PLAN", "product_id": product.id, "name": "E2E Plan", "billing_model": "MONTHLY", "effective_date": date.today() - timedelta(days=1)},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, Decimal("1000.00"), "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        plan_id = plan.id

        commission_plan = create_commission_plan({"plan_code": "E2E_COMM_PLAN", "name": "E2E Commission Plan"}, staff_finance)
        create_commission_rule_version(
            commission_plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("10.0"),
            effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_finance,
        )
        assign_employee_commission_plan(profile_sales_id, commission_plan, effective_from=date.today() - timedelta(days=1), actor_staff_user_id=staff_finance)

    # ---- Lead -> qualified ----
    with app.app_context():
        from app.leads.services import change_lead_status, create_lead

        lead = create_lead({"organization_or_prospect_name": "E2E Lead Co", "phone": "+962-79-111-2222"}, profile_sales_id, staff_sales)
        for target in ("POTENTIAL", "FOLLOW_UP", "UNDER_OBSERVATION", "QUALIFIED"):
            change_lead_status(lead, target, actor_employee_profile_id=profile_sales_id, actor_staff_user_id=staff_sales, reason=None)
        lead_id = lead.id

    # ---- Lead-based Quote, with a pricing exception requiring approval ----
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.models.commercial_sales import Quote

        quote = create_quote({"lead_id": lead_id, "currency": "USD"}, actor_employee_profile_id=profile_sales_id, actor_staff_user_id=staff_sales)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1,
            override_unit_price=Decimal("900.00"), override_reason="loyal prospect discount",
            actor_staff_user_id=staff_sales,
        )
        submit_quote(quote, actor_staff_user_id=staff_sales)

        pending = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])
        assert len(pending) == 1, "the price override must have created a real pending approval"
        decide_approval(pending[0], approved=True, decision_reason="approved, within finance discretion", decided_by_staff_user_id=staff_finance)

        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_sales)
        quote_id = quote.id
        quote = db_session.get(Quote, quote_id)
        assert quote.status == "ACCEPTED"

    # ---- Lead-to-Customer conversion boundary ----
    with app.app_context():
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.models.commercial_sales import Quote
        from app.models.leads import Lead

        quote = db_session.get(Quote, quote_id)
        customer = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_sales, idempotency_key=str(uuid.uuid4()))
        customer_id = customer.id
        lead = db_session.get(Lead, lead_id)
        assert lead.status == "CONFIRMED"

    # ---- Sales Order ----
    with app.app_context():
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
        from app.models.commercial_sales import Quote

        quote = db_session.get(Quote, quote_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_sales_id, actor_staff_user_id=staff_sales, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=staff_finance)
        order_id = order.id
        assert order.total == Decimal("900.00")

    # ---- Commercial Invoice ----
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.models.commercial_sales import SalesOrder

        order = db_session.get(SalesOrder, order_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_sales_id, actor_staff_user_id=staff_sales, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=staff_finance)
        invoice_id = invoice.id

    # ---- Payment + Allocation (the real commission-earning trigger) ----
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.models.commercial_sales import CommercialInvoice

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("900.00"), currency="USD", method="BANK_TRANSFER",
            payment_date=date.today(), actor_staff_user_id=staff_sales,
        )
        confirm_payment(payment, actor_staff_user_id=staff_finance)
        payment_id = payment.id

        invoice = db_session.get(CommercialInvoice, invoice_id)
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=Decimal("900.00"), actor_staff_user_id=staff_finance)
        allocation_id = allocation.id
        assert invoice.status == "PAID"

    # ---- Real commission earning ----
    with app.app_context():
        from sqlalchemy import select

        from app.models.commissions import CommissionLedgerEntry

        entries = db_session.execute(
            select(CommissionLedgerEntry).where(CommissionLedgerEntry.source_payment_allocation_id == allocation_id)
        ).scalars().all()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.status == "EARNED"
        assert entry.commission_amount == Decimal("90.00")  # 10% of 900, tax-excluded
        entry_id = entry.id

    # ---- Commission approval + payout ----
    with app.app_context():
        from app.commissions.ledger import approve_commission_entry, approve_payout_batch, create_payout_batch, record_payout
        from app.models.commissions import CommissionLedgerEntry

        entry = db_session.get(CommissionLedgerEntry, entry_id)
        approve_commission_entry(entry, actor_staff_user_id=staff_finance)
        assert entry.status == "APPROVED"

        batch = create_payout_batch(
            {"batch_reference": f"E2E-PAYOUT-{uuid.uuid4().hex[:8]}", "period_start": date.today(), "period_end": date.today()},
            staff_finance,
        )
        approve_payout_batch(batch, actor_staff_user_id=staff_sales)  # different actor than the batch creator
        record_payout(entry, batch, actor_staff_user_id=staff_finance)
        assert entry.status == "PAID"
        assert entry.paid_at is not None

    # ---- Fulfillment: real Subscription + License ----
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.models.commercial_sales import SalesOrder
        from app.models.subscriptions import Subscription

        order = db_session.get(SalesOrder, order_id)
        result = fulfill_order(order, actor_staff_user_id=staff_finance, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert result["subscription_id"] is not None
        assert result["license_id"] is not None
        subscription_id = result["subscription_id"]
        subscription = db_session.get(Subscription, subscription_id)
        assert subscription.status == "ACTIVE"

    # ---- Partial refund -> proportional commission reversal ----
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund
        from app.models.commercial_sales import CommercialInvoice
        from app.models.commissions import CommissionLedgerEntry
        from app.models.subscriptions import Subscription

        invoice = db_session.get(CommercialInvoice, invoice_id)
        refund = create_refund(
            invoice, amount=Decimal("450.00"), reason="customer downsizing", payment_record_id=payment_id,
            actor_employee_profile_id=profile_sales_id, actor_staff_user_id=staff_sales,
        )
        approve_refund(refund, actor_staff_user_id=staff_finance)
        confirm_refund(refund, actor_staff_user_id=staff_finance)
        assert invoice.status == "PARTIALLY_REFUNDED"

        # Proportional (50%) reversal of the original 90.00 commission.
        reversal = db_session.execute(
            select(CommissionLedgerEntry).where(CommissionLedgerEntry.reversal_of_ledger_entry_id == entry_id)
        ).scalars().first()
        assert reversal is not None
        assert reversal.status == "REVERSED"
        assert reversal.commission_amount == Decimal("-45.00")

        # Original entry itself is untouched -- append-only.
        original = db_session.get(CommissionLedgerEntry, entry_id)
        assert original.commission_amount == Decimal("90.00")
        assert original.status == "PAID"

        # A 50% refund is not a full refund -- no entitlement consequence,
        # the subscription stays ACTIVE (determine_entitlement_consequence()'s
        # own documented rule).
        subscription = db_session.get(Subscription, subscription_id)
        assert subscription.status == "ACTIVE"

    # ---- Full audit trail sanity: every major transition left a real record ----
    with app.app_context():
        from app.models.audit import AuditLog

        action_codes = {
            "QUOTE_CREATED", "QUOTE_SUBMITTED", "COMMERCIAL_APPROVAL_APPROVED", "QUOTE_LINKED_TO_CONVERTED_CUSTOMER",
            "ORDER_CREATED", "ORDER_CONFIRMED", "INVOICE_CREATED", "INVOICE_ISSUED", "PAYMENT_CONFIRMED",
            "PAYMENT_ALLOCATED", "COMMISSION_EARNED", "COMMISSION_APPROVED", "COMMISSION_PAID",
            "FULFILLMENT_COMPLETED", "REFUND_CONFIRMED", "COMMISSION_REVERSED",
        }
        present = {
            row[0] for row in db_session.execute(
                select(AuditLog.action_code).where(AuditLog.action_code.in_(action_codes))
            ).all()
        }
        missing = action_codes - present
        assert not missing, f"expected audit entries missing for: {missing}"
