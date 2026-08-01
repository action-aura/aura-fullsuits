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


def test_convert_creates_active_customer_and_confirms_lead(app, seeded):
    staff_id = make_staff(app, "conv1@example.com")
    with app.app_context():
        from app.leads.conversion import convert
        from app.leads.services import create_lead
        from app.models.customers import Customer

        from app.employees.services import activate_employee

        profile = _make_profile(app, staff_id, "EMP-C1")
        # Phase 9.5C Milestone 4 -- create_lead() now validates that an
        # inline assigned_employee_profile_id is ACTIVE.
        activate_employee(profile, actor_staff_user_id=staff_id)
        lead = create_lead(
            {"organization_or_prospect_name": "Convertible LLC", "assigned_employee_profile_id": profile.id},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )

        customer = convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))

        assert isinstance(customer, Customer)
        assert customer.lifecycle_status == "ACTIVE"
        assert customer.converted_from_lead_id == lead.id
        assert customer.assigned_sales_staff_id == staff_id
        assert lead.status == "CONFIRMED"
        assert lead.converted_at is not None


def test_convert_is_idempotent(app, seeded):
    staff_id = make_staff(app, "conv2@example.com")
    with app.app_context():
        from app.leads.conversion import convert
        from app.leads.services import create_lead

        profile = _make_profile(app, staff_id, "EMP-C2")
        lead = create_lead(
            {"organization_or_prospect_name": "Idempotent LLC"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        key = str(uuid.uuid4())

        first = convert(lead, actor_staff_user_id=staff_id, idempotency_key=key)
        second = convert(lead, actor_staff_user_id=staff_id, idempotency_key=key)

        assert first.id == second.id


def test_convert_rejects_lost_lead(app, seeded):
    staff_id = make_staff(app, "conv3@example.com")
    with app.app_context():
        from app.leads.conversion import InvalidLeadStateError, convert
        from app.leads.services import change_lead_status, create_lead

        profile = _make_profile(app, staff_id, "EMP-C3")
        lead = create_lead(
            {"organization_or_prospect_name": "Lost LLC"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        # Phase 9.5C Milestone 2 -- a reason is now required for any -> LOST transition.
        change_lead_status(lead, "LOST", actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id, reason="budget cut")

        with pytest.raises(InvalidLeadStateError):
            convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))


def test_convert_flags_duplicate_without_explicit_confirmation(app, seeded):
    staff_id = make_staff(app, "conv4@example.com")
    with app.app_context():
        from app.customers.services import create_customer
        from app.leads.conversion import DuplicateCustomerError, convert
        from app.leads.services import create_lead

        profile = _make_profile(app, staff_id, "EMP-C4")
        create_customer({"legal_name": "Duplicate Target Co"}, actor_staff_user_id=staff_id)
        lead = create_lead(
            {"organization_or_prospect_name": "Duplicate Target Co"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )

        with pytest.raises(DuplicateCustomerError):
            convert(lead, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))


def test_convert_with_existing_customer_id_links_instead_of_creating(app, seeded):
    staff_id = make_staff(app, "conv5@example.com")
    with app.app_context():
        from app.customers.services import create_customer
        from app.leads.conversion import convert
        from app.leads.services import create_lead

        profile = _make_profile(app, staff_id, "EMP-C5")
        existing = create_customer({"legal_name": "Existing Org"}, actor_staff_user_id=staff_id)
        lead = create_lead(
            {"organization_or_prospect_name": "Existing Org"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )

        result = convert(
            lead, actor_staff_user_id=staff_id, existing_customer_id=existing.id, idempotency_key=str(uuid.uuid4())
        )

        assert result.id == existing.id
        assert result.lifecycle_status == "ACTIVE"
        assert result.converted_from_lead_id == lead.id
