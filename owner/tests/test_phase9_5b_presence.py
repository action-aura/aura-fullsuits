from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import make_staff


def _make_profile(app, staff_id, employee_number):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def _make_staff_session_id(app, staff_id):
    from app.auth.session import create_session
    from app.extensions import db_session
    from app.models.staff import StaffSession, StaffUser
    from app.security.tokens import hash_token
    from sqlalchemy import select

    with app.test_request_context():
        staff = db_session.get(StaffUser, staff_id)
        raw_token = create_session(staff)
    row = db_session.execute(
        select(StaffSession).where(StaffSession.token_hash == hash_token(raw_token))
    ).scalars().first()
    return row.id


def test_no_session_is_offline(app, seeded):
    staff_id = make_staff(app, "pres1@example.com")
    with app.app_context():
        from app.employees.presence import OFFLINE, employee_presence_state

        profile = _make_profile(app, staff_id, "EMP-PRES1")
        assert employee_presence_state(profile.id) == OFFLINE


def test_fresh_heartbeat_is_online(app, seeded):
    staff_id = make_staff(app, "pres2@example.com")
    with app.app_context():
        from app.employees.presence import ONLINE, employee_presence_state
        from app.employees.services import touch_presence
        from app.models.base import utcnow

        profile = _make_profile(app, staff_id, "EMP-PRES2")
        touch_presence(profile.id, staff_session_id=_make_staff_session_id(app, staff_id), app_instance_id="dev-1", platform="WEB")
        assert employee_presence_state(profile.id, as_of=utcnow()) == ONLINE


def test_boundary_thresholds(app, seeded):
    staff_id = make_staff(app, "pres3@example.com")
    with app.app_context():
        from app.employees.presence import ONLINE, OFFLINE, RECENTLY_ACTIVE, employee_presence_state
        from app.employees.services import touch_presence
        from app.models.base import utcnow

        profile = _make_profile(app, staff_id, "EMP-PRES3")
        touch_presence(profile.id, staff_session_id=_make_staff_session_id(app, staff_id), app_instance_id="dev-1", platform="WEB")
        now = utcnow()

        assert employee_presence_state(profile.id, as_of=now + timedelta(seconds=119)) == ONLINE
        assert employee_presence_state(profile.id, as_of=now + timedelta(seconds=121)) == RECENTLY_ACTIVE
        assert employee_presence_state(profile.id, as_of=now + timedelta(seconds=899)) == RECENTLY_ACTIVE
        assert employee_presence_state(profile.id, as_of=now + timedelta(seconds=901)) == OFFLINE


def test_revoked_session_is_offline_even_if_recent(app, seeded):
    staff_id = make_staff(app, "pres4@example.com")
    with app.app_context():
        from app.employees.presence import OFFLINE, employee_presence_state
        from app.employees.services import revoke_presence, touch_presence
        from app.models.base import utcnow

        profile = _make_profile(app, staff_id, "EMP-PRES4")
        session_row = touch_presence(profile.id, staff_session_id=_make_staff_session_id(app, staff_id), app_instance_id="dev-1", platform="WEB")
        revoke_presence(session_row)
        assert employee_presence_state(profile.id, as_of=utcnow()) == OFFLINE


def test_suspension_immediately_forces_offline_not_just_after_15_minutes(app, seeded):
    """Real gap this milestone closed: suspend_employee() now also revokes
    EmployeePresenceSession rows, a separate table from StaffSession."""
    staff_id = make_staff(app, "pres5@example.com")
    with app.app_context():
        from app.employees.presence import OFFLINE, employee_presence_state
        from app.employees.services import suspend_employee, touch_presence
        from app.models.base import utcnow

        profile = _make_profile(app, staff_id, "EMP-PRES5")
        touch_presence(profile.id, staff_session_id=_make_staff_session_id(app, staff_id), app_instance_id="dev-1", platform="WEB")
        suspend_employee(profile, "policy", actor_staff_user_id=staff_id)
        assert employee_presence_state(profile.id, as_of=utcnow()) == OFFLINE


def test_bulk_presence_states_matches_individual_lookups(app, seeded):
    staff_a = make_staff(app, "pres6a@example.com")
    staff_b = make_staff(app, "pres6b@example.com")
    with app.app_context():
        from app.employees.presence import bulk_presence_states, employee_presence_state
        from app.employees.services import touch_presence
        from app.models.base import utcnow

        profile_a = _make_profile(app, staff_a, "EMP-PRES6A")
        profile_b = _make_profile(app, staff_b, "EMP-PRES6B")
        touch_presence(profile_a.id, staff_session_id=_make_staff_session_id(app, staff_a), app_instance_id="dev-1", platform="WEB")

        now = utcnow()
        bulk = bulk_presence_states([profile_a.id, profile_b.id], as_of=now)
        assert bulk[profile_a.id] == employee_presence_state(profile_a.id, as_of=now)
        assert bulk[profile_b.id] == employee_presence_state(profile_b.id, as_of=now)
