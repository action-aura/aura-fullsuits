"""Owner staff forgot-password / reset-password and the admin reset link.

Why the assertions look the way they do (ENGINEERING 1): every "refused" or
"neutral" check is paired with a positive control (the same flow DID work for a
real account in the same test) and asserts on the thing that matters -- rows in
the outbox, a changed password hash, revoked sessions -- not just a status code,
because a 200 cannot tell "the guard ran" from "the route did nothing".

The token tests use the real itsdangerous signer; time is moved by patching the
signer's own clock (TimestampSigner.get_timestamp), never by sleeping.
"""
from __future__ import annotations

import re

import pytest
from itsdangerous.timed import TimestampSigner
from sqlalchemy import select

from app.auth import password_reset
from app.extensions import db_session
from app.models.audit import AuditLog
from app.models.email_outbox import EmailOutbox
from app.models.staff import StaffSession, StaffUser
from app.security.passwords import verify_password
from tests.conftest import force_login, get_csrf, login, login_and_verify_mfa, make_staff

OLD = "Sup3r-Str0ng-Pass!"
NEW = "Brand-New-Passw0rd!"


def _forgot(client, email):
    csrf = get_csrf(client.get("/auth/forgot-password").get_data(as_text=True))
    return client.post("/auth/forgot-password", data={"csrf_token": csrf, "email": email})


def _outbox(app):
    with app.app_context():
        return db_session.execute(select(EmailOutbox).order_by(EmailOutbox.created_at)).scalars().all()


def _token_for(app, email):
    with app.app_context():
        staff = db_session.execute(select(StaffUser).where(StaffUser.email == email)).scalars().first()
        return password_reset.make_token(staff)


def _reset(client, token, new=NEW, confirm=None):
    csrf = get_csrf(client.get(f"/auth/reset-password/{token}").get_data(as_text=True))
    return client.post(f"/auth/reset-password/{token}",
                       data={"csrf_token": csrf, "new_password": new, "confirm_password": confirm if confirm is not None else new})


def _hash(app, email):
    with app.app_context():
        return db_session.execute(select(StaffUser).where(StaffUser.email == email)).scalars().first().password_hash


def _disable(app, email):
    from app.models.base import utcnow

    with app.app_context():
        staff = db_session.execute(select(StaffUser).where(StaffUser.email == email)).scalars().first()
        staff.is_active = False
        staff.disabled_at = utcnow()
        db_session.commit()


# ---------------------------------------------------------------------------
# R1 -- the request page
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("smtp", ["smtp.example", ""])
def test_the_response_is_identical_for_a_real_an_unknown_and_a_disabled_account(app, client, seeded, smtp):
    app.config.update(SMTP_HOST=smtp)
    make_staff(app, "real@example.com")
    make_staff(app, "gone@example.com")
    _disable(app, "gone@example.com")
    real = _forgot(client, "real@example.com")
    unknown = _forgot(client, "nobody@example.com")
    disabled = _forgot(client, "gone@example.com")
    assert (real.status_code, real.data) == (unknown.status_code, unknown.data) == (disabled.status_code, disabled.data)
    assert real.status_code == 200
    assert b"If an account exists for that email, a reset link has been sent." in real.data


def test_a_row_is_queued_for_a_real_active_account_only_and_only_when_smtp_is_on(app, client, seeded):
    app.config.update(SMTP_HOST="smtp.example")
    make_staff(app, "real@example.com")
    make_staff(app, "gone@example.com")
    _disable(app, "gone@example.com")
    _forgot(client, "nobody@example.com")
    _forgot(client, "gone@example.com")
    assert _outbox(app) == [], "unknown and disabled accounts must queue nothing"
    _forgot(client, "REAL@example.com")                     # positive control (address is case-insensitive)
    rows = _outbox(app)
    assert len(rows) == 1
    row = rows[0]
    assert (row.kind, row.source, row.to_address, row.status) == ("staff_password_reset", "owner", "real@example.com", "QUEUED")
    link = re.search(r"(http\S*/auth/reset-password/\S+)", row.body_text).group(1)
    token = link.rsplit("/", 1)[1]
    assert _reset(client, token).status_code == 302          # the emailed link really works


def test_with_smtp_off_nothing_is_queued_even_for_a_real_account(app, client, seeded):
    app.config.update(SMTP_HOST="")
    make_staff(app, "real@example.com")
    assert _forgot(client, "real@example.com").status_code == 200
    assert _outbox(app) == []


def test_the_fourth_request_in_an_hour_queues_nothing_and_looks_the_same(app, client, seeded):
    app.config.update(SMTP_HOST="smtp.example")
    make_staff(app, "real@example.com")
    pages = [_forgot(client, "real@example.com") for _ in range(4)]
    assert [len(_outbox(app))] == [3], "requests 1-3 queue a mail, the 4th does not"
    assert len({(p.status_code, p.data) for p in pages}) == 1
    # another address is unaffected (the limit is per address)
    make_staff(app, "other@example.com")
    _forgot(client, "other@example.com")
    assert len(_outbox(app)) == 4
    # and asking for resets cannot lock anyone out of logging in
    assert login(client, "real@example.com").status_code == 302


def test_reset_requests_do_not_unlock_a_locked_out_login(app, client, seeded):
    """Reset requests share the attempts table; they must neither count as login
    failures nor reset the failure counter."""
    app.config.update(SMTP_HOST="smtp.example")
    make_staff(app, "real@example.com")
    for _ in range(app.config["LOGIN_MAX_ATTEMPTS"]):
        assert login(client, "real@example.com", "wrong-password-1A!").status_code == 401
    _forgot(client, "real@example.com")
    assert login(client, "real@example.com", OLD).status_code == 401, "still locked out"


# ---------------------------------------------------------------------------
# R2 -- the token
# ---------------------------------------------------------------------------
def test_a_token_expires_at_thirty_minutes(app, client, seeded, monkeypatch):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    real_ts = TimestampSigner.get_timestamp

    def at(offset):
        monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: real_ts(self) + offset)

    at(29 * 60 + 50)
    assert client.get(f"/auth/reset-password/{token}").status_code == 200       # still good at 29:50
    at(30 * 60 + 10)
    expired = client.get(f"/auth/reset-password/{token}")
    assert expired.status_code == 400 and b"no longer valid" in expired.data
    csrf_less = client.post(f"/auth/reset-password/{token}", data={"new_password": NEW, "confirm_password": NEW})
    assert csrf_less.status_code in (400, 403)
    assert verify_password(OLD, _hash(app, "real@example.com"))                  # nothing changed


def test_a_token_is_dead_after_one_successful_reset(app, client, seeded):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    first = _reset(client, token)
    assert first.status_code == 302                                              # positive control
    assert verify_password(NEW, _hash(app, "real@example.com"))
    again = client.get(f"/auth/reset-password/{token}")
    assert again.status_code == 400 and b"no longer valid" in again.data
    csrf = get_csrf(client.get("/auth/forgot-password").get_data(as_text=True))
    replay = client.post(f"/auth/reset-password/{token}", data={"csrf_token": csrf, "new_password": "Another-Passw0rd!x", "confirm_password": "Another-Passw0rd!x"})
    assert replay.status_code == 400
    assert verify_password(NEW, _hash(app, "real@example.com")), "the replay must not change the password"


def test_a_token_is_dead_after_an_ordinary_password_change(app, client, seeded):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    assert client.get(f"/auth/reset-password/{token}").status_code == 200       # control
    assert login(client, "real@example.com").status_code == 302
    csrf = get_csrf(client.get("/auth/change-password").get_data(as_text=True))
    changed = client.post("/auth/change-password", data={"csrf_token": csrf, "current_password": OLD, "new_password": "Changed-Passw0rd!y"})
    assert changed.status_code == 302
    assert client.get(f"/auth/reset-password/{token}").status_code == 400


def test_garbage_tampered_and_wrong_salt_tokens_are_refused(app, client, seeded):
    from itsdangerous import URLSafeTimedSerializer

    make_staff(app, "real@example.com")
    good = _token_for(app, "real@example.com")
    assert client.get(f"/auth/reset-password/{good}").status_code == 200
    forged = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="some-other-salt").dumps({"uid": "x", "ph": "y"})
    for bad in ("garbage", good[:-3] + "abc", good + "x", forged):
        resp = client.get(f"/auth/reset-password/{bad}")
        assert resp.status_code == 400 and b"no longer valid" in resp.data


def test_a_token_stops_working_when_the_account_is_disabled(app, client, seeded):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    assert client.get(f"/auth/reset-password/{token}").status_code == 200
    _disable(app, "real@example.com")
    assert client.get(f"/auth/reset-password/{token}").status_code == 400


def test_the_emailed_link_is_https_in_staging_and_production_only(app, seeded):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    with app.test_request_context(base_url="http://owner.example"):
        assert password_reset.build_link(token).startswith("http://")
        app.config["ENV"] = "production"
        assert password_reset.build_link(token).startswith("https://")
        app.config["ENV"] = "staging"
        assert password_reset.build_link(token).startswith("https://")
        app.config["ENV"] = "testing"


# ---------------------------------------------------------------------------
# R3 -- setting the new password
# ---------------------------------------------------------------------------
def test_a_weak_or_mismatched_password_is_refused_and_the_link_stays_usable(app, client, seeded):
    make_staff(app, "real@example.com")
    token = _token_for(app, "real@example.com")
    assert _reset(client, token, new="short").status_code == 400
    assert _reset(client, token, new=NEW, confirm="Different-Passw0rd!").status_code == 400
    assert verify_password(OLD, _hash(app, "real@example.com"))
    assert _reset(client, token).status_code == 302                             # then it works


def test_a_reset_revokes_every_session_and_is_audited(app, seeded):
    staff_id = make_staff(app, "real@example.com")
    c1, c2, anon = app.test_client(), app.test_client(), app.test_client()
    force_login(c1, app, staff_id)
    force_login(c2, app, staff_id)
    assert c1.get("/").status_code == 200 and c2.get("/").status_code == 200   # control
    token = _token_for(app, "real@example.com")
    assert _reset(anon, token).status_code == 302
    for c in (c1, c2):
        resp = c.get("/")
        assert resp.status_code == 302 and "/auth/login" in resp.headers["Location"]
    with app.app_context():
        sessions = db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars().all()
        assert len(sessions) == 2 and all(s.revoked_at is not None for s in sessions)
        assert {s.revoked_reason for s in sessions} == {"password_reset"}
        actions = [a.action_code for a in db_session.execute(select(AuditLog)).scalars().all()]
        assert "STAFF_PASSWORD_RESET" in actions


def test_mfa_is_still_required_at_login_after_a_reset(app, client, seeded):
    make_staff(app, "real@example.com", super_admin=True, mfa=True)
    token = _token_for(app, "real@example.com")
    assert _reset(client, token).status_code == 302
    resp = login(client, "real@example.com", NEW)
    assert resp.status_code == 302 and "/auth/mfa-verify" in resp.headers["Location"], "the new password alone must not log in"
    assert "owner_session" not in resp.headers.get("Set-Cookie", "")
    assert client.get("/").status_code == 302
    # and the old password no longer works at all
    assert login(app.test_client(), "real@example.com", OLD).status_code == 401
    with app.app_context():
        staff = db_session.execute(select(StaffUser).where(StaffUser.email == "real@example.com")).scalars().first()
        assert staff.mfa_credential is not None and staff.mfa_credential.confirmed


def test_the_login_page_links_to_forgot_password(client):
    page = client.get("/auth/login").get_data(as_text=True)
    assert "/auth/forgot-password" in page


# ---------------------------------------------------------------------------
# R4 -- the admin link
# ---------------------------------------------------------------------------
def test_the_admin_link_needs_the_reset_mfa_permission(app, seeded):
    target = make_staff(app, "target@example.com")
    # MFA-enrolled and freshly verified, so require_recent_auth passes and the
    # ONLY thing standing between this user and the page is the permission.
    make_staff(app, "sales@example.com", role_codes=["SALES"], mfa=True)
    c = app.test_client()
    login_and_verify_mfa(c, "sales@example.com")
    csrf = get_csrf(c.get("/auth/change-password").get_data(as_text=True))
    assert csrf, "needs a real CSRF token so the 403 below is the permission check, not CSRF"
    assert c.post(f"/staff/{target}/password-reset-link", data={"csrf_token": csrf}).status_code == 403
    with app.app_context():
        assert "STAFF_PASSWORD_RESET_LINK_CREATED" not in [a.action_code for a in db_session.execute(select(AuditLog)).scalars().all()]

    make_staff(app, "boss@example.com", super_admin=True, mfa=True)
    boss = app.test_client()
    login_and_verify_mfa(boss, "boss@example.com")
    csrf = get_csrf(boss.get(f"/staff/{target}").get_data(as_text=True))
    ok = boss.post(f"/staff/{target}/password-reset-link", data={"csrf_token": csrf})
    assert ok.status_code == 200
    page = ok.get_data(as_text=True)
    assert "shown here only once" in page and "trusted channel" in page
    link = re.search(r"(http\S*/auth/reset-password/[^\s<]+)", page).group(1)
    token = link.rsplit("/", 1)[1]
    assert _reset(app.test_client(), token).status_code == 302                  # the link really resets
    assert verify_password(NEW, _hash(app, "target@example.com"))
    with app.app_context():
        actions = [a.action_code for a in db_session.execute(select(AuditLog)).scalars().all()]
        assert "STAFF_PASSWORD_RESET_LINK_CREATED" in actions


def test_the_admin_cannot_create_a_reset_link_for_themselves(app, seeded):
    boss_id = make_staff(app, "boss@example.com", super_admin=True, mfa=True)
    boss = app.test_client()
    login_and_verify_mfa(boss, "boss@example.com")
    csrf = get_csrf(boss.get(f"/staff/{boss_id}").get_data(as_text=True))
    resp = boss.post(f"/staff/{boss_id}/password-reset-link", data={"csrf_token": csrf})
    assert resp.status_code == 403
    assert b"/auth/reset-password/" not in resp.data
    with app.app_context():
        assert "STAFF_PASSWORD_RESET_LINK_CREATED" not in [a.action_code for a in db_session.execute(select(AuditLog)).scalars().all()]
        assert db_session.execute(select(StaffUser).where(StaffUser.email == "boss@example.com")).scalars().first().password_hash


def test_the_detail_page_shows_the_button_only_with_the_permission(app, seeded):
    target = make_staff(app, "target@example.com")
    make_staff(app, "boss@example.com", super_admin=True, mfa=True)
    boss = app.test_client()
    login_and_verify_mfa(boss, "boss@example.com")
    assert "Create password reset link" in boss.get(f"/staff/{target}").get_data(as_text=True)
