from __future__ import annotations

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


def test_add_lead_contact_requires_name(app, seeded):
    from app.leads.contacts import add_lead_contact
    from app.leads.errors import LeadError
    from app.leads.services import create_lead

    staff_id = make_staff(app, "contact1@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CT1")
        lead = create_lead({"organization_or_prospect_name": "Contact Co", "phone": "111"}, profile.id, staff_id)

        with pytest.raises(LeadError):
            add_lead_contact(lead.id, {"name": ""}, staff_id)


def test_setting_new_primary_lead_contact_demotes_old_one(app, seeded):
    from app.leads.contacts import add_lead_contact, list_lead_contacts
    from app.leads.services import create_lead

    staff_id = make_staff(app, "contact2@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CT2")
        lead = create_lead({"organization_or_prospect_name": "Contact Co 2", "phone": "111"}, profile.id, staff_id)

        first = add_lead_contact(lead.id, {"name": "Alice", "is_primary": True}, staff_id)
        assert first.is_primary is True

        second = add_lead_contact(lead.id, {"name": "Bob", "is_primary": True}, staff_id)
        assert second.is_primary is True

        contacts = list_lead_contacts(lead.id)
        primaries = [c for c in contacts if c.is_primary]
        assert len(primaries) == 1
        assert primaries[0].name == "Bob"


def test_setting_new_primary_customer_contact_demotes_old_one(app, seeded):
    from app.customers.services import add_contact, create_customer

    staff_id = make_staff(app, "contact3@example.com", role_codes=["SALES"])
    with app.app_context():
        customer = create_customer({"legal_name": "Contact Customer Co"}, staff_id)
        add_contact(customer, {"name": "Alice", "is_primary": True}, staff_id)
        add_contact(customer, {"name": "Bob", "is_primary": True}, staff_id)

        from app.extensions import db_session
        from app.models.customers import CustomerContact

        rows = db_session.query(CustomerContact).filter_by(customer_id=customer.id).all()
        primaries = [c for c in rows if c.is_primary]
        assert len(primaries) == 1
        assert primaries[0].name == "Bob"


def test_update_lead_contact_stale_version_rejected(app, seeded):
    from app.leads.contacts import add_lead_contact, update_lead_contact
    from app.leads.errors import LeadError
    from app.leads.services import create_lead

    staff_id = make_staff(app, "contact4@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-CT4")
        lead = create_lead({"organization_or_prospect_name": "Contact Co 4", "phone": "111"}, profile.id, staff_id)
        contact = add_lead_contact(lead.id, {"name": "Carol"}, staff_id)

        with pytest.raises(LeadError):
            update_lead_contact(contact, {"title": "CEO"}, staff_id, expected_version=contact.version + 1)

        update_lead_contact(contact, {"title": "CEO"}, staff_id, expected_version=contact.version)
        assert contact.title == "CEO"
