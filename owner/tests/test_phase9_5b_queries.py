from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def _make_active_profile(app, staff_id, employee_number, **extra):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
            **extra,
        },
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def test_list_employees_excludes_archived_by_default(app, seeded):
    staff_a = make_staff(app, "q1a@example.com")
    staff_b = make_staff(app, "q1b@example.com")
    with app.app_context():
        from app.employees.queries import list_employees
        from app.employees.services import archive_employee, terminate_employee

        _make_active_profile(app, staff_a, "EMP-Q1A")
        profile_b = _make_active_profile(app, staff_b, "EMP-Q1B")
        terminate_employee(profile_b, "x", actor_staff_user_id=staff_a)
        archive_employee(profile_b, actor_staff_user_id=staff_a)

        result = list_employees()
        numbers = {r.employee_number for r in result["rows"]}
        assert "EMP-Q1A" in numbers
        assert "EMP-Q1B" not in numbers

        result_incl = list_employees(include_archived=True)
        numbers_incl = {r.employee_number for r in result_incl["rows"]}
        assert "EMP-Q1B" in numbers_incl


def test_search_matches_name_or_employee_number(app, seeded):
    staff_id = make_staff(app, "q2@example.com")
    with app.app_context():
        from app.employees.queries import list_employees
        from app.employees.services import update_employee_profile

        profile = _make_active_profile(app, staff_id, "EMP-Q2")
        update_employee_profile(profile, {"full_name": "Zanzibar Search Target"}, actor_staff_user_id=staff_id)

        by_name = list_employees(search="Zanzibar")
        assert any(r.id == profile.id for r in by_name["rows"])
        by_number = list_employees(search="EMP-Q2")
        assert any(r.id == profile.id for r in by_number["rows"])


def test_department_filter(app, seeded):
    staff_a = make_staff(app, "q3a@example.com")
    staff_b = make_staff(app, "q3b@example.com")
    with app.app_context():
        from app.employees.queries import list_employees

        _make_active_profile(app, staff_a, "EMP-Q3A", department="Sales")
        _make_active_profile(app, staff_b, "EMP-Q3B", department="Support")

        result = list_employees(department="Sales")
        numbers = {r.employee_number for r in result["rows"]}
        assert numbers == {"EMP-Q3A"}


def test_role_filter(app, seeded):
    staff_a = make_staff(app, "q4a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "q4b@example.com", role_codes=["SUPPORT"])
    with app.app_context():
        from app.employees.queries import list_employees

        _make_active_profile(app, staff_a, "EMP-Q4A")
        _make_active_profile(app, staff_b, "EMP-Q4B")

        result = list_employees(role_code="SALES")
        numbers = {r.employee_number for r in result["rows"]}
        assert "EMP-Q4A" in numbers
        assert "EMP-Q4B" not in numbers


def test_presence_filter_matches_derivation_exactly(app, seeded):
    staff_a = make_staff(app, "q5a@example.com")
    staff_b = make_staff(app, "q5b@example.com")
    with app.app_context():
        from app.auth.session import create_session
        from app.employees.presence import employee_presence_state
        from app.employees.queries import list_employees
        from app.employees.services import touch_presence
        from app.extensions import db_session
        from app.models.staff import StaffSession, StaffUser
        from app.security.tokens import hash_token
        from sqlalchemy import select

        profile_a = _make_active_profile(app, staff_a, "EMP-Q5A")
        _make_active_profile(app, staff_b, "EMP-Q5B")

        with app.test_request_context():
            staff = db_session.get(StaffUser, staff_a)
            raw_token = create_session(staff)
        session_row = db_session.execute(select(StaffSession).where(StaffSession.token_hash == hash_token(raw_token))).scalars().first()
        touch_presence(profile_a.id, staff_session_id=session_row.id, app_instance_id="dev-1", platform="WEB")

        assert employee_presence_state(profile_a.id) == "ONLINE"

        online_result = list_employees(presence="ONLINE")
        online_numbers = {r.employee_number for r in online_result["rows"]}
        assert online_numbers == {"EMP-Q5A"}

        offline_result = list_employees(presence="OFFLINE")
        offline_numbers = {r.employee_number for r in offline_result["rows"]}
        assert "EMP-Q5B" in offline_numbers
        assert "EMP-Q5A" not in offline_numbers


def test_pagination_total_matches_filtered_count_not_unfiltered(app, seeded):
    staff_a = make_staff(app, "q6a@example.com")
    staff_b = make_staff(app, "q6b@example.com")
    with app.app_context():
        from app.employees.queries import list_employees

        _make_active_profile(app, staff_a, "EMP-Q6A", department="Finance")
        _make_active_profile(app, staff_b, "EMP-Q6B", department="Sales")

        result = list_employees(department="Finance", page_size=10)
        assert result["total"] == 1
