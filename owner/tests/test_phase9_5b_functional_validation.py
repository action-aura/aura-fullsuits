"""Phase 9.5B Milestone 22 -- local functional validation, synthetic data
only. Walks the governing spec's own end-to-end scenario in one real test.
Presence-threshold "waiting" is simulated via presence_state()'s as_of
parameter (the same real, sound technique used throughout
test_phase9_5b_presence.py) rather than literal wall-clock sleep -- a real
15-minute sleep in a test suite would be its own kind of dishonesty (slow,
flaky, and testing the clock rather than the logic).
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from tests.conftest import get_csrf, login_and_verify_mfa, make_staff


def test_full_employee_lifecycle_scenario(app, client, seeded):
    # 1. Management account A (real login + MFA, matching real Aura Owner practice).
    make_staff(app, "e2e-mgmt-a@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "e2e-mgmt-a@example.com")

    # 2-4. A creates a SALES employee; account + profile created transactionally,
    # one-time setup package generated.
    new_page = client.get("/employees/new")
    csrf = get_csrf(new_page.get_data(as_text=True))
    create_resp = client.post(
        "/employees",
        data={
            "csrf_token": csrf, "email": "e2e-employee@example.com", "employee_number": "EMP-E2E1",
            "full_name": "Scenario Employee", "employment_start_date": "2026-01-01", "role_codes": ["SALES"],
            "mfa_required": "1",
        },
    )
    assert create_resp.status_code == 200
    match = re.search(r"/auth/accept-invitation/([^\"<\s]+)", create_resp.get_data(as_text=True))
    assert match is not None
    raw_token = match.group(1)

    with app.app_context():
        from app.employees.queries import find_own_profile
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from sqlalchemy import select

        employee_staff = db_session.execute(select(StaffUser).where(StaffUser.email == "e2e-employee@example.com")).scalars().first()
        assert employee_staff is None  # not created yet -- only the invitation exists so far

    # 5-6. Employee completes password setup.
    accept_page = client.get(f"/auth/accept-invitation/{raw_token}")
    accept_csrf = get_csrf(accept_page.get_data(as_text=True))
    accept_resp = client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": accept_csrf, "display_name": "Scenario Employee", "password": "Sup3r-Str0ng-Pass!"},
    )
    assert accept_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-E2E1")).scalars().first()
        assert profile.employment_status == "PENDING"
        employee_profile_id = profile.id

    # Admin A's own session is still active on this shared test client -- log
    # it out before the employee's own login steps below (otherwise
    # /auth/login redirects an already-authenticated caller away instead of
    # showing the form).
    logout_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": logout_csrf})

    # 7. Employee signs in -- forced into MFA enrollment (mfa_required=True).
    login_page = client.get("/auth/login")
    login_csrf = get_csrf(login_page.get_data(as_text=True))
    login_resp = client.post(
        "/auth/login", data={"csrf_token": login_csrf, "email": "e2e-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert login_resp.status_code == 302
    assert "/auth/mfa-enroll" in login_resp.headers["Location"]

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        reread = db_session.get(EmployeeProfile, employee_profile_id)
        assert reread.employment_status == "PENDING"  # MFA not completed yet -- not activated

    # Complete MFA enrollment (real TOTP flow, same client/session).
    enroll_page = client.get("/auth/mfa-enroll")
    assert enroll_page.status_code == 200
    secret_match = re.search(r"<dt>Manual secret</dt><dd><code[^>]*>([^<]+)</code></dd>", enroll_page.get_data(as_text=True))
    assert secret_match is not None
    import pyotp

    code = pyotp.TOTP(secret_match.group(1)).now()
    enroll_csrf = get_csrf(enroll_page.get_data(as_text=True))
    enroll_resp = client.post("/auth/mfa-enroll", data={"csrf_token": enroll_csrf, "code": code})
    assert enroll_resp.status_code == 302  # full session established now

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        reread = db_session.get(EmployeeProfile, employee_profile_id)
        assert reread.employment_status == "ACTIVE"  # activated on the completed first login

    # 8. Employee sees self-profile only.
    profile_resp = client.get("/profile")
    assert profile_resp.status_code == 200
    assert b"Scenario Employee" in profile_resp.data

    # 9-10. Employee sends heartbeat; dashboard shows ONLINE.
    heartbeat_csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    heartbeat_resp = client.post(
        "/api/operations/v1/presence/heartbeat", json={"app_instance_id": "e2e-device-1", "platform": "WEB"},
        headers={"X-CSRFToken": heartbeat_csrf},
    )
    assert heartbeat_resp.status_code == 200
    assert heartbeat_resp.get_json()["presence"] == "ONLINE"

    with app.app_context():
        from app.employees.presence import OFFLINE, ONLINE, RECENTLY_ACTIVE, employee_presence_state
        from app.models.base import utcnow

        now = utcnow()
        # 11-12. Past ONLINE threshold -> RECENTLY_ACTIVE (simulated via as_of, not literal sleep).
        assert employee_presence_state(employee_profile_id, as_of=now + timedelta(seconds=121)) == RECENTLY_ACTIVE
        # 13-14. Past RECENTLY_ACTIVE threshold -> OFFLINE.
        assert employee_presence_state(employee_profile_id, as_of=now + timedelta(seconds=901)) == OFFLINE

    # 16-18. Management account B sees the same employee and updates it; A sees the change.
    make_staff(app, "e2e-mgmt-b@example.com", super_admin=True, mfa=True)

    with app.test_client() as client_b:
        login_and_verify_mfa(client_b, "e2e-mgmt-b@example.com")
        detail_b = client_b.get(f"/employees/{employee_profile_id}")
        assert detail_b.status_code == 200
        assert b"Scenario Employee" in detail_b.data

        edit_csrf = get_csrf(detail_b.get_data(as_text=True))
        with app.app_context():
            from app.extensions import db_session
            from app.models.employees import EmployeeProfile

            current_version = db_session.get(EmployeeProfile, employee_profile_id).version

        edit_resp = client_b.post(
            f"/employees/{employee_profile_id}/edit",
            data={"csrf_token": edit_csrf, "version": str(current_version), "full_name": "Scenario Employee", "job_title": "Set by B"},
        )
        assert edit_resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        reread = db_session.get(EmployeeProfile, employee_profile_id)
        assert reread.job_title == "Set by B"  # A's next read (below) sees B's change

    with app.test_client() as admin_a_reread_client:
        login_and_verify_mfa(admin_a_reread_client, "e2e-mgmt-a@example.com")
        detail_a_after = admin_a_reread_client.get(f"/employees/{employee_profile_id}")
        assert b"Set by B" in detail_a_after.data

    # 19-20. Employee cannot change protected fields / cannot access another employee.
    with app.app_context():
        from app.employees.services import SELF_EDITABLE_PROFILE_FIELDS

        assert "employee_number" not in SELF_EDITABLE_PROFILE_FIELDS
    another_employee_id = "00000000-0000-0000-0000-000000000000"
    forbidden_resp = client.get(f"/employees/{another_employee_id}")
    assert forbidden_resp.status_code == 403  # SALES lacks employees.view_all entirely

    # 21-23. Management suspends the employee; sessions revoked; heartbeat and login rejected.
    with app.test_client() as admin_client:
        login_and_verify_mfa(admin_client, "e2e-mgmt-a@example.com")
        suspend_csrf = get_csrf(admin_client.get(f"/employees/{employee_profile_id}").get_data(as_text=True))
        suspend_resp = admin_client.post(
            f"/employees/{employee_profile_id}/suspend", data={"csrf_token": suspend_csrf, "reason": "scenario suspension"}
        )
        assert suspend_resp.status_code == 302

    heartbeat_after_suspend = client.post(
        "/api/operations/v1/presence/heartbeat", json={"app_instance_id": "e2e-device-1", "platform": "WEB"},
        headers={"X-CSRFToken": heartbeat_csrf},
    )
    assert heartbeat_after_suspend.status_code in (401, 403)  # the revoked session is rejected

    relogin_page = client.get("/auth/login")
    relogin_csrf = get_csrf(relogin_page.get_data(as_text=True))
    relogin_resp = client.post(
        "/auth/login", data={"csrf_token": relogin_csrf, "email": "e2e-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert relogin_resp.status_code == 401  # suspension blocks new login, not just old sessions

    # 25-26. Management reactivates; employee can sign in again.
    with app.test_client() as admin_client:
        login_and_verify_mfa(admin_client, "e2e-mgmt-a@example.com")
        reactivate_csrf = get_csrf(admin_client.get(f"/employees/{employee_profile_id}").get_data(as_text=True))
        reactivate_resp = admin_client.post(f"/employees/{employee_profile_id}/reactivate", data={"csrf_token": reactivate_csrf})
        assert reactivate_resp.status_code == 302

    relogin2_page = client.get("/auth/login")
    relogin2_csrf = get_csrf(relogin2_page.get_data(as_text=True))
    relogin2_resp = client.post(
        "/auth/login", data={"csrf_token": relogin2_csrf, "email": "e2e-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert relogin2_resp.status_code == 302  # login works again post-reactivation

    # 27-29. Management terminates; sessions revoked; login rejected.
    with app.test_client() as admin_client:
        login_and_verify_mfa(admin_client, "e2e-mgmt-a@example.com")
        terminate_csrf = get_csrf(admin_client.get(f"/employees/{employee_profile_id}").get_data(as_text=True))
        terminate_resp = admin_client.post(
            f"/employees/{employee_profile_id}/terminate", data={"csrf_token": terminate_csrf, "reason": "scenario termination"}
        )
        assert terminate_resp.status_code == 302

    relogin3_page = client.get("/auth/login")
    relogin3_csrf = get_csrf(relogin3_page.get_data(as_text=True))
    relogin3_resp = client.post(
        "/auth/login", data={"csrf_token": relogin3_csrf, "email": "e2e-employee@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert relogin3_resp.status_code == 401

    # 30. Historical profile and audit remain (never hard-deleted).
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.employees import EmployeeProfile

        final_profile = db_session.get(EmployeeProfile, employee_profile_id)
        assert final_profile is not None
        assert final_profile.employment_status == "TERMINATED"
        assert final_profile.full_name == "Scenario Employee"

        audit_rows = db_session.query(AuditLog).filter_by(entity_type="employee_profile", entity_public_id=str(employee_profile_id)).all()
        action_codes = {r.action_code for r in audit_rows}
        assert {"EMPLOYEE_SUSPENDED", "EMPLOYEE_REACTIVATED", "EMPLOYEE_TERMINATED", "EMPLOYEE_PROFILE_UPDATED"} <= action_codes

    # 31. Last-SUPER_ADMIN protection demonstrated with the two real scenario admins.
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.super_admin_guard import LastSuperAdminError
        from app.staff.services import disable_staff
        from sqlalchemy import select

        admin_a = db_session.execute(select(StaffUser).where(StaffUser.email == "e2e-mgmt-a@example.com")).scalars().first()
        admin_b = db_session.execute(select(StaffUser).where(StaffUser.email == "e2e-mgmt-b@example.com")).scalars().first()
        disable_staff(admin_b, "scenario", admin_a.id)  # succeeds -- A remains
        with pytest.raises(LastSuperAdminError):
            disable_staff(admin_a, "scenario", admin_a.id)  # blocked -- would leave zero

    # 32. Audit timeline shows correct, distinct actors.
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.staff import StaffUser
        from sqlalchemy import select

        admin_a_id = db_session.execute(select(StaffUser.id).where(StaffUser.email == "e2e-mgmt-a@example.com")).scalar_one()
        suspend_row = db_session.query(AuditLog).filter_by(action_code="EMPLOYEE_SUSPENDED", entity_public_id=str(employee_profile_id)).first()
        assert suspend_row.actor_staff_user_id == admin_a_id
