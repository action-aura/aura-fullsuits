from __future__ import annotations

from tests.conftest import get_csrf, login, login_and_verify_mfa, make_staff, totp_code


def test_bootstrap_creates_no_default_credentials(app):
    """No staff exists until explicitly created -- there is no seed-time staff user."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        assert db_session.query(StaffUser).count() == 0


def test_login_wrong_password_rejected(app, client, seeded):
    make_staff(app, "user@example.com", password="Correct-Password-1!")
    resp = login(client, "user@example.com", password="wrong-password")
    assert resp.status_code == 401
    assert b"Invalid email or password" in resp.data


def test_login_unknown_email_gives_generic_error(app, client, seeded):
    resp = login(client, "nobody@example.com", password="whatever")
    assert resp.status_code == 401
    assert b"Invalid email or password" in resp.data  # never reveals whether the email exists


def test_login_throttling_locks_out_after_max_attempts(app, client, seeded):
    make_staff(app, "throttle@example.com", password="Correct-Password-1!")
    for _ in range(5):
        login(client, "throttle@example.com", password="wrong")
    resp = login(client, "throttle@example.com", password="Correct-Password-1!")
    assert resp.status_code == 401
    assert b"Too many attempts" in resp.data


def test_login_disabled_account_rejected(app, client, seeded):
    staff_id = make_staff(app, "disabled@example.com", password="Correct-Password-1!")
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        staff.is_active = False
        staff.disabled_at = utcnow()
        db_session.commit()
    resp = login(client, "disabled@example.com", password="Correct-Password-1!")
    assert resp.status_code == 401


def test_login_without_mfa_goes_straight_to_dashboard(app, client, seeded):
    make_staff(app, "nomfa@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    resp = login(client, "nomfa@example.com", password="Correct-Password-1!")
    assert resp.status_code == 302
    assert "/mfa" not in resp.headers["Location"]


def test_super_admin_login_requires_mfa_verify_step(app, client, seeded):
    make_staff(app, "admin@example.com", super_admin=True, password="Correct-Password-1!", mfa=True)
    resp = login(client, "admin@example.com", password="Correct-Password-1!")
    assert resp.status_code == 302
    assert "mfa-verify" in resp.headers["Location"]


def test_mfa_verify_wrong_code_rejected(app, client, seeded):
    make_staff(app, "admin2@example.com", super_admin=True, password="Correct-Password-1!", mfa=True)
    login(client, "admin2@example.com", password="Correct-Password-1!")
    page = client.get("/auth/mfa-verify")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": "000000"})
    assert resp.status_code == 401


def test_mfa_verify_correct_code_logs_in(app, client, seeded):
    make_staff(app, "admin3@example.com", super_admin=True, password="Correct-Password-1!", mfa=True)
    resp = login_and_verify_mfa(client, "admin3@example.com", password="Correct-Password-1!")
    assert resp.status_code == 302
    dash = client.get("/", follow_redirects=True)
    assert dash.status_code == 200


def test_recovery_code_can_be_used_once(app, client, seeded):
    staff_id = make_staff(app, "admin4@example.com", super_admin=True, password="Correct-Password-1!", mfa=True)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import MfaCredential, MfaRecoveryCode
        from app.security.mfa import hash_recovery_code

        cred = db_session.query(MfaCredential).filter_by(staff_user_id=staff_id).first()
        db_session.add(MfaRecoveryCode(mfa_credential_id=cred.id, code_hash=hash_recovery_code("abcd-1234")))
        db_session.commit()

    login(client, "admin4@example.com", password="Correct-Password-1!")
    page = client.get("/auth/mfa-verify")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": "abcd-1234"})
    assert resp.status_code == 302

    # A second login attempt with the SAME recovery code must fail (one-time use).
    client.post("/auth/logout", data={"csrf_token": csrf})
    login(client, "admin4@example.com", password="Correct-Password-1!")
    page2 = client.get("/auth/mfa-verify")
    csrf2 = get_csrf(page2.get_data(as_text=True))
    resp2 = client.post("/auth/mfa-verify", data={"csrf_token": csrf2, "code": "abcd-1234"})
    assert resp2.status_code == 401


def test_session_idle_timeout_expires_session(app, client, seeded):
    from datetime import timedelta

    make_staff(app, "idle@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    login(client, "idle@example.com", password="Correct-Password-1!")
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.last_seen_at = utcnow() - timedelta(seconds=app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 5)
        db_session.commit()
    resp = client.get("/")
    assert resp.status_code == 302
    assert "login" in resp.headers["Location"]


def test_logout_revokes_session(app, client, seeded):
    make_staff(app, "logout@example.com", password="Correct-Password-1!", role_codes=["VIEWER"])
    login(client, "logout@example.com", password="Correct-Password-1!")
    page = client.get("/")
    csrf = get_csrf(page.get_data(as_text=True))
    client.post("/auth/logout", data={"csrf_token": csrf})
    resp = client.get("/")
    assert resp.status_code == 302


def test_password_change_revokes_other_sessions(app, seeded):
    from app import create_app

    staff_id = make_staff(app, "pwchange@example.com", password="Old-Password-1!", role_codes=["VIEWER"])
    client_a = app.test_client()
    client_b = app.test_client()
    login(client_a, "pwchange@example.com", password="Old-Password-1!")
    login(client_b, "pwchange@example.com", password="Old-Password-1!")

    page = client_a.get("/auth/change-password")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client_a.post(
        "/auth/change-password",
        data={"csrf_token": csrf, "current_password": "Old-Password-1!", "new_password": "New-Password-99!"},
    )
    assert resp.status_code == 302

    resp_b = client_b.get("/")
    assert resp_b.status_code == 302  # client_b's old session is now invalid
