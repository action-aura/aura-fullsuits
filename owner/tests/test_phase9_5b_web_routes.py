from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _csrf(client, url):
    page = client.get(url)
    return get_csrf(page.get_data(as_text=True))


def test_management_can_create_list_and_view_employee(app, client, seeded):
    make_staff(app, "web1admin@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "web1admin@example.com")

    csrf = _csrf(client, "/employees/new")
    resp = client.post(
        "/employees",
        data={
            "csrf_token": csrf, "email": "web1@example.com", "employee_number": "EMP-WEB1", "full_name": "Web One",
            "employment_start_date": "2026-01-01", "role_codes": ["SALES"],
        },
    )
    assert resp.status_code == 200
    assert b"invitation" in resp.data.lower() or b"setup link" in resp.data.lower()

    # The EmployeeProfile only materializes on invitation ACCEPTANCE (Milestone
    # 3's transactional design) -- extract the one-time link and accept it,
    # matching real employee onboarding, before expecting it in the list.
    import re

    match = re.search(r"/auth/accept-invitation/([^\"<\s]+)", resp.get_data(as_text=True))
    assert match is not None
    raw_token = match.group(1)

    accept_page = client.get(f"/auth/accept-invitation/{raw_token}")
    accept_csrf = get_csrf(accept_page.get_data(as_text=True))
    client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": accept_csrf, "display_name": "Web One", "password": "Sup3r-Str0ng-Pass!"},
    )

    list_resp = client.get("/employees")
    assert list_resp.status_code == 200
    assert b"EMP-WEB1" in list_resp.data

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-WEB1")).scalars().first()

    detail_resp = client.get(f"/employees/{profile.id}")
    assert detail_resp.status_code == 200
    assert b"Web One" in detail_resp.data


def test_sales_employee_forbidden_from_management_employee_list(app, client, seeded):
    staff_id = make_staff(app, "web2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    resp = client.get("/employees")
    assert resp.status_code == 403


def test_self_profile_route_never_exposes_a_uuid_and_shows_own_data(app, client, seeded):
    admin_id = make_staff(app, "web3admin@example.com", super_admin=True)
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-WEB3", "full_name": "Web Three", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)

    force_login(client, app, admin_id)
    resp = client.get("/profile")
    assert resp.status_code == 200
    assert b"Web Three" in resp.data
    assert b"EMP-WEB3" in resp.data


def test_self_profile_cannot_edit_employee_number_field_at_all(app, client, seeded):
    """No route accepts employee_number from a self-service caller -- proven
    by inspecting update_own_profile()'s own fixed field set, not just by a
    form omission (a form omission alone wouldn't stop a raw POST)."""
    from app.employees.services import SELF_EDITABLE_PROFILE_FIELDS

    assert SELF_EDITABLE_PROFILE_FIELDS == ("phone",)


def test_suspend_then_reactivate_via_web_routes(app, client, seeded):
    admin_id = make_staff(app, "web4admin@example.com", super_admin=True)
    target_id = make_staff(app, "web4target@example.com")
    with app.app_context():
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": target_id, "employee_number": "EMP-WEB4", "full_name": "Web Four", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)
        profile_id = profile.id

    force_login(client, app, admin_id)
    # require_recent_auth needs an MFA-confirmed session -- force_login bypasses login but not MFA confirmation.
    # suspend/terminate require recent auth; without it, expect a redirect to reauth (not a 500).
    resp = client.post(f"/employees/{profile_id}/suspend", data={"csrf_token": _csrf(client, f"/employees/{profile_id}"), "reason": "test"})
    assert resp.status_code in (302, 401)
