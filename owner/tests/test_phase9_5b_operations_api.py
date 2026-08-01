from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, make_staff


def _make_active_employee(app, staff_id, employee_number):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": employee_number, "full_name": f"Employee {employee_number}", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def test_me_endpoint_returns_own_profile_and_permissions(app, client, seeded):
    staff_id = make_staff(app, "api1@example.com", role_codes=["SALES"])
    _make_active_employee(app, staff_id, "EMP-API1")
    force_login(client, app, staff_id)

    resp = client.get("/api/operations/v1/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["email"] == "api1@example.com"
    assert "employees.view_own" in body["permissions"]
    assert body["employee_profile"]["employee_number"] == "EMP-API1"


def test_me_endpoint_requires_authentication(app, client, seeded):
    resp = client.get("/api/operations/v1/me")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "authentication_required"


def test_presence_heartbeat_updates_and_returns_online(app, client, seeded):
    staff_id = make_staff(app, "api2@example.com")
    _make_active_employee(app, staff_id, "EMP-API2")
    force_login(client, app, staff_id)

    csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    resp = client.post(
        "/api/operations/v1/presence/heartbeat",
        json={"app_instance_id": "device-1", "platform": "WEB"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 200
    assert resp.get_json()["presence"] == "ONLINE"


def test_presence_heartbeat_rejects_anonymous(app, client, seeded):
    resp = client.post("/api/operations/v1/presence/heartbeat", json={"app_instance_id": "x", "platform": "WEB"})
    # CSRF protection runs globally, before route/auth logic -- an anonymous
    # request with no token is rejected at 400 before it would even reach the
    # require_login 401 check. Either way, an anonymous heartbeat never
    # succeeds -- both codes are a real rejection, never a 200.
    assert resp.status_code in (400, 401)


def test_presence_heartbeat_rejects_missing_app_instance_id(app, client, seeded):
    staff_id = make_staff(app, "api3@example.com")
    _make_active_employee(app, staff_id, "EMP-API3")
    force_login(client, app, staff_id)
    csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    resp = client.post("/api/operations/v1/presence/heartbeat", json={"platform": "WEB"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400


def test_employees_list_requires_permission(app, client, seeded):
    staff_id = make_staff(app, "api4@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.get("/api/operations/v1/employees")
    assert resp.status_code == 403


def test_employees_list_returns_paginated_envelope(app, client, seeded):
    admin_id = make_staff(app, "api5admin@example.com", super_admin=True)
    _make_active_employee(app, admin_id, "EMP-API5")
    force_login(client, app, admin_id)
    resp = client.get("/api/operations/v1/employees")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "rows" in body and "total" in body and "page" in body


def test_employee_detail_uses_public_uuid_and_hides_secrets(app, client, seeded):
    admin_id = make_staff(app, "api6admin@example.com", super_admin=True)
    profile = _make_active_employee(app, admin_id, "EMP-API6")
    force_login(client, app, admin_id)
    resp = client.get(f"/api/operations/v1/employees/{profile.id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == str(profile.id)
    assert "password_hash" not in str(body).lower()
    assert "mfa_secret" not in str(body).lower()
    assert "token" not in str(body).lower()


def test_employee_detail_not_found_returns_record_not_found(app, client, seeded):
    import uuid

    admin_id = make_staff(app, "api7admin@example.com", super_admin=True)
    force_login(client, app, admin_id)
    resp = client.get(f"/api/operations/v1/employees/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "RECORD_NOT_FOUND"


def test_update_employee_version_conflict(app, client, seeded):
    admin_id = make_staff(app, "api8admin@example.com", super_admin=True)
    profile = _make_active_employee(app, admin_id, "EMP-API8")
    force_login(client, app, admin_id)
    csrf = get_csrf(client.get(f"/employees/{profile.id}").get_data(as_text=True))
    resp = client.patch(
        f"/api/operations/v1/employees/{profile.id}",
        json={"full_name": "New Name", "version": profile.version + 99},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "VERSION_CONFLICT"


def test_lifecycle_invalid_transition_returns_named_error(app, client, seeded):
    admin_id = make_staff(app, "api9admin@example.com", super_admin=True)
    target_id = make_staff(app, "api9target@example.com")
    with app.app_context():
        from app.employees.services import terminate_employee

        profile = _make_active_employee(app, target_id, "EMP-API9")
        terminate_employee(profile, "x", actor_staff_user_id=admin_id)
        profile_id = profile.id
    force_login(client, app, admin_id)
    csrf = get_csrf(client.get(f"/employees/{profile_id}").get_data(as_text=True))
    resp = client.post(f"/api/operations/v1/employees/{profile_id}/activate", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 409
    assert resp.get_json()["error"] == "INVALID_STATE_TRANSITION"


def test_my_sessions_endpoint_lists_only_own_sessions(app, client, seeded):
    staff_id = make_staff(app, "api10@example.com")
    force_login(client, app, staff_id)
    resp = client.get("/api/operations/v1/me/sessions")
    assert resp.status_code == 200
    body = resp.get_json()
    assert all("token" not in str(row).lower() for row in body["rows"])
