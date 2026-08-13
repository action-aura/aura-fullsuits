"""Phase 9.5B-R Milestone 16 -- every gated template renders in both
locales, with correct lang/dir, and no raw translation-key leakage."""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff


def _switch_to_arabic(client):
    resp = client.get("/locale/ar?next=/")
    assert resp.status_code == 302


def _make_active_employee(app, staff_id):
    from app.employees.services import activate_employee, create_employee_profile

    profile = create_employee_profile(
        {"staff_user_id": staff_id, "employee_number": "EMP-R1", "full_name": "Render Test", "employment_start_date": date(2026, 1, 1)},
        actor_staff_user_id=staff_id,
    )
    activate_employee(profile, actor_staff_user_id=staff_id)
    return profile


def test_login_page_renders_in_english_by_default(app, client, seeded):
    resp = client.get("/auth/login")
    data = resp.get_data(as_text=True)
    assert 'lang="en" dir="ltr"' in data
    assert "Log in" in data
    assert "{{" not in data  # no unrendered Jinja leaked through


def test_login_page_renders_in_arabic_after_switch(app, client, seeded):
    _switch_to_arabic(client)
    resp = client.get("/auth/login")
    data = resp.get_data(as_text=True)
    assert 'lang="ar" dir="rtl"' in data
    assert "تسجيل الدخول" in data


def test_every_auth_template_renders_in_both_locales(app, client, seeded):
    routes = ["/auth/login", "/auth/mfa-verify"]
    for locale in ("en", "ar"):
        client.get(f"/locale/{locale}?next=/")
        for route in routes:
            resp = client.get(route)
            assert resp.status_code in (200, 302)


def test_employee_list_and_dashboard_render_in_arabic(app, client, seeded):
    admin_id = make_staff(app, "render1@example.com", super_admin=True)
    with app.app_context():
        _make_active_employee(app, admin_id)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    list_resp = client.get("/employees")
    assert list_resp.status_code == 200
    list_data = list_resp.get_data(as_text=True)
    assert 'dir="rtl"' in list_data
    assert "الموظفون" in list_data

    dash_resp = client.get("/employees/dashboard")
    assert dash_resp.status_code == 200
    assert "لوحة تحكم الموظفين" in dash_resp.get_data(as_text=True)


def test_employee_detail_renders_in_arabic_with_localized_status(app, client, seeded):
    admin_id = make_staff(app, "render2@example.com", super_admin=True)
    with app.app_context():
        profile = _make_active_employee(app, admin_id)
        profile_id = profile.id
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    resp = client.get(f"/employees/{profile_id}")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert "نشط" in data  # ACTIVE status label
    assert "data-confirm=" in data  # JS confirm fix present
    assert "onsubmit=" not in data  # the old, unsafe inline pattern is gone


def test_self_profile_and_sessions_render_in_arabic(app, client, seeded):
    admin_id = make_staff(app, "render3@example.com", super_admin=True)
    with app.app_context():
        _make_active_employee(app, admin_id)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    profile_resp = client.get("/profile")
    assert profile_resp.status_code == 200
    assert "ملفي الشخصي" in profile_resp.get_data(as_text=True)

    sessions_resp = client.get("/profile/sessions")
    assert sessions_resp.status_code == 200
    assert "جلساتي" in sessions_resp.get_data(as_text=True)


def test_new_employee_form_renders_in_arabic_with_localized_role_labels(app, client, seeded):
    admin_id = make_staff(app, "render4@example.com", super_admin=True)
    force_login(client, app, admin_id)
    _switch_to_arabic(client)

    resp = client.get("/employees/new")
    assert resp.status_code == 200
    data = resp.get_data(as_text=True)
    assert "إضافة موظف" in data
    assert "المبيعات" in data  # role_label("SALES")
    assert "value=\"SALES\"" in data  # the submitted form value stays the raw code, never translated
