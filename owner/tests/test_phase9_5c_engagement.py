from __future__ import annotations

from datetime import date, timedelta

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


def test_log_lead_interaction_creates_row(app, seeded):
    from app.leads.engagement import log_lead_interaction
    from app.leads.services import create_lead

    staff_id = make_staff(app, "eng1@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-E1")
        lead = create_lead({"organization_or_prospect_name": "Eng Co", "phone": "12345"}, profile.id, staff_id)

        interaction = log_lead_interaction(
            lead.id, {"interaction_type": "CALL", "summary": "Discussed pricing."}, profile.id, staff_id
        )
        assert interaction.interaction_type == "CALL"
        assert interaction.lead_id == lead.id


def test_log_interaction_rejects_unknown_type(app, seeded):
    from app.leads.engagement import log_lead_interaction
    from app.leads.errors import LeadError
    from app.leads.services import create_lead

    staff_id = make_staff(app, "eng2@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-E2")
        lead = create_lead({"organization_or_prospect_name": "Eng Co 2", "phone": "12345"}, profile.id, staff_id)

        with pytest.raises(LeadError):
            log_lead_interaction(lead.id, {"interaction_type": "CARRIER_PIGEON"}, profile.id, staff_id)


def test_followup_lifecycle_open_complete_idempotent(app, seeded):
    from app.leads.engagement import complete_lead_followup, create_lead_followup, followup_status
    from app.leads.services import create_lead
    from app.models.base import utcnow

    staff_id = make_staff(app, "eng3@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-E3")
        lead = create_lead({"organization_or_prospect_name": "Eng Co 3", "phone": "12345"}, profile.id, staff_id)

        followup = create_lead_followup(lead.id, {"due_at": utcnow() + timedelta(days=1)}, profile.id, staff_id)
        assert followup_status(followup) == "OPEN"

        complete_lead_followup(followup, staff_id)
        assert followup_status(followup) == "COMPLETED"

        # Idempotent: completing again is a safe no-op, not an error.
        complete_lead_followup(followup, staff_id)
        assert followup_status(followup) == "COMPLETED"


def test_followup_cancel_requires_reason_and_is_terminal(app, seeded):
    from app.leads.engagement import cancel_lead_followup, complete_lead_followup, create_lead_followup, followup_status
    from app.leads.errors import LeadError
    from app.leads.services import create_lead
    from app.models.base import utcnow

    staff_id = make_staff(app, "eng4@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-E4")
        lead = create_lead({"organization_or_prospect_name": "Eng Co 4", "phone": "12345"}, profile.id, staff_id)
        followup = create_lead_followup(lead.id, {"due_at": utcnow() + timedelta(days=1)}, profile.id, staff_id)

        with pytest.raises(LeadError):
            cancel_lead_followup(followup, "", staff_id)

        cancel_lead_followup(followup, "no longer relevant", staff_id)
        assert followup_status(followup) == "CANCELLED"

        # A cancelled follow-up cannot then be completed -- terminal state.
        with pytest.raises(LeadError):
            complete_lead_followup(followup, staff_id)


def test_overdue_is_derived_not_stored(app, seeded):
    from app.leads.engagement import create_lead_followup, is_overdue
    from app.leads.services import create_lead
    from app.models.base import utcnow

    staff_id = make_staff(app, "eng5@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-E5")
        lead = create_lead({"organization_or_prospect_name": "Eng Co 5", "phone": "12345"}, profile.id, staff_id)

        past_due = create_lead_followup(lead.id, {"due_at": utcnow() - timedelta(days=1)}, profile.id, staff_id)
        future_due = create_lead_followup(lead.id, {"due_at": utcnow() + timedelta(days=1)}, profile.id, staff_id)

        assert is_overdue(past_due) is True
        assert is_overdue(future_due) is False
        assert not hasattr(type(past_due), "is_overdue")  # confirms no persisted column/property shortcut exists


def test_due_today_and_overdue_queries_scoped_to_actor(app, seeded):
    from app.leads.engagement import create_lead_followup, list_own_lead_followups_due_today, list_own_lead_followups_overdue
    from app.leads.services import create_lead
    from app.models.base import utcnow

    staff_a = make_staff(app, "eng6a@example.com")
    staff_b = make_staff(app, "eng6b@example.com")
    with app.app_context():
        profile_a = _make_profile(app, staff_a, "EMP-E6A")
        profile_b = _make_profile(app, staff_b, "EMP-E6B")
        lead = create_lead({"organization_or_prospect_name": "Eng Co 6", "phone": "12345"}, profile_a.id, staff_a)

        # Phase 9.5C -- day-boundary-safe AND not-yet-overdue: "due today"
        # is a UTC calendar-day window (day_start <= due_at < day_start + 1
        # day); "overdue" additionally requires due_at < now. A wall-clock-
        # relative offset like "+2 hours" is NOT safe -- it flakes whenever
        # the test runs within 2 hours of UTC midnight (a real, observed
        # failure, root-caused rather than hidden: it silently fell outside
        # today's window). A fixed offset from today's midnight is *also*
        # unsafe on its own -- if that offset already lies in the past
        # relative to "now" (e.g. the test runs late in the UTC day), the
        # row becomes overdue instead of merely due-today, corrupting the
        # very distinction this test checks. The only value safe on both
        # axes, regardless of what time the test runs, is the earlier of
        # "shortly after now" and "shortly before today's end".
        now = utcnow()
        day_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        due_today_at = min(now + timedelta(hours=1), day_end - timedelta(minutes=1))

        create_lead_followup(lead.id, {"due_at": due_today_at, "employee_profile_id": profile_a.id}, profile_a.id, staff_a)
        create_lead_followup(lead.id, {"due_at": utcnow() - timedelta(days=2), "employee_profile_id": profile_a.id}, profile_a.id, staff_a)
        create_lead_followup(lead.id, {"due_at": due_today_at, "employee_profile_id": profile_b.id}, profile_b.id, staff_b)

        due_today_a = list_own_lead_followups_due_today(profile_a.id)
        assert due_today_a["total"] == 1

        overdue_a = list_own_lead_followups_overdue(profile_a.id)
        assert overdue_a["total"] == 1

        due_today_b = list_own_lead_followups_due_today(profile_b.id)
        assert due_today_b["total"] == 1
