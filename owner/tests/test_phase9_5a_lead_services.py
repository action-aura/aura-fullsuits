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


def test_create_lead_writes_initial_status_history(app, seeded):
    staff_id = make_staff(app, "lead1@example.com")
    with app.app_context():
        from app.leads.services import create_lead
        from app.extensions import db_session
        from app.models.leads import LeadStatusHistory

        profile = _make_profile(app, staff_id, "EMP-L1")
        lead = create_lead(
            {"organization_or_prospect_name": "Acme Prospect"}, actor_employee_profile_id=profile.id,
            actor_staff_user_id=staff_id,
        )
        assert lead.status == "NEW"
        history = db_session.query(LeadStatusHistory).filter_by(lead_id=lead.id).all()
        assert len(history) == 1
        assert history[0].from_status is None
        assert history[0].to_status == "NEW"


def test_own_employee_sees_only_own_lead(app, seeded):
    staff_a = make_staff(app, "lead2a@example.com")
    staff_b = make_staff(app, "lead2b@example.com")
    with app.app_context():
        from app.leads.services import create_lead, list_own_leads

        profile_a = _make_profile(app, staff_a, "EMP-L2A")
        profile_b = _make_profile(app, staff_b, "EMP-L2B")
        create_lead({"organization_or_prospect_name": "A's Lead"}, actor_employee_profile_id=profile_a.id, actor_staff_user_id=staff_a)
        create_lead({"organization_or_prospect_name": "B's Lead"}, actor_employee_profile_id=profile_b.id, actor_staff_user_id=staff_b)

        result_a = list_own_leads(profile_a.id)
        assert result_a["total"] == 1
        assert result_a["rows"][0].organization_or_prospect_name == "A's Lead"


def test_management_list_all_sees_every_lead(app, seeded):
    staff_a = make_staff(app, "lead3a@example.com")
    staff_b = make_staff(app, "lead3b@example.com")
    with app.app_context():
        from app.leads.services import create_lead, list_all_leads

        profile_a = _make_profile(app, staff_a, "EMP-L3A")
        profile_b = _make_profile(app, staff_b, "EMP-L3B")
        create_lead({"organization_or_prospect_name": "A's Lead"}, actor_employee_profile_id=profile_a.id, actor_staff_user_id=staff_a)
        create_lead({"organization_or_prospect_name": "B's Lead"}, actor_employee_profile_id=profile_b.id, actor_staff_user_id=staff_b)

        result = list_all_leads()
        assert result["total"] == 2


def test_change_lead_status_appends_history_row(app, seeded):
    staff_id = make_staff(app, "lead4@example.com")
    with app.app_context():
        from app.leads.services import change_lead_status, create_lead
        from app.extensions import db_session
        from app.models.leads import LeadStatusHistory

        profile = _make_profile(app, staff_id, "EMP-L4")
        lead = create_lead({"organization_or_prospect_name": "Status Co"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id)
        # Phase 9.5C Milestone 2 -- change_lead_status() now enforces the real
        # transition matrix (docs/owner/phase9_5c/lead-transition-matrix.md);
        # NEW can only advance one step to POTENTIAL, never straight to
        # QUALIFIED. This test verifies history-row appending, not the full
        # chain, so a single valid transition is the correct, non-weakened fix.
        change_lead_status(lead, "POTENTIAL", actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id, reason="good fit")

        assert lead.status == "POTENTIAL"
        history = db_session.query(LeadStatusHistory).filter_by(lead_id=lead.id).order_by(LeadStatusHistory.changed_at).all()
        assert [h.to_status for h in history] == ["NEW", "POTENTIAL"]


def test_reassign_lead_closes_old_assignment_opens_new(app, seeded):
    staff_a = make_staff(app, "lead5a@example.com")
    staff_b = make_staff(app, "lead5b@example.com")
    with app.app_context():
        from app.leads.services import assign_lead, create_lead
        from app.extensions import db_session
        from app.models.leads import LeadAssignment

        from app.employees.services import activate_employee

        profile_a = _make_profile(app, staff_a, "EMP-L5A")
        profile_b = _make_profile(app, staff_b, "EMP-L5B")
        # Phase 9.5C Milestone 4 -- create_lead() now validates an inline
        # assigned_employee_profile_id is ACTIVE.
        activate_employee(profile_a, actor_staff_user_id=staff_a)
        # Phase 9.5C Milestone 3 -- assign_lead() now rejects an inactive
        # destination employee (a fresh profile defaults to PENDING); real
        # reassignment targets must be ACTIVE.
        activate_employee(profile_b, actor_staff_user_id=staff_a)
        lead = create_lead(
            {"organization_or_prospect_name": "Reassign Co", "assigned_employee_profile_id": profile_a.id},
            actor_employee_profile_id=profile_a.id, actor_staff_user_id=staff_a,
        )

        # reason is now required when closing an existing open assignment.
        assign_lead(lead, profile_b.id, actor_employee_profile_id=profile_a.id, actor_staff_user_id=staff_a, reason="workload rebalance")

        assert lead.assigned_employee_profile_id == profile_b.id
        rows = db_session.query(LeadAssignment).filter_by(lead_id=lead.id).order_by(LeadAssignment.assigned_at).all()
        assert len(rows) == 2
        assert rows[0].unassigned_at is not None
        assert rows[1].unassigned_at is None
        assert rows[1].assigned_to_employee_profile_id == profile_b.id


def test_capture_location_requires_exactly_one_owner(app, seeded):
    staff_id = make_staff(app, "lead6@example.com")
    with app.app_context():
        from app.leads.services import capture_location, create_lead

        profile = _make_profile(app, staff_id, "EMP-L6")
        lead = create_lead({"organization_or_prospect_name": "Loc Co"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id)

        with pytest.raises(ValueError):
            capture_location(
                lead_id=None, customer_id=None, fields={"source": "MANUAL"},
                actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
            )
        with pytest.raises(ValueError):
            capture_location(
                lead_id=lead.id, customer_id="not-none", fields={"source": "MANUAL"},
                actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
            )

        location = capture_location(
            lead_id=lead.id, customer_id=None, fields={"source": "MANUAL", "manual_address": "123 Main St"},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        assert location.lead_id == lead.id
        assert location.customer_id is None
        assert location.verified is False
