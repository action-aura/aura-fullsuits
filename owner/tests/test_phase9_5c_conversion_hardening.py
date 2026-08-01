from __future__ import annotations

import uuid
from datetime import date

import pytest

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


def test_conversion_appends_status_history(app, seeded):
    from app.leads.conversion import convert
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv5@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import LeadStatusHistory

        profile = _make_profile(app, staff_id, "EMP-CV5")
        lead = create_lead({"organization_or_prospect_name": "History Co", "phone": "111"}, profile.id, staff_id)

        convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        history = db_session.query(LeadStatusHistory).filter_by(lead_id=lead.id).order_by(LeadStatusHistory.changed_at).all()
        assert [h.to_status for h in history] == ["NEW", "CONFIRMED"]


def test_cannot_reconvert_already_confirmed_lead(app, seeded):
    from app.leads.conversion import InvalidLeadStateError, convert
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv6@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CV6")
        lead = create_lead({"organization_or_prospect_name": "Reconvert Co", "phone": "111"}, profile.id, staff_id)
        convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        with pytest.raises(InvalidLeadStateError):
            convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))


def test_reusing_idempotency_key_for_different_lead_is_a_conflict(app, seeded):
    from app.leads.conversion import convert
    from app.leads.errors import LeadError
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv7@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CV7")
        lead_a = create_lead({"organization_or_prospect_name": "Conflict Co A", "phone": "111"}, profile.id, staff_id)
        lead_b = create_lead({"organization_or_prospect_name": "Conflict Co B", "phone": "222"}, profile.id, staff_id)

        shared_key = str(uuid.uuid4())
        convert(lead_a, actor_staff_user_id=staff_id, idempotency_key=shared_key)

        with pytest.raises(LeadError):
            convert(lead_b, actor_staff_user_id=staff_id, idempotency_key=shared_key)


def test_stale_version_rejected_on_conversion(app, seeded):
    from app.leads.conversion import convert
    from app.leads.errors import LeadError
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv8@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CV8")
        lead = create_lead({"organization_or_prospect_name": "Stale Co", "phone": "111"}, profile.id, staff_id)

        with pytest.raises(LeadError):
            convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()), expected_version=lead.version + 1)


def test_lead_contacts_copied_to_customer_on_conversion(app, seeded):
    from app.leads.contacts import add_lead_contact
    from app.leads.conversion import convert
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv9@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import CustomerContact

        profile = _make_profile(app, staff_id, "EMP-CV9")
        lead = create_lead({"organization_or_prospect_name": "Contact Carry Co", "phone": "111"}, profile.id, staff_id)
        add_lead_contact(lead.id, {"name": "Primary Contact", "is_primary": True, "business_email": "p@carry.example"}, staff_id)

        customer = convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        copied = db_session.query(CustomerContact).filter_by(customer_id=customer.id).all()
        assert len(copied) == 1
        assert copied[0].name == "Primary Contact"
        assert copied[0].business_email == "p@carry.example"
        assert copied[0].is_primary is True


def test_converted_customer_defaults_to_converting_actor_when_lead_unassigned(app, seeded):
    """Real bug found via Milestone 25 end-to-end browser testing: a Lead
    created without an explicit assignee converted to a Customer with
    assigned_sales_staff_id=None -- invisible to the very employee who
    just converted it, since the ownership-visibility check correctly
    treats an unassigned Customer as visible only to a view_all holder."""
    staff_id = make_staff(app, "conv11@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.customers.services import customer_visible_to_actor
        from app.leads.conversion import convert
        from app.leads.services import create_lead

        profile = _make_profile(app, staff_id, "EMP-CV11")
        lead = create_lead({"organization_or_prospect_name": "Unassigned Convert Co", "phone": "111"}, profile.id, staff_id)
        assert lead.assigned_employee_profile_id is None  # the real scenario: no explicit assignee

        customer = convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert customer.assigned_sales_staff_id == staff_id
        assert customer_visible_to_actor(customer, staff_id, {"customers.view_own"}) is True


def test_conversion_creates_no_subscription_license_or_commercial_documents(app, seeded):
    """Explicit negative-space proof: conversion must not create any
    commercial fulfillment record."""
    from app.leads.conversion import convert
    from app.leads.services import create_lead

    staff_id = make_staff(app, "conv10@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        profile = _make_profile(app, staff_id, "EMP-CV10")
        lead = create_lead({"organization_or_prospect_name": "No Fulfillment Co", "phone": "111"}, profile.id, staff_id)
        customer = convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert db_session.query(Subscription).filter_by(customer_id=customer.id).count() == 0
        assert db_session.query(License).filter_by(customer_id=customer.id).count() == 0
