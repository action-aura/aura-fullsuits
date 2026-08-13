"""Phase 9.5B-R Milestone 19 -- local bilingual end-to-end validation.

Real HTTP requests through the actual Flask test client, synthetic data
only. Mirrors Phase 9.5B's own English scenario
(test_phase9_5b_functional_validation.py) but in Arabic, using Arabic
employee-name data, and asserts that switching language changes nothing
about identity/authorization/state/audit.
"""
from __future__ import annotations

import re

import pyotp

from tests.conftest import get_csrf, login_and_verify_mfa, make_staff


def test_full_arabic_employee_lifecycle_scenario(app, client, seeded):
    # 1-2. Open login in Arabic, confirm lang/dir.
    client.get("/locale/ar?next=/auth/login")
    login_page = client.get("/auth/login")
    login_data = login_page.get_data(as_text=True)
    assert 'lang="ar" dir="rtl"' in login_data
    assert "تسجيل الدخول" in login_data

    # 3. Management admin authenticates with MFA.
    make_staff(app, "ar-mgmt@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "ar-mgmt@example.com")

    # 4. Employee dashboard renders correctly in Arabic.
    dash_resp = client.get("/employees/dashboard")
    assert dash_resp.status_code == 200
    assert "لوحة تحكم الموظفين" in dash_resp.get_data(as_text=True)

    # 5. Employee list renders correctly (empty state, Arabic).
    list_resp = client.get("/employees")
    assert "لا يوجد موظفون مطابقون لهذه المرشحات" in list_resp.get_data(as_text=True)

    # 6. Create a synthetic employee using Arabic name data.
    new_page = client.get("/employees/new")
    assert "إضافة موظف" in new_page.get_data(as_text=True)
    csrf = get_csrf(new_page.get_data(as_text=True))
    create_resp = client.post(
        "/employees",
        data={
            "csrf_token": csrf, "email": "ar-employee@example.com", "employee_number": "EMP-AR1",
            "full_name": "سارة أحمد", "employment_start_date": "2026-01-01", "role_codes": ["SALES"],
            "mfa_required": "1",
        },
    )
    assert create_resp.status_code == 200
    assert "تم إنشاء دعوة الموظف" in create_resp.get_data(as_text=True)
    match = re.search(r"/auth/accept-invitation/([^\"<\s]+)", create_resp.get_data(as_text=True))
    assert match is not None
    raw_token = match.group(1)

    # Log the admin out before the employee's own steps (shared test client).
    logout_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": logout_csrf})

    # 7. Complete setup in Arabic.
    accept_page = client.get(f"/auth/accept-invitation/{raw_token}")
    assert "إعداد حساب Aura Owner الخاص بك" in accept_page.get_data(as_text=True)
    accept_csrf = get_csrf(accept_page.get_data(as_text=True))
    accept_resp = client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": accept_csrf, "display_name": "سارة أحمد", "password": "Sup3r-Str0ng-Pass!"},
    )
    assert accept_resp.status_code == 302

    # First login -- forced into Arabic MFA enrollment.
    login_page2 = client.get("/auth/login")
    login_csrf2 = get_csrf(login_page2.get_data(as_text=True))
    login_resp = client.post(
        "/auth/login", data={"csrf_token": login_csrf2, "email": "ar-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert login_resp.status_code == 302
    assert "/auth/mfa-enroll" in login_resp.headers["Location"]

    enroll_page = client.get("/auth/mfa-enroll")
    assert "تفعيل المصادقة متعددة العوامل" in enroll_page.get_data(as_text=True)
    secret_match = re.search(r"<dt>الرمز السري اليدوي</dt><dd><code[^>]*>([^<]+)</code></dd>", enroll_page.get_data(as_text=True))
    assert secret_match is not None
    code = pyotp.TOTP(secret_match.group(1)).now()
    enroll_csrf = get_csrf(enroll_page.get_data(as_text=True))
    enroll_resp = client.post("/auth/mfa-enroll", data={"csrf_token": enroll_csrf, "code": code})
    assert enroll_resp.status_code == 302

    # 8. Self-profile renders correctly in Arabic with the real Arabic name.
    profile_resp = client.get("/profile")
    assert profile_resp.status_code == 200
    profile_data = profile_resp.get_data(as_text=True)
    assert "سارة أحمد" in profile_data
    assert "EMP-AR1" in profile_data  # identifier stays LTR-safe, unmangled

    # 9. Presence labels render correctly (heartbeat + Arabic dashboard).
    heartbeat_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    heartbeat_resp = client.post(
        "/api/operations/v1/presence/heartbeat", json={"app_instance_id": "ar-device-1", "platform": "WEB"},
        headers={"X-CSRFToken": heartbeat_csrf},
    )
    assert heartbeat_resp.status_code == 200
    assert heartbeat_resp.get_json()["presence"] == "ONLINE"  # API stays raw English enum

    # Log employee out, log admin back in.
    logout_csrf2 = get_csrf(client.get("/profile").get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": logout_csrf2})

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-AR1")).scalars().first()
        profile_id = profile.id
        assert profile.employment_status == "ACTIVE"  # real stored enum, unaffected by locale
        assert profile.full_name == "سارة أحمد"  # Arabic name preserved exactly

    login_and_verify_mfa(client, "ar-mgmt@example.com")
    client.get("/locale/ar?next=/")

    detail_resp = client.get(f"/employees/{profile_id}")
    detail_data = detail_resp.get_data(as_text=True)
    assert "سارة أحمد" in detail_data
    assert "نشط" in detail_data  # localized ACTIVE label

    # 10-11. Lifecycle confirmations/validation in Arabic; suspension still blocks login.
    suspend_csrf = get_csrf(detail_resp.get_data(as_text=True))
    assert "هل تريد إيقاف هذا الموظف مؤقتًا؟" in detail_data  # localized confirm() text present
    suspend_resp = client.post(
        f"/employees/{profile_id}/suspend", data={"csrf_token": suspend_csrf, "reason": "اختبار"}
    )
    assert suspend_resp.status_code == 302

    # Log the admin out first -- otherwise /auth/login redirects the
    # already-authenticated caller away instead of showing the form.
    admin_logout_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": admin_logout_csrf})

    relogin_page = client.get("/auth/login")
    relogin_csrf = get_csrf(relogin_page.get_data(as_text=True))
    relogin_resp = client.post(
        "/auth/login", data={"csrf_token": relogin_csrf, "email": "ar-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert relogin_resp.status_code == 401  # suspension blocks login regardless of locale

    # 12. Audit timeline uses localized display labels, real actor/entity unaffected.
    login_and_verify_mfa(client, "ar-mgmt@example.com")
    client.get("/locale/ar?next=/")
    audit_resp = client.get(f"/employees/{profile_id}")
    audit_data = audit_resp.get_data(as_text=True)
    assert "تم إيقاف الموظف مؤقتًا" in audit_data  # localized EMPLOYEE_SUSPENDED label

    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        row = db_session.query(AuditLog).filter_by(action_code="EMPLOYEE_SUSPENDED", entity_public_id=str(profile_id)).first()
        assert row.action_code == "EMPLOYEE_SUSPENDED"  # stored value stays the real English identifier

    # 13. Identifiers remain correctly ordered/isolated (bidi-safe).
    assert '<bdi dir="ltr">EMP-AR1</bdi>' in detail_data

    # 14-15. Switch back to English; preference persists.
    switch_back = client.get("/locale/en?next=/auth/login")
    assert switch_back.status_code == 302

    final_logout_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": final_logout_csrf})

    login_after_switch = client.get("/auth/login")
    assert 'lang="en" dir="ltr"' in login_after_switch.get_data(as_text=True)

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from sqlalchemy import select

        admin = db_session.execute(select(StaffUser).where(StaffUser.email == "ar-mgmt@example.com")).scalars().first()
        assert admin.locale == "en"  # the switch-back persisted to the account too
