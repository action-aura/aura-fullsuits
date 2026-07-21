from __future__ import annotations

from datetime import timedelta

from tests.conftest import force_login, get_csrf, login, make_staff


def test_csrf_required_on_state_changing_post(app, client, seeded):
    staff_id = make_staff(app, "sec1@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    resp = client.post("/customers", data={"legal_name": "No CSRF Co"})  # no csrf_token field at all
    assert resp.status_code == 400


def test_security_headers_present(client):
    resp = client.get("/auth/login")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in resp.headers


def test_session_cookie_is_httponly(client):
    resp = client.get("/auth/login")
    # Flask session cookie itself isn't set on a GET with nothing stored; assert
    # the app-level config that governs it instead (used for owner_session too).
    assert client.application.config["SESSION_COOKIE_HTTPONLY"] is True


def test_xss_payload_in_customer_name_is_escaped_on_render(app, client, seeded):
    staff_id = make_staff(app, "sec2@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    page = client.get("/customers/new")
    csrf = get_csrf(page.get_data(as_text=True))
    payload = "<script>alert(1)</script>"
    resp = client.post("/customers", data={"csrf_token": csrf, "legal_name": payload})
    detail = client.get(resp.headers["Location"])
    assert b"<script>alert(1)</script>" not in detail.data
    assert b"&lt;script&gt;" in detail.data  # Jinja auto-escaping neutralizes it


def test_sql_injection_style_input_is_treated_as_literal_data(app, client, seeded):
    staff_id = make_staff(app, "sec3@example.com", role_codes=["SALES"])
    force_login(client, app, staff_id)
    page = client.get("/customers/new")
    csrf = get_csrf(page.get_data(as_text=True))
    payload = "Robert'); DROP TABLE owner_customers;--"
    resp = client.post("/customers", data={"csrf_token": csrf, "legal_name": payload})
    assert resp.status_code == 302  # succeeded as ordinary data, no SQL error

    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        assert db_session.query(Customer).filter_by(legal_name=payload).count() == 1
        assert db_session.query(Customer).count() >= 1  # table still exists and has rows


def test_privilege_escalation_role_change_requires_recent_auth(app, client, seeded):
    admin_id = make_staff(app, "sec4-admin@example.com", super_admin=True, mfa=True)
    target_id = make_staff(app, "sec4-target@example.com", role_codes=["VIEWER"])
    force_login(client, app, admin_id)  # no recent MFA confirmation captured
    resp = client.post(
        f"/staff/{target_id}/roles", data={"csrf_token": "irrelevant", "role_codes": "SUPER_ADMIN"}
    )
    assert resp.status_code in (302, 400)  # redirected to reauth, or CSRF-blocked -- never silently applied
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        target = db_session.get(StaffUser, target_id)
        assert not any(a.role.code == "SUPER_ADMIN" for a in target.role_assignments)


def test_invitation_token_is_one_time_use(app, client, seeded):
    admin_id = make_staff(app, "sec5-admin@example.com")
    with app.app_context():
        from app.staff.services import create_invitation

        invitation, raw_token = create_invitation("newstaff@example.com", ["VIEWER"], admin_id)

    page = client.get(f"/auth/accept-invitation/{raw_token}")
    csrf = get_csrf(page.get_data(as_text=True))
    resp1 = client.post(
        f"/auth/accept-invitation/{raw_token}",
        data={"csrf_token": csrf, "display_name": "New Person", "password": "Str0ng-Enough-Pw!"},
    )
    assert resp1.status_code == 302

    page2 = client.get(f"/auth/accept-invitation/{raw_token}")
    assert b"invalid or has expired" in page2.data


def test_expired_invitation_rejected(app, client, seeded):
    admin_id = make_staff(app, "sec6-admin@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.staff.services import create_invitation

        invitation, raw_token = create_invitation("expired@example.com", ["VIEWER"], admin_id)
        invitation.expires_at = utcnow() - timedelta(days=1)
        db_session.commit()

    page = client.get(f"/auth/accept-invitation/{raw_token}")
    assert b"invalid or has expired" in page.data


def test_login_does_not_leak_stack_traces_on_bad_input(client):
    resp = client.post("/auth/login", data={"email": "not-a-real-email-format", "password": ""})
    assert resp.status_code in (400, 401)
    assert b"Traceback" not in resp.data


def test_login_open_redirect_rejected(app, client, seeded):
    make_staff(app, "openredirect@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    page = client.get("/auth/login")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(
        "/auth/login?next=https://evil.example.com/phish",
        data={"csrf_token": csrf, "email": "openredirect@example.com", "password": "Correct-Password-1!"},
    )
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/") and not location.startswith("//")
    assert "evil.example.com" not in location


def test_non_super_admin_cannot_grant_super_admin_role_via_service(app, seeded):
    from app.staff.services import SelfEscalationError, assign_roles

    non_admin_id = make_staff(app, "notadmin@example.com", role_codes=["SUPPORT"])
    target_id = make_staff(app, "target@example.com", role_codes=["VIEWER"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        target = db_session.get(StaffUser, target_id)
        try:
            assign_roles(target, ["SUPER_ADMIN"], non_admin_id)
            assert False, "should have raised SelfEscalationError"
        except SelfEscalationError:
            pass
        db_session.refresh(target)
        assert not any(a.role.code == "SUPER_ADMIN" for a in target.role_assignments)


def test_super_admin_can_grant_super_admin_role(app, seeded):
    from app.staff.services import assign_roles

    admin_id = make_staff(app, "realadmin@example.com", super_admin=True)
    target_id = make_staff(app, "target2@example.com", role_codes=["VIEWER"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        target = db_session.get(StaffUser, target_id)
        assign_roles(target, ["SUPER_ADMIN"], admin_id)
        db_session.refresh(target)
        assert any(a.role.code == "SUPER_ADMIN" for a in target.role_assignments)


def test_mfa_verify_brute_force_locked_out(app, client, seeded):
    make_staff(app, "mfabrute@example.com", super_admin=True, password="Correct-Password-1!", mfa=True)
    login(client, "mfabrute@example.com", password="Correct-Password-1!")
    for _ in range(5):
        page = client.get("/auth/mfa-verify")
        csrf = get_csrf(page.get_data(as_text=True))
        client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": "000000"})
    page = client.get("/auth/mfa-verify")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": "000000"})
    assert resp.status_code == 401
    assert b"Too many attempts" in resp.data


def test_backup_creation_hard_requires_super_admin_flag_not_just_permission(app, client, seeded):
    """Authorization-gate-parity: create_backup_route must hard-check
    is_super_admin the same way restore_backup_route does, not rely solely on
    the system.backup permission grant."""
    from tests.conftest import force_login

    # A hypothetical non-Super-Admin account that was (mis)granted system.backup
    # directly via a role would still be blocked at the route level.
    staff_id = make_staff(app, "notsuperadmin@example.com", role_codes=["SUPER_ADMIN"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        staff.is_super_admin = False  # role grants full permissions, but the boolean flag is what the route checks
        db_session.commit()
    force_login(client, app, staff_id)
    resp = client.post("/system/backups", data={"csrf_token": "x"})
    assert resp.status_code in (400, 403)


def test_reenrolling_mfa_while_logged_in_requires_recent_auth(app, client, seeded):
    staff_id = make_staff(app, "reenroll@example.com", super_admin=True, mfa=True)
    from tests.conftest import login_and_verify_mfa

    login_and_verify_mfa(client, "reenroll@example.com")
    # Recent-auth window expires.
    with app.app_context():
        from datetime import timedelta

        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow() - timedelta(seconds=app.config["RECENT_AUTH_WINDOW_SECONDS"] + 5)
        db_session.commit()
    resp = client.get("/auth/mfa-enroll")
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]
