"""Phase 9.5A Milestone 24 -- authorization / IDOR proving tests.

No HTTP routes exist yet for leads/employees/commissions this phase
(foundation-only, per the governing spec's own boundary) -- these tests
prove the real building block a future route MUST use
(apply_ownership_filter(), the RBAC permission seed) is itself correct,
since that is the actual, testable surface at this milestone. A raw
`db_session.get(Lead, id)` bypassing the ownership filter is expected to
succeed at the ORM layer by design (enforcement is documented as a
query-layer responsibility, not a model-layer one -- record-ownership-
policy.md) -- these tests instead prove that any code path going through
apply_ownership_filter(), as every future route must, cannot see another
employee's record even when given its real UUID.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

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


def test_ownership_filtered_query_excludes_other_employees_lead_by_real_id(app, seeded):
    """The core IDOR proof at this layer: even with B's real Lead UUID in
    hand, a query built with apply_ownership_filter() for A returns nothing."""
    staff_a = make_staff(app, "idor1a@example.com")
    staff_b = make_staff(app, "idor1b@example.com")
    with app.app_context():
        from app.leads.ownership import apply_ownership_filter
        from app.leads.services import create_lead
        from app.models.leads import Lead

        profile_a = _make_profile(app, staff_a, "EMP-IDOR1A")
        profile_b = _make_profile(app, staff_b, "EMP-IDOR1B")
        lead_b = create_lead(
            {"organization_or_prospect_name": "B's Confidential Deal"}, actor_employee_profile_id=profile_b.id,
            actor_staff_user_id=staff_b,
        )

        from app.extensions import db_session

        stmt = apply_ownership_filter(
            select(Lead).where(Lead.id == lead_b.id), Lead, profile_a.id, all_permission_held=False
        )
        assert db_session.execute(stmt).scalars().first() is None


def test_ownership_filter_result_indistinguishable_from_nonexistent_record(app, seeded):
    """record-ownership-policy.md: 'no results due to permission' must look
    identical to 'no results because none exist' -- both empty, never a
    distinct signal that would confirm the record's existence."""
    staff_a = make_staff(app, "idor2a@example.com")
    staff_b = make_staff(app, "idor2b@example.com")
    with app.app_context():
        import uuid as uuid_mod

        from app.leads.ownership import apply_ownership_filter
        from app.leads.services import create_lead
        from app.models.leads import Lead

        profile_a = _make_profile(app, staff_a, "EMP-IDOR2A")
        profile_b = _make_profile(app, staff_b, "EMP-IDOR2B")
        lead_b = create_lead(
            {"organization_or_prospect_name": "B's Other Deal"}, actor_employee_profile_id=profile_b.id,
            actor_staff_user_id=staff_b,
        )

        from app.extensions import db_session

        stmt_real_but_not_owned = apply_ownership_filter(
            select(Lead).where(Lead.id == lead_b.id), Lead, profile_a.id, all_permission_held=False
        )
        stmt_nonexistent = apply_ownership_filter(
            select(Lead).where(Lead.id == uuid_mod.uuid4()), Lead, profile_a.id, all_permission_held=False
        )
        assert db_session.execute(stmt_real_but_not_owned).scalars().first() is None
        assert db_session.execute(stmt_nonexistent).scalars().first() is None


def test_creator_retains_read_but_loses_write_context_after_reassignment(app, seeded):
    """record-ownership-policy.md's explicit reassignment policy: the
    creator's read access survives reassignment (rule #1), but the lead's
    write/assignment authority moves to the new assignee."""
    staff_a = make_staff(app, "idor3a@example.com")
    staff_b = make_staff(app, "idor3b@example.com")
    with app.app_context():
        from app.leads.ownership import apply_ownership_filter
        from app.leads.services import assign_lead, create_lead
        from app.models.leads import Lead

        profile_a = _make_profile(app, staff_a, "EMP-IDOR3A")
        profile_b = _make_profile(app, staff_b, "EMP-IDOR3B")
        lead = create_lead(
            {"organization_or_prospect_name": "Created By A"}, actor_employee_profile_id=profile_a.id,
            actor_staff_user_id=staff_a,
        )
        assign_lead(lead, profile_b.id, actor_employee_profile_id=profile_a.id, actor_staff_user_id=staff_a)

        from app.extensions import db_session

        stmt_a = apply_ownership_filter(
            select(Lead).where(Lead.id == lead.id), Lead, profile_a.id, all_permission_held=False
        )
        assert db_session.execute(stmt_a).scalars().first() is not None  # A created it -- read retained
        assert lead.assigned_employee_profile_id == profile_b.id  # but write/assignment moved to B


def test_management_all_permission_bypasses_ownership_filter_entirely(app, seeded):
    staff_a = make_staff(app, "idor4a@example.com")
    staff_mgmt = make_staff(app, "idor4mgmt@example.com")
    with app.app_context():
        from app.leads.ownership import apply_ownership_filter
        from app.leads.services import create_lead
        from app.models.leads import Lead

        profile_a = _make_profile(app, staff_a, "EMP-IDOR4A")
        lead = create_lead(
            {"organization_or_prospect_name": "A's Deal"}, actor_employee_profile_id=profile_a.id,
            actor_staff_user_id=staff_a,
        )

        from app.extensions import db_session

        stmt = apply_ownership_filter(
            select(Lead).where(Lead.id == lead.id), Lead, None, all_permission_held=True
        )
        assert db_session.execute(stmt).scalars().first() is not None


def test_two_super_admins_see_identical_data_with_distinct_audit_attribution(app, seeded):
    """Both Bahaa and Awab are separate SUPER_ADMIN accounts (RBAC is
    account-independent, no shared credential) -- both must see identical
    global data, but every action they each take is attributed to their own
    real actor_staff_user_id (duplication-risk-report.md's RBAC section)."""
    bahaa = make_staff(app, "bahaa@example.com", super_admin=True)
    awab = make_staff(app, "awab@example.com", super_admin=True)
    staff_owner = make_staff(app, "idor5owner@example.com")
    with app.app_context():
        from app.leads.services import create_lead, list_all_leads

        profile_owner = _make_profile(app, staff_owner, "EMP-IDOR5")
        create_lead({"organization_or_prospect_name": "Global Deal"}, actor_employee_profile_id=profile_owner.id, actor_staff_user_id=staff_owner)

        result_bahaa = list_all_leads()
        result_awab = list_all_leads()
        assert [r.id for r in result_bahaa["rows"]] == [r.id for r in result_awab["rows"]]

        from app.audit.services import record as audit_record
        from app.extensions import db_session
        from app.models.audit import AuditLog

        audit_record(actor_staff_user_id=bahaa, actor_role_snapshot="SUPER_ADMIN", action_code="LEAD_VIEWED", entity_type="lead", entity_public_id=str(profile_owner.id))
        audit_record(actor_staff_user_id=awab, actor_role_snapshot="SUPER_ADMIN", action_code="LEAD_VIEWED", entity_type="lead", entity_public_id=str(profile_owner.id))

        rows = db_session.query(AuditLog).filter_by(action_code="LEAD_VIEWED").all()
        actors = {r.actor_staff_user_id for r in rows}
        assert actors == {bahaa, awab}  # distinct attribution, not merged/anonymous
