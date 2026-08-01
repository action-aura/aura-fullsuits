from __future__ import annotations

from datetime import date

from tests.conftest import force_login, make_staff


def _make_active_profile(app, staff_id, employee_number):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": employee_number, "full_name": f"Employee {employee_number}", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def test_dashboard_metrics_definitions_are_exact_and_do_not_conflate(app, seeded):
    staff_a = make_staff(app, "dash1a@example.com")
    staff_b = make_staff(app, "dash1b@example.com")
    with app.app_context():
        from app.employees.dashboard import get_employee_dashboard_metrics
        from app.employees.services import create_employee_profile, suspend_employee

        _make_active_profile(app, staff_a, "EMP-DASH1A")
        pending_profile = create_employee_profile(
            {"staff_user_id": staff_b, "employee_number": "EMP-DASH1B", "full_name": "Pending B", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_a,
        )

        metrics = get_employee_dashboard_metrics()
        assert metrics["total_employees"] == 2
        assert metrics["active_employees"] == 1
        assert metrics["pending_employees"] == 1
        assert metrics["setup_pending"] == metrics["pending_employees"]  # same real definition, not a second count


def test_dashboard_route_requires_employees_view_all(app, client, seeded):
    staff_id = make_staff(app, "dash2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.get("/employees/dashboard")
    assert resp.status_code == 403


def test_dashboard_route_renders_for_management(app, client, seeded):
    admin_id = make_staff(app, "dash3@example.com", super_admin=True)
    force_login(client, app, admin_id)
    resp = client.get("/employees/dashboard")
    assert resp.status_code == 200
    assert b"Employee dashboard" in resp.data
