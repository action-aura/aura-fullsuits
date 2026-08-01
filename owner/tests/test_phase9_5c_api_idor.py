"""Phase 9.5C Milestone 21 -- security/IDOR completion tests against the
real HTTP layer (Flask test client), not just the service layer directly.
Proves apply_ownership_filter()/customer_visible_to_actor() are actually
wired into every CRM route, not merely correct in isolation.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from tests.conftest import force_login, get_csrf, make_staff


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


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def test_employee_a_cannot_read_employee_b_lead_via_api(app, client, seeded):
    staff_a = make_staff(app, "idorapi1a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "idorapi1b@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.leads.services import create_lead

        profile_a = _make_profile(app, staff_a, "EMP-IA1A")
        profile_b = _make_profile(app, staff_b, "EMP-IA1B")
        lead_b = create_lead({"organization_or_prospect_name": "B's Lead", "phone": "111"}, profile_b.id, staff_b)
        lead_id = lead_b.id

    force_login(client, app, staff_a)
    resp = client.get(f"/api/operations/v1/leads/{lead_id}")
    assert resp.status_code == 404  # not 403 -- existence itself is not confirmed to an unauthorized actor


def test_employee_a_cannot_read_employee_b_lead_contacts_via_api(app, client, seeded):
    staff_a = make_staff(app, "idorapi2a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "idorapi2b@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.leads.contacts import add_lead_contact
        from app.leads.services import create_lead

        profile_a = _make_profile(app, staff_a, "EMP-IA2A")
        profile_b = _make_profile(app, staff_b, "EMP-IA2B")
        lead_b = create_lead({"organization_or_prospect_name": "B's Lead 2", "phone": "111"}, profile_b.id, staff_b)
        add_lead_contact(lead_b.id, {"name": "Secret Contact"}, staff_b)
        lead_id = lead_b.id

    force_login(client, app, staff_a)
    # Even knowing the parent Lead's real UUID, its child contacts must be unreachable.
    resp = client.get(f"/api/operations/v1/leads/{lead_id}/contacts")
    assert resp.status_code == 404


def test_employee_a_cannot_complete_employee_b_followup_by_uuid(app, client, seeded):
    staff_a = make_staff(app, "idorapi3a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "idorapi3b@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.leads.engagement import create_lead_followup
        from app.leads.services import create_lead
        from app.models.base import utcnow

        profile_a = _make_profile(app, staff_a, "EMP-IA3A")
        profile_b = _make_profile(app, staff_b, "EMP-IA3B")
        lead_b = create_lead({"organization_or_prospect_name": "B's Lead 3", "phone": "111"}, profile_b.id, staff_b)
        followup = create_lead_followup(lead_b.id, {"due_at": utcnow() + timedelta(days=1)}, profile_b.id, staff_b)
        followup_id = followup.id

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/followups/{followup_id}/complete", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 404

    # Confirm it genuinely was not completed -- not just a rejected response
    # with the mutation silently applied anyway.
    with app.app_context():
        from app.extensions import db_session
        from app.models.leads import LeadFollowup

        row = db_session.get(LeadFollowup, followup_id)
        assert row.completed_at is None


def test_employee_a_cannot_verify_employee_b_customer_location(app, client, seeded):
    staff_a = make_staff(app, "idorapi4a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "idorapi4b@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.customers.services import create_customer
        from app.leads.services import capture_location

        profile_a = _make_profile(app, staff_a, "EMP-IA4A")
        profile_b = _make_profile(app, staff_b, "EMP-IA4B")
        customer_b = create_customer({"legal_name": "B's Customer", "assigned_sales_staff_id": staff_b}, staff_b)
        location = capture_location(
            lead_id=None, customer_id=customer_b.id,
            fields={"latitude": "31.9", "longitude": "35.9", "source": "GPS"},
            actor_employee_profile_id=profile_b.id, actor_staff_user_id=staff_b,
        )
        location_id = location.id

    # staff_a lacks customers.verify_location entirely in the SALES role,
    # so this is rejected by the permission decorator itself (403) --
    # confirming the permission gate exists at all, a prerequisite for the
    # ownership check underneath it to ever matter.
    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(
        f"/api/operations/v1/locations/{location_id}/verify",
        json={"reason": "trying to verify someone else's location"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 403


def test_management_view_all_can_read_any_lead(app, client, seeded):
    staff_a = make_staff(app, "idorapi5a@example.com", role_codes=["SALES"])
    staff_mgmt = make_staff(app, "idorapi5mgmt@example.com", role_codes=["VIEWER"])
    with app.app_context():
        from app.leads.services import create_lead

        profile_a = _make_profile(app, staff_a, "EMP-IA5A")
        lead_a = create_lead({"organization_or_prospect_name": "A's Lead", "phone": "111"}, profile_a.id, staff_a)
        lead_id = lead_a.id

    force_login(client, app, staff_mgmt)
    resp = client.get(f"/api/operations/v1/leads/{lead_id}")
    assert resp.status_code == 200
    assert resp.get_json()["organization_or_prospect_name"] == "A's Lead"


def test_lead_list_api_does_not_leak_other_employees_leads_in_total_count(app, client, seeded):
    staff_a = make_staff(app, "idorapi6a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "idorapi6b@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.leads.services import create_lead

        profile_a = _make_profile(app, staff_a, "EMP-IA6A")
        profile_b = _make_profile(app, staff_b, "EMP-IA6B")
        create_lead({"organization_or_prospect_name": "A Lead 1", "phone": "1"}, profile_a.id, staff_a)
        create_lead({"organization_or_prospect_name": "B Lead 1", "phone": "2"}, profile_b.id, staff_b)
        create_lead({"organization_or_prospect_name": "B Lead 2", "phone": "3"}, profile_b.id, staff_b)

    force_login(client, app, staff_a)
    resp = client.get("/api/operations/v1/leads")
    body = resp.get_json()
    assert body["total"] == 1  # not 3 -- the count itself must not include B's leads
    assert all(row["organization_or_prospect_name"] == "A Lead 1" for row in body["rows"])


def test_idempotency_key_reuse_across_different_leads_is_rejected_via_api(app, client, seeded):
    staff_id = make_staff(app, "idorapi7@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.leads.services import create_lead

        profile = _make_profile(app, staff_id, "EMP-IA7")
        lead_a = create_lead({"organization_or_prospect_name": "Conflict A", "phone": "1"}, profile.id, staff_id)
        lead_b = create_lead({"organization_or_prospect_name": "Conflict B", "phone": "2"}, profile.id, staff_id)
        lead_a_id, lead_b_id = lead_a.id, lead_b.id

    force_login(client, app, staff_id)
    csrf = _csrf(client)
    shared_key = str(uuid.uuid4())
    resp1 = client.post(f"/api/operations/v1/leads/{lead_a_id}/convert", json={"idempotency_key": shared_key}, headers={"X-CSRFToken": csrf})
    assert resp1.status_code == 201

    resp2 = client.post(f"/api/operations/v1/leads/{lead_b_id}/convert", json={"idempotency_key": shared_key}, headers={"X-CSRFToken": csrf})
    assert resp2.status_code == 409
    assert resp2.get_json()["error"] == "IDEMPOTENCY_CONFLICT"


def test_xss_payload_in_lead_note_is_escaped_on_render(app, client, seeded):
    """Same class of proof as test_security.py's existing Customer-name
    XSS test, extended to the new Lead note free-text field -- Jinja
    autoescape is the only defense, no |safe filter anywhere in
    leads/detail.html (confirmed by this test actually rendering the page,
    not just inspecting the template source)."""
    staff_id = make_staff(app, "idorapi8@example.com", role_codes=["SALES"])
    payload = "<script>alert(1)</script>"
    with app.app_context():
        from app.leads.services import add_lead_note, create_lead

        profile = _make_profile(app, staff_id, "EMP-IA8")
        lead = create_lead({"organization_or_prospect_name": "XSS Co", "phone": "111"}, profile.id, staff_id)
        add_lead_note(lead, payload, profile.id, staff_id)
        lead_id = lead.id

    force_login(client, app, staff_id)
    resp = client.get(f"/leads/{lead_id}")
    body = resp.get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
