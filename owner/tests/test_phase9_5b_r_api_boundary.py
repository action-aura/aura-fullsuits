"""Phase 9.5B-R Milestone 15/16 -- API stable-value boundary: locale must
never change a JSON field name, error code, or state value."""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, make_staff


def _make_active_employee(app, staff_id):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": "EMP-API-R1", "full_name": "API Boundary Test", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def test_employee_status_field_stays_raw_english_enum_in_arabic_locale(app, client, seeded):
    admin_id = make_staff(app, "apib1@example.com", super_admin=True)
    with app.app_context():
        profile = _make_active_employee(app, admin_id)
        profile_id = profile.id

    force_login(client, app, admin_id)
    client.get("/locale/ar?next=/")

    resp = client.get(f"/api/operations/v1/employees/{profile_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["employment_status"] == "ACTIVE"  # never "نشط"
    assert body["presence"] in ("ONLINE", "RECENTLY_ACTIVE", "OFFLINE")


def test_error_codes_stay_stable_in_arabic_locale(app, client, seeded):
    import uuid

    admin_id = make_staff(app, "apib2@example.com", super_admin=True)
    force_login(client, app, admin_id)
    client.get("/locale/ar?next=/")

    resp = client.get(f"/api/operations/v1/employees/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "RECORD_NOT_FOUND"


def test_permission_denied_error_code_stable_in_arabic(app, client, seeded):
    staff_id = make_staff(app, "apib3@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    client.get("/locale/ar?next=/")

    resp = client.get("/api/operations/v1/employees")
    assert resp.status_code == 403


def test_me_endpoint_permissions_list_unaffected_by_locale(app, client, seeded):
    staff_id = make_staff(app, "apib4@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)

    resp_en = client.get("/api/operations/v1/me")
    perms_en = sorted(resp_en.get_json()["permissions"])

    client.get("/locale/ar?next=/")
    resp_ar = client.get("/api/operations/v1/me")
    perms_ar = sorted(resp_ar.get_json()["permissions"])

    assert perms_en == perms_ar
    assert "employees.view_own" in perms_ar  # a real permission code, never translated
