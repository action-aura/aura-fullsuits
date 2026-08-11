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


def test_pending_to_active_to_suspended_to_active_to_terminated_to_archived(app, seeded):
    staff_id = make_staff(app, "life1@example.com")
    with app.app_context():
        from app.employees.services import activate_employee, archive_employee, reactivate_employee, suspend_employee, terminate_employee

        profile = _make_profile(app, staff_id, "EMP-LIFE1")
        assert profile.employment_status == "PENDING"

        activate_employee(profile, actor_staff_user_id=staff_id)
        assert profile.employment_status == "ACTIVE"

        suspend_employee(profile, "policy check", actor_staff_user_id=staff_id)
        assert profile.employment_status == "SUSPENDED"

        reactivate_employee(profile, actor_staff_user_id=staff_id)
        assert profile.employment_status == "ACTIVE"

        terminate_employee(profile, "resigned", actor_staff_user_id=staff_id)
        assert profile.employment_status == "TERMINATED"

        archive_employee(profile, actor_staff_user_id=staff_id)
        assert profile.employment_status == "ARCHIVED"
        assert profile.archived_at is not None


def test_terminated_to_active_rejected_no_rehire_policy(app, seeded):
    staff_id = make_staff(app, "life2@example.com")
    with app.app_context():
        from app.employees.services import InvalidEmploymentTransitionError, activate_employee, terminate_employee

        profile = _make_profile(app, staff_id, "EMP-LIFE2")
        activate_employee(profile, actor_staff_user_id=staff_id)
        terminate_employee(profile, "resigned", actor_staff_user_id=staff_id)

        with pytest.raises(InvalidEmploymentTransitionError):
            activate_employee(profile, actor_staff_user_id=staff_id)


def test_archived_is_terminal(app, seeded):
    staff_id = make_staff(app, "life3@example.com")
    with app.app_context():
        from app.employees.services import (
            InvalidEmploymentTransitionError, activate_employee, archive_employee, suspend_employee, terminate_employee,
        )

        profile = _make_profile(app, staff_id, "EMP-LIFE3")
        activate_employee(profile, actor_staff_user_id=staff_id)
        terminate_employee(profile, "resigned", actor_staff_user_id=staff_id)
        archive_employee(profile, actor_staff_user_id=staff_id)

        with pytest.raises(InvalidEmploymentTransitionError):
            suspend_employee(profile, "x", actor_staff_user_id=staff_id)


def test_suspension_blocks_new_login_not_just_existing_sessions(app, seeded):
    staff_id = make_staff(app, "life4@example.com")
    with app.app_context():
        from app.employees.services import activate_employee, suspend_employee
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile = _make_profile(app, staff_id, "EMP-LIFE4")
        activate_employee(profile, actor_staff_user_id=staff_id)
        suspend_employee(profile, "x", actor_staff_user_id=staff_id)

        staff = db_session.get(StaffUser, staff_id)
        assert staff.is_active is False  # the real gate authenticate() checks


def test_reactivate_restores_login_unless_independently_disabled(app, seeded):
    staff_id = make_staff(app, "life5@example.com")
    with app.app_context():
        from app.employees.services import activate_employee, reactivate_employee, suspend_employee
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile = _make_profile(app, staff_id, "EMP-LIFE5")
        activate_employee(profile, actor_staff_user_id=staff_id)
        suspend_employee(profile, "x", actor_staff_user_id=staff_id)
        reactivate_employee(profile, actor_staff_user_id=staff_id)

        staff = db_session.get(StaffUser, staff_id)
        assert staff.is_active is True


def test_reactivate_does_not_override_independent_disable(app, seeded):
    staff_id = make_staff(app, "life6@example.com")
    admin_id = make_staff(app, "life6admin@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, reactivate_employee, suspend_employee
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.staff.services import disable_staff

        profile = _make_profile(app, staff_id, "EMP-LIFE6")
        activate_employee(profile, actor_staff_user_id=staff_id)
        suspend_employee(profile, "x", actor_staff_user_id=staff_id)
        disable_staff(db_session.get(StaffUser, staff_id), "separate security concern", admin_id)

        reactivate_employee(profile, actor_staff_user_id=admin_id)

        assert profile.employment_status == "ACTIVE"
        staff = db_session.get(StaffUser, staff_id)
        assert staff.is_active is False  # independent disable still holds
        assert staff.disabled_at is not None


def test_last_super_admin_cannot_be_disabled(app, seeded):
    admin_id = make_staff(app, "life7admin@example.com", super_admin=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.super_admin_guard import LastSuperAdminError
        from app.staff.services import disable_staff

        with pytest.raises(LastSuperAdminError):
            disable_staff(db_session.get(StaffUser, admin_id), "test", admin_id)


def test_second_super_admin_can_be_disabled_when_one_remains(app, seeded):
    admin_a = make_staff(app, "life8a@example.com", super_admin=True)
    admin_b = make_staff(app, "life8b@example.com", super_admin=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.staff.services import disable_staff

        disable_staff(db_session.get(StaffUser, admin_b), "test", admin_a)  # must not raise
        staff_b = db_session.get(StaffUser, admin_b)
        assert staff_b.is_active is False


def test_last_super_admin_cannot_be_suspended_via_employee_lifecycle(app, seeded):
    admin_id = make_staff(app, "life9admin@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile, suspend_employee
        from app.security.super_admin_guard import LastSuperAdminError

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-LIFE9", "full_name": "Admin Nine", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)

        with pytest.raises(LastSuperAdminError):
            suspend_employee(profile, "x", actor_staff_user_id=admin_id)


def test_non_super_admin_can_always_be_suspended(app, seeded):
    """The guard only fires for Super Admin accounts -- a normal SALES
    employee suspension is never blocked by super-admin logic."""
    staff_id = make_staff(app, "life10@example.com")
    with app.app_context():
        from app.employees.services import activate_employee, suspend_employee

        profile = _make_profile(app, staff_id, "EMP-LIFE10")
        activate_employee(profile, actor_staff_user_id=staff_id)
        suspend_employee(profile, "x", actor_staff_user_id=staff_id)  # must not raise
        assert profile.employment_status == "SUSPENDED"
