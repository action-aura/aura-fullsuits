"""Phase 9.5B Milestone 18 -- authorization/IDOR gaps not already covered by
test_phase9_5b_web_routes.py (list/self-profile) or test_phase9_5b_sessions.py
(session IDOR) or test_phase9_5b_operations_api.py (API RBAC)."""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, make_staff


def _make_active_employee(app, staff_id, employee_number, actor_id):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": employee_number, "full_name": f"Employee {employee_number}", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=actor_id,
    )
    activate_employee(profile, actor_staff_user_id=actor_id)
    return profile


def test_sales_employee_cannot_view_another_employees_detail_by_uuid(app, client, seeded):
    admin_id = make_staff(app, "auth1admin@example.com", super_admin=True)
    victim_id = make_staff(app, "auth1victim@example.com")
    attacker_id = make_staff(app, "auth1attacker@example.com", role_codes=["SALES"])
    with app.app_context():
        profile = _make_active_employee(app, victim_id, "EMP-AUTH1", admin_id)
        profile_id = profile.id

    force_login(client, app, attacker_id)
    resp = client.get(f"/employees/{profile_id}")
    assert resp.status_code == 403  # permission-denied, not even a 404 that would leak existence differently


def test_sales_employee_cannot_suspend_another_employee(app, client, seeded):
    admin_id = make_staff(app, "auth2admin@example.com", super_admin=True)
    victim_id = make_staff(app, "auth2victim@example.com")
    attacker_id = make_staff(app, "auth2attacker@example.com", role_codes=["SALES"])
    with app.app_context():
        profile = _make_active_employee(app, victim_id, "EMP-AUTH2", admin_id)
        profile_id = profile.id

    force_login(client, app, attacker_id)
    csrf = get_csrf(client.get("/profile").get_data(as_text=True))  # a real token, so CSRF passes and the permission check is what's actually proven
    resp = client.post(f"/employees/{profile_id}/suspend", data={"csrf_token": csrf, "reason": "malicious"})
    assert resp.status_code == 403

    with app.app_context():
        from app.employees.queries import find_own_profile

        reread = find_own_profile(victim_id)
        assert reread.employment_status == "ACTIVE"  # untouched


def test_sales_employee_cannot_terminate_via_api(app, client, seeded):
    admin_id = make_staff(app, "auth3admin@example.com", super_admin=True)
    victim_id = make_staff(app, "auth3victim@example.com")
    attacker_id = make_staff(app, "auth3attacker@example.com", role_codes=["SALES"])
    with app.app_context():
        profile = _make_active_employee(app, victim_id, "EMP-AUTH3", admin_id)
        profile_id = profile.id

    force_login(client, app, attacker_id)
    csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    resp = client.post(
        f"/api/operations/v1/employees/{profile_id}/terminate", json={"reason": "malicious"}, headers={"X-CSRFToken": csrf}
    )
    assert resp.status_code == 403


def test_sales_employee_cannot_revoke_another_employees_all_sessions(app, client, seeded):
    admin_id = make_staff(app, "auth4admin@example.com", super_admin=True)
    victim_id = make_staff(app, "auth4victim@example.com")
    attacker_id = make_staff(app, "auth4attacker@example.com", role_codes=["SALES"])
    with app.app_context():
        profile = _make_active_employee(app, victim_id, "EMP-AUTH4", admin_id)
        profile_id = profile.id

    force_login(client, app, attacker_id)
    csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    resp = client.post(f"/employees/{profile_id}/sessions/revoke", data={"csrf_token": csrf})
    assert resp.status_code == 403


def test_support_role_cannot_assign_roles(app, client, seeded):
    staff_id = make_staff(app, "auth5@example.com", role_codes=["SUPPORT"])
    other_id = make_staff(app, "auth5other@example.com")
    force_login(client, app, staff_id)
    csrf = get_csrf(client.get("/auth/change-password").get_data(as_text=True))
    resp = client.post(f"/staff/{other_id}/roles", data={"csrf_token": csrf, "role_codes": ["SALES"]})
    assert resp.status_code == 403


def test_finance_role_cannot_view_employees(app, client, seeded):
    """FINANCE's real permission set (rbac-permission-matrix.md) deliberately
    excludes employees.* -- confirmed at the route layer, not just the seed
    data table."""
    staff_id = make_staff(app, "auth6@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)
    resp = client.get("/employees")
    assert resp.status_code == 403


def test_support_role_cannot_view_employee_presence_aggregate(app, client, seeded):
    """SUPPORT's real permission set has no employees.* grant at all (unlike
    SALES, which legitimately holds employees.view_presence for its own
    dashboard) -- confirmed against the real seed data, not assumed."""
    staff_id = make_staff(app, "auth7@example.com", role_codes=["SUPPORT"])
    force_login(client, app, staff_id)
    resp = client.get("/api/operations/v1/employees/presence")
    assert resp.status_code == 403


def test_terminated_employee_cannot_authenticate_even_with_correct_password(app, client, seeded):
    admin_id = make_staff(app, "auth8admin@example.com", super_admin=True)
    target_id = make_staff(app, "auth8target@example.com")
    with app.app_context():
        from app.employees.services import terminate_employee

        profile = _make_active_employee(app, target_id, "EMP-AUTH8", admin_id)
        terminate_employee(profile, "x", actor_staff_user_id=admin_id)

    login_page = client.get("/auth/login")
    csrf = get_csrf(login_page.get_data(as_text=True))
    resp = client.post("/auth/login", data={"csrf_token": csrf, "email": "auth8target@example.com", "password": "Sup3r-Str0ng-Pass!"})
    assert resp.status_code == 401
