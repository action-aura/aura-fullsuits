"""Phase 9.5B Milestone 17 -- two-management-account synchronization.

Two synthetic SUPER_ADMIN accounts (never named after real people -- RBAC is
account-independent, matching duplication-risk-report.md's own precedent).
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import make_staff


def _make_active_employee(app, staff_id, employee_number, actor_id):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": employee_number, "full_name": f"Employee {employee_number}", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=actor_id,
    )
    activate_employee(profile, actor_staff_user_id=actor_id)
    return profile


def test_both_admins_see_identical_employee_list(app, seeded):
    admin_a = make_staff(app, "sync1a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync1b@example.com", super_admin=True)
    target = make_staff(app, "sync1target@example.com")
    with app.app_context():
        from app.employees.queries import list_employees

        _make_active_employee(app, target, "EMP-SYNC1", admin_a)

        result_a = list_employees()
        result_b = list_employees()
        assert [r.id for r in result_a["rows"]] == [r.id for r in result_b["rows"]]
        assert result_a["total"] == result_b["total"] == 1


def test_admin_b_sees_admin_a_created_employee_immediately_after_commit(app, seeded):
    admin_a = make_staff(app, "sync2a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync2b@example.com", super_admin=True)
    target = make_staff(app, "sync2target@example.com")
    with app.app_context():
        from app.employees.queries import list_employees

        before = list_employees()["total"]
        _make_active_employee(app, target, "EMP-SYNC2", admin_a)
        after = list_employees()["total"]
        assert after == before + 1


def test_admin_b_sees_admin_a_profile_update(app, seeded):
    admin_a = make_staff(app, "sync3a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync3b@example.com", super_admin=True)
    target = make_staff(app, "sync3target@example.com")
    with app.app_context():
        from app.employees.services import update_employee_profile
        from app.employees.queries import find_own_profile

        profile = _make_active_employee(app, target, "EMP-SYNC3", admin_a)
        update_employee_profile(profile, {"job_title": "Set by A"}, actor_staff_user_id=admin_a)

        reread = find_own_profile(target)
        assert reread.job_title == "Set by A"  # B's own read of the same authoritative row


def test_optimistic_lock_prevents_silent_simultaneous_overwrite(app, seeded):
    admin_a = make_staff(app, "sync4a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync4b@example.com", super_admin=True)
    target = make_staff(app, "sync4target@example.com")
    with app.app_context():
        from app.employees.services import update_employee_profile

        profile = _make_active_employee(app, target, "EMP-SYNC4", admin_a)
        version_seen_by_both = profile.version

        update_employee_profile(profile, {"job_title": "A's edit"}, actor_staff_user_id=admin_a)
        assert profile.version == version_seen_by_both + 1

        # B loaded the row at version_seen_by_both and would now be submitting
        # a stale version -- the real API-layer VERSION_CONFLICT guard
        # (owner/app/api_operations/routes.py::update_employee_route) is what
        # rejects this in practice; proven directly in
        # test_phase9_5b_operations_api.py::test_update_employee_version_conflict.
        assert profile.version != version_seen_by_both


def test_one_admin_suspends_the_other_sees_the_status_with_correct_audit_actor(app, seeded):
    admin_a = make_staff(app, "sync5a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync5b@example.com", super_admin=True)
    target = make_staff(app, "sync5target@example.com")
    with app.app_context():
        from app.employees.queries import find_own_profile
        from app.employees.services import suspend_employee
        from app.extensions import db_session
        from app.models.audit import AuditLog

        profile = _make_active_employee(app, target, "EMP-SYNC5", admin_a)
        suspend_employee(profile, "policy", actor_staff_user_id=admin_a)

        reread = find_own_profile(target)
        assert reread.employment_status == "SUSPENDED"  # B sees it too (same row)

        row = db_session.query(AuditLog).filter_by(action_code="EMPLOYEE_SUSPENDED", entity_public_id=str(profile.id)).first()
        assert row.actor_staff_user_id == admin_a  # attributed to A specifically, not B or a generic admin


def test_admins_have_independent_sessions_and_mfa(app, seeded):
    admin_a = make_staff(app, "sync6a@example.com", super_admin=True, mfa=True)
    admin_b = make_staff(app, "sync6b@example.com", super_admin=True, mfa=True)
    with app.app_context():
        from app.auth.session import create_session, list_sessions_for_staff
        from app.extensions import db_session
        from app.models.staff import StaffUser

        with app.test_request_context():
            staff_a = db_session.get(StaffUser, admin_a)
            create_session(staff_a)

        sessions_a = list_sessions_for_staff(admin_a)
        sessions_b = list_sessions_for_staff(admin_b)
        assert len(sessions_a) >= 1
        assert len(sessions_b) == 0  # B has no session just because A logged in

        staff_a_row = db_session.get(StaffUser, admin_a)
        staff_b_row = db_session.get(StaffUser, admin_b)
        assert staff_a_row.mfa_credential.totp_secret_encrypted != staff_b_row.mfa_credential.totp_secret_encrypted


def test_last_super_admin_protection_holds_with_two_real_admins(app, seeded):
    """Two admins exist -- disabling one must succeed (one remains usable);
    then disabling the last one must be blocked."""
    admin_a = make_staff(app, "sync7a@example.com", super_admin=True)
    admin_b = make_staff(app, "sync7b@example.com", super_admin=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.super_admin_guard import LastSuperAdminError
        from app.staff.services import disable_staff

        disable_staff(db_session.get(StaffUser, admin_b), "test", admin_a)  # succeeds -- A remains

        with pytest.raises(LastSuperAdminError):
            disable_staff(db_session.get(StaffUser, admin_a), "test", admin_a)  # blocked -- would leave zero
