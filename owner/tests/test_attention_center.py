"""Attention Center service tests (Stage F).

Focused on the two things the governing task explicitly calls out as
non-optional: (1) ownership scoping must exactly mirror the real
`_own`/`_all` permission-pair rule already enforced by the real list
routes -- getting this wrong would let one employee see another's
assigned records, which the spec explicitly prohibits -- and (2) a
category must not appear at all for an actor who holds none of the
permissions that govern it (real per-category gating, not
compute-then-hide).
"""
from __future__ import annotations

from datetime import date, timedelta

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


def _make_overdue_lead_followup(app, staff_id, profile_id, *, org_name, overdue_by: timedelta):
    from app.leads.engagement import create_lead_followup
    from app.leads.services import create_lead
    from app.models.base import utcnow

    lead = create_lead(
        {"organization_or_prospect_name": org_name}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id
    )
    followup = create_lead_followup(
        lead.id,
        {"due_at": utcnow() - overdue_by, "employee_profile_id": profile_id},
        actor_employee_profile_id=profile_id,
        actor_staff_user_id=staff_id,
    )
    return lead, followup


def test_own_permission_excludes_other_employees_followup(app, seeded):
    """The real IDOR-shaped proof for this new module: staff A holding
    only leads.view_own must never see staff B's overdue follow-up, even
    though both exist in the same table and B's row is a real, valid,
    currently-overdue record."""
    staff_a = make_staff(app, "attn-a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "attn-b@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.attention.service import get_attention_items
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-ATTN-A")
        profile_b = _make_profile(app, staff_b, "EMP-ATTN-B")
        _make_overdue_lead_followup(app, staff_a, profile_a.id, org_name="A's Deal", overdue_by=timedelta(days=1))
        _make_overdue_lead_followup(app, staff_b, profile_b.id, org_name="B's Confidential Deal", overdue_by=timedelta(days=1))

        actor_a = db_session.get(StaffUser, staff_a)
        items = get_attention_items(actor_a)
        followup_items = [i for i in items if i.category == "overdue_followups"]

        titles = " ".join(i.title for i in followup_items)
        assert "A's Deal" in titles
        assert "B's Confidential Deal" not in titles


def test_view_all_permission_sees_every_employees_followup(app, seeded):
    """VIEWER holds leads.view_all -- the real company-wide bypass, same
    rule apply_ownership_filter() already applies everywhere else."""
    staff_a = make_staff(app, "attn-c@example.com", role_codes=["SALES"])
    viewer = make_staff(app, "attn-viewer@example.com", role_codes=["VIEWER"])
    with app.test_request_context():
        from app.attention.service import get_attention_items
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-ATTN-C")
        _make_overdue_lead_followup(app, staff_a, profile_a.id, org_name="Company-Wide Visible Deal", overdue_by=timedelta(days=1))

        actor_viewer = db_session.get(StaffUser, viewer)
        items = get_attention_items(actor_viewer)
        followup_items = [i for i in items if i.category == "overdue_followups"]
        assert any("Company-Wide Visible Deal" in i.title for i in followup_items)


def test_staff_with_no_relevant_permission_gets_no_items(app, seeded):
    """A staff account with zero Attention Center permissions must get a
    real empty list -- no category is ever computed for a permission the
    actor doesn't hold (see ATTENTION_CATEGORY_PERMISSIONS)."""
    bystander = make_staff(app, "attn-bystander@example.com", role_codes=[])
    with app.test_request_context():
        from app.attention.service import get_attention_items
        from app.extensions import db_session
        from app.models.staff import StaffUser

        actor = db_session.get(StaffUser, bystander)
        assert get_attention_items(actor) == []


def test_overdue_followup_priority_reflects_how_overdue_it_is(app, seeded):
    """Priority is derived from real elapsed time, not an arbitrary
    per-item score: a follow-up overdue by several days must outrank one
    overdue by a few minutes."""
    staff_a = make_staff(app, "attn-priority@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.attention.service import PRIORITY_LOW, PRIORITY_URGENT, get_attention_items
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-ATTN-PRI")
        _make_overdue_lead_followup(app, staff_a, profile_a.id, org_name="Very Overdue Deal", overdue_by=timedelta(days=5))
        _make_overdue_lead_followup(app, staff_a, profile_a.id, org_name="Barely Overdue Deal", overdue_by=timedelta(minutes=5))

        actor = db_session.get(StaffUser, staff_a)
        items = {i.title: i.priority for i in get_attention_items(actor) if i.category == "overdue_followups"}

        assert items["Follow-up overdue: Very Overdue Deal"] == PRIORITY_URGENT
        assert items["Follow-up overdue: Barely Overdue Deal"] == PRIORITY_LOW


def test_completed_followup_no_longer_appears(app, seeded):
    """Proves the "derived, not persisted read-state" design directly: an
    item's only lifecycle is its underlying real record's lifecycle --
    completing the follow-up removes it from the very next computation,
    with nothing to explicitly "dismiss" or "mark read"."""
    staff_a = make_staff(app, "attn-complete@example.com", role_codes=["SALES"])
    with app.test_request_context():
        from app.attention.service import get_attention_items
        from app.extensions import db_session
        from app.leads.engagement import complete_lead_followup
        from app.models.staff import StaffUser

        profile_a = _make_profile(app, staff_a, "EMP-ATTN-COMPLETE")
        _lead, followup = _make_overdue_lead_followup(
            app, staff_a, profile_a.id, org_name="Soon Completed Deal", overdue_by=timedelta(days=1)
        )
        actor = db_session.get(StaffUser, staff_a)
        before = [i for i in get_attention_items(actor) if i.category == "overdue_followups"]
        assert any("Soon Completed Deal" in i.title for i in before)

        complete_lead_followup(followup, staff_a)

        after = [i for i in get_attention_items(actor) if i.category == "overdue_followups"]
        assert not any("Soon Completed Deal" in i.title for i in after)


def test_attention_page_requires_login_only(app, client, seeded):
    """The page itself needs no single permission (it's a personalized
    aggregation, not one screen behind one gate) -- an unauthenticated
    request is redirected to login, same as any other real-login route."""
    response = client.get("/attention")
    assert response.status_code in (302, 401)


def test_attention_page_renders_real_empty_state_for_permitted_staff(app, client, seeded):
    staff_id = make_staff(app, "attn-page@example.com", role_codes=[])
    from tests.conftest import force_login

    force_login(client, app, staff_id)
    response = client.get("/attention")
    assert response.status_code == 200
    assert b"Nothing needs your attention" in response.data
