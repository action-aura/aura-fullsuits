from __future__ import annotations

from datetime import date

import pytest

from tests.conftest import get_csrf, make_staff


def test_create_employee_invitation_and_accept_creates_profile_transactionally(app, client, seeded):
    admin_id = make_staff(app, "onb1admin@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import create_employee_invitation

        invitation, raw_token = create_employee_invitation(
            "onb1@example.com", ["SALES"],
            {"employee_number": "EMP-ONB1", "full_name": "Onboarded One", "employment_start_date": date(2026, 1, 1)},
            admin_id,
        )

    page = client.get(f"/auth/accept-invitation/{raw_token}")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": csrf, "display_name": "Onboarded One", "password": "Sup3r-Str0ng-Pass!"},
    )
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from app.models.staff import StaffUser
        from sqlalchemy import select

        staff = db_session.execute(select(StaffUser).where(StaffUser.email == "onb1@example.com")).scalars().first()
        assert staff is not None
        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff.id)).scalars().first()
        assert profile is not None
        assert profile.employee_number == "EMP-ONB1"
        assert profile.employment_status == "PENDING"  # not yet activated -- no login has happened yet


def test_duplicate_employee_number_rejected_before_invitation_created(app, seeded):
    admin_id = make_staff(app, "onb2admin@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import EmployeeDraftValidationError, create_employee_invitation

        create_employee_invitation(
            "onb2a@example.com", ["SALES"],
            {"employee_number": "EMP-ONB2", "full_name": "First", "employment_start_date": date(2026, 1, 1)},
            admin_id,
        )
        with pytest.raises(EmployeeDraftValidationError):
            create_employee_invitation(
                "onb2b@example.com", ["SALES"],
                {"employee_number": "EMP-ONB2", "full_name": "Second", "employment_start_date": date(2026, 1, 1)},
                admin_id,
            )


def test_invalid_manager_reference_rejected(app, seeded):
    import uuid

    admin_id = make_staff(app, "onb3admin@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import EmployeeDraftValidationError, create_employee_invitation

        with pytest.raises(EmployeeDraftValidationError):
            create_employee_invitation(
                "onb3@example.com", ["SALES"],
                {
                    "employee_number": "EMP-ONB3", "full_name": "X", "employment_start_date": date(2026, 1, 1),
                    "manager_employee_profile_id": str(uuid.uuid4()),
                },
                admin_id,
            )


def test_first_login_activates_pending_profile(app, client, seeded):
    admin_id = make_staff(app, "onb4admin@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import create_employee_invitation

        _, raw_token = create_employee_invitation(
            "onb4@example.com", ["SALES"],
            {"employee_number": "EMP-ONB4", "full_name": "Onboarded Four", "employment_start_date": date(2026, 1, 1)},
            admin_id, mfa_required=False,
        )

    page = client.get(f"/auth/accept-invitation/{raw_token}")
    csrf = get_csrf(page.get_data(as_text=True))
    client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": csrf, "display_name": "Onboarded Four", "password": "Sup3r-Str0ng-Pass!"},
    )

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-ONB4")).scalars().first()
        assert profile.employment_status == "PENDING"  # still pending -- no login yet

    login_page = client.get("/auth/login")
    login_csrf = get_csrf(login_page.get_data(as_text=True))
    resp = client.post("/auth/login", data={"csrf_token": login_csrf, "email": "onb4@example.com", "password": "Sup3r-Str0ng-Pass!"})
    assert resp.status_code == 302

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-ONB4")).scalars().first()
        assert profile.employment_status == "ACTIVE"  # activated on first real login


def test_mfa_required_employee_forced_through_enrollment_before_full_access(app, client, seeded):
    admin_id = make_staff(app, "onb5admin@example.com", super_admin=True)
    with app.app_context():
        from app.staff.services import create_employee_invitation

        _, raw_token = create_employee_invitation(
            "onb5@example.com", ["SALES"],
            {"employee_number": "EMP-ONB5", "full_name": "Onboarded Five", "employment_start_date": date(2026, 1, 1)},
            admin_id, mfa_required=True,
        )

    page = client.get(f"/auth/accept-invitation/{raw_token}")
    csrf = get_csrf(page.get_data(as_text=True))
    client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": csrf, "display_name": "Onboarded Five", "password": "Sup3r-Str0ng-Pass!"},
    )

    login_page = client.get("/auth/login")
    login_csrf = get_csrf(login_page.get_data(as_text=True))
    resp = client.post(
        "/auth/login", data={"csrf_token": login_csrf, "email": "onb5@example.com", "password": "Sup3r-Str0ng-Pass!"}
    )
    assert resp.status_code == 302
    assert "/auth/mfa-enroll" in resp.headers["Location"]  # not a full session yet -- forced into enrollment

    with app.app_context():
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile
        from sqlalchemy import select

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.employee_number == "EMP-ONB5")).scalars().first()
        assert profile.employment_status == "PENDING"  # still pending -- MFA enrollment not yet completed
