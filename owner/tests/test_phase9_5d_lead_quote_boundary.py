"""Phase 9.5D Milestone 7 -- Lead/Quote/Customer boundary tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff


def _seed_sales_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{email[:6].upper()}",
                "full_name": f"Sales {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_qualified_lead(app, staff_id, profile_id, name="Boundary Test Lead Co"):
    from app.leads.services import change_lead_status, create_lead

    with app.app_context():
        lead = create_lead({"organization_or_prospect_name": name, "phone": "+962-79-000-0000"}, profile_id, staff_id)
        for target in ("POTENTIAL", "FOLLOW_UP", "UNDER_OBSERVATION", "QUALIFIED"):
            change_lead_status(lead, target, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, reason=None)
        return lead.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Boundary Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Boundary Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def test_lead_based_quote_converts_on_acceptance(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "boundarya@example.com")
    lead_id = _seed_qualified_lead(app, staff_id, profile_id)
    plan_id = _seed_plan(app, "BA_PLAN")
    with app.app_context():
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"lead_id": lead_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        assert quote.customer_id is None
        assert quote.lead_id == lead_id

        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)

        customer = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert customer is not None
        assert quote.customer_id == customer.id
        assert quote.lead_id == lead_id  # historical origin retained, not cleared

        from app.extensions import db_session
        from app.models.leads import Lead

        lead = db_session.get(Lead, lead_id)
        assert lead.status == "CONFIRMED"

        # Milestone 21 audit-coverage finding: linking Quote.customer_id is
        # a real, separate mutation from convert()'s own internal audit
        # trail (which only knows about the Lead-to-Customer conversion,
        # not this Quote-specific consequence) -- must have its own entry.
        from app.models.audit import AuditLog

        link_events = db_session.query(AuditLog).filter_by(
            action_code="QUOTE_LINKED_TO_CONVERTED_CUSTOMER", entity_public_id=str(quote.id)
        ).all()
        assert len(link_events) == 1


def test_not_accepted_quote_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "boundaryb@example.com")
    lead_id = _seed_qualified_lead(app, staff_id, profile_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.commercial_sales.quotes import create_quote

        quote = create_quote({"lead_id": lead_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "QUOTE_NOT_ACCEPTED"


def test_customer_based_quote_returns_existing_customer_directly(app, seeded):
    from app.customers.services import create_customer

    staff_id, profile_id = _seed_sales_employee(app, "boundaryc@example.com")
    plan_id = _seed_plan(app, "BC_PLAN")
    with app.app_context():
        customer = create_customer({"legal_name": "Direct Customer Co"}, actor_staff_user_id=staff_id)
        customer_id = customer.id

    with app.app_context():
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)

        resolved = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        assert resolved.id == customer_id


def test_idempotent_replay_returns_same_customer(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "boundaryd@example.com")
    lead_id = _seed_qualified_lead(app, staff_id, profile_id)
    plan_id = _seed_plan(app, "BD_PLAN")
    with app.app_context():
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"lead_id": lead_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)

        first = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        # Second call: quote.customer_id is now already set, so this
        # returns immediately without touching conversion at all --
        # trivially idempotent by construction, not just by replay-key.
        second = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        assert first.id == second.id


def test_boundary_creates_no_commercial_fulfillment_documents(app, seeded):
    """Explicit negative-space proof, matching Phase 9.5C's own precedent:
    the boundary conversion must never create Payment/Subscription/
    License/Installation/Commission."""
    staff_id, profile_id = _seed_sales_employee(app, "boundarye@example.com")
    lead_id = _seed_qualified_lead(app, staff_id, profile_id)
    plan_id = _seed_plan(app, "BE_PLAN")
    with app.app_context():
        from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry
        from app.models.licensing import License
        from app.models.subscriptions import PaymentRecord, Subscription

        quote = create_quote({"lead_id": lead_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)

        customer = resolve_customer_for_accepted_quote(quote, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert db_session.query(Subscription).filter_by(customer_id=customer.id).count() == 0
        assert db_session.query(License).filter_by(customer_id=customer.id).count() == 0
        assert db_session.query(PaymentRecord).count() == 0
        assert db_session.query(CommissionLedgerEntry).filter_by(employee_profile_id=profile_id).count() == 0
