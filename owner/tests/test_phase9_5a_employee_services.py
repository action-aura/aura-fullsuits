from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def _make_profile(app, staff_id, employee_number="EMP-001"):
    from app.employees.services import create_employee_profile

    return create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": "Test Employee",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )


def test_create_employee_profile_defaults_to_pending(app, seeded):
    staff_id = make_staff(app, "emp1@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id)
        assert profile.employment_status == "PENDING"
        assert profile.version == 1


def test_update_employee_profile_bumps_version_and_audits(app, seeded):
    staff_id = make_staff(app, "emp2@example.com")
    with app.app_context():
        from app.employees.services import update_employee_profile
        from app.models.audit import AuditLog
        from app.extensions import db_session

        profile = _make_profile(app, staff_id)
        update_employee_profile(profile, {"job_title": "Sales Rep"}, actor_staff_user_id=staff_id)
        assert profile.job_title == "Sales Rep"
        assert profile.version == 2
        row = db_session.query(AuditLog).filter_by(action_code="EMPLOYEE_PROFILE_UPDATED").first()
        assert row is not None
        assert row.entity_public_id == str(profile.id)


def test_suspend_employee_revokes_sessions(app, seeded):
    staff_id = make_staff(app, "emp3@example.com")
    with app.app_context():
        from app.auth.session import create_session, _get_valid_session
        from app.employees.services import suspend_employee

        profile = _make_profile(app, staff_id)
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        with app.test_request_context():
            raw_token = create_session(staff)
        assert _get_valid_session(raw_token) is not None

        suspend_employee(profile, "policy violation", actor_staff_user_id=staff_id)

        assert profile.employment_status == "SUSPENDED"
        assert _get_valid_session(raw_token) is None


def test_terminate_employee_sets_end_date_and_revokes_sessions(app, seeded):
    staff_id = make_staff(app, "emp4@example.com")
    with app.app_context():
        from app.auth.session import create_session, _get_valid_session
        from app.employees.services import terminate_employee
        from app.extensions import db_session
        from app.models.staff import StaffUser

        profile = _make_profile(app, staff_id)
        staff = db_session.get(StaffUser, staff_id)
        with app.test_request_context():
            raw_token = create_session(staff)

        terminate_employee(profile, "resigned", actor_staff_user_id=staff_id)

        assert profile.employment_status == "TERMINATED"
        assert profile.employment_end_date is not None
        assert _get_valid_session(raw_token) is None


def _make_staff_session_id(app, staff_id):
    from app.auth.session import create_session
    from app.extensions import db_session
    from app.models.staff import StaffUser, StaffSession
    from app.security.tokens import hash_token

    with app.test_request_context():
        staff = db_session.get(StaffUser, staff_id)
        raw_token = create_session(staff)
    row = db_session.execute(
        __import__("sqlalchemy").select(StaffSession).where(StaffSession.token_hash == hash_token(raw_token))
    ).scalars().first()
    return row.id


def test_touch_presence_upserts_by_app_instance(app, seeded):
    staff_id = make_staff(app, "emp5@example.com")
    with app.app_context():
        from app.employees.services import touch_presence
        from app.extensions import db_session
        from app.models.employees import EmployeePresenceSession

        profile = _make_profile(app, staff_id)
        staff_session_id = _make_staff_session_id(app, staff_id)
        first = touch_presence(profile.id, staff_session_id=staff_session_id, app_instance_id="device-1", platform="ANDROID")
        second = touch_presence(profile.id, staff_session_id=staff_session_id, app_instance_id="device-1", platform="ANDROID")

        assert first.id == second.id
        assert db_session.query(EmployeePresenceSession).count() == 1


def test_revoke_presence_marks_revoked(app, seeded):
    staff_id = make_staff(app, "emp6@example.com")
    with app.app_context():
        from app.employees.services import revoke_presence, touch_presence

        profile = _make_profile(app, staff_id)
        staff_session_id = _make_staff_session_id(app, staff_id)
        session_row = touch_presence(profile.id, staff_session_id=staff_session_id, app_instance_id="device-2", platform="IOS")
        revoke_presence(session_row)
        assert session_row.revoked_at is not None
