from __future__ import annotations

from datetime import date

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


def test_author_only_note_hidden_from_non_author_non_management(app, seeded):
    from app.leads.notes import list_lead_notes_visible_to
    from app.leads.services import add_lead_note, create_lead

    author_staff = make_staff(app, "note1a@example.com")
    other_staff = make_staff(app, "note1b@example.com")
    with app.app_context():
        author_profile = _make_profile(app, author_staff, "EMP-N1A")
        other_profile = _make_profile(app, other_staff, "EMP-N1B")
        lead = create_lead({"organization_or_prospect_name": "Note Co", "phone": "111"}, author_profile.id, author_staff)

        add_lead_note(lead, "Private thought about this lead.", author_profile.id, author_staff, visibility="AUTHOR_ONLY")

        as_author = list_lead_notes_visible_to(lead.id, author_profile.id, is_management=False)
        as_other = list_lead_notes_visible_to(lead.id, other_profile.id, is_management=False)
        as_mgmt = list_lead_notes_visible_to(lead.id, other_profile.id, is_management=True)

        assert len(as_author) == 1
        assert len(as_other) == 0  # not merely redacted -- entirely absent, count doesn't leak
        assert len(as_mgmt) == 1


def test_management_only_note_hidden_from_non_management(app, seeded):
    from app.leads.notes import list_lead_notes_visible_to
    from app.leads.services import add_lead_note, create_lead

    staff_id = make_staff(app, "note2@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-N2")
        lead = create_lead({"organization_or_prospect_name": "Note Co 2", "phone": "111"}, profile.id, staff_id)

        add_lead_note(lead, "Escalation context for managers only.", profile.id, staff_id, visibility="MANAGEMENT_ONLY")

        as_regular = list_lead_notes_visible_to(lead.id, profile.id, is_management=False)
        as_mgmt = list_lead_notes_visible_to(lead.id, profile.id, is_management=True)

        assert len(as_regular) == 0
        assert len(as_mgmt) == 1


def test_assigned_record_users_note_visible_to_any_authorized_caller(app, seeded):
    from app.leads.notes import list_lead_notes_visible_to
    from app.leads.services import add_lead_note, create_lead

    author_staff = make_staff(app, "note3a@example.com")
    other_staff = make_staff(app, "note3b@example.com")
    with app.app_context():
        author_profile = _make_profile(app, author_staff, "EMP-N3A")
        other_profile = _make_profile(app, other_staff, "EMP-N3B")
        lead = create_lead({"organization_or_prospect_name": "Note Co 3", "phone": "111"}, author_profile.id, author_staff)

        add_lead_note(lead, "Standard progress note.", author_profile.id, author_staff)  # default visibility

        assert len(list_lead_notes_visible_to(lead.id, author_profile.id, is_management=False)) == 1
        assert len(list_lead_notes_visible_to(lead.id, other_profile.id, is_management=False)) == 1


def test_customer_note_visibility_mirrors_lead(app, seeded):
    from app.customers.services import add_note, create_customer
    from app.leads.notes import list_customer_notes_visible_to

    author_staff = make_staff(app, "note4a@example.com", role_codes=["SALES"])
    other_staff = make_staff(app, "note4b@example.com", role_codes=["SALES"])
    with app.app_context():
        customer = create_customer({"legal_name": "Note Customer Co"}, author_staff)
        add_note(customer, "Private.", author_staff, visibility="AUTHOR_ONLY")

        assert len(list_customer_notes_visible_to(customer.id, author_staff, is_management=False)) == 1
        assert len(list_customer_notes_visible_to(customer.id, other_staff, is_management=False)) == 0
