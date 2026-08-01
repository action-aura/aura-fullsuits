"""Auth routes: login, logout, MFA verify/enroll, re-auth, password change,
invitation accept (Part E/F/H)."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.services import authenticate, find_staff_by_email
from app.auth.session import (
    COOKIE_NAME,
    create_session,
    has_recent_auth,
    load_current_staff,
    mark_mfa_verified,
    revoke_session,
)
from app.extensions import db_session
from app.models.base import utcnow
from app.models.staff import MfaCredential, MfaRecoveryCode, StaffInvitation, StaffUser
from app.security.mfa import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    provisioning_uri,
    verify_totp_code,
)
from app.security.passwords import PasswordPolicyError, hash_password, verify_password
from app.security.ratelimit import is_locked_out, record_attempt
from app.security.rbac import require_login
from app.security.tokens import hash_token

bp = Blueprint("auth", __name__, url_prefix="/auth")

_PENDING_MFA_SESSION_KEY = "pending_mfa_staff_id"
_PENDING_MFA_TTL_SECONDS = 300


def _safe_next(value: str | None) -> str | None:
    """Only ever redirect to a same-site relative path -- rejects absolute
    URLs and protocol-relative '//host' values to prevent open redirect."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return None
    return value


@bp.route("/login", methods=["GET"])
def login_form():
    if load_current_staff() is not None:
        return redirect(url_for("dashboard.index"))
    return render_template("auth/login.html", error=None)


@bp.route("/login", methods=["POST"])
def login_submit():
    email = request.form.get("email", "")
    password = request.form.get("password", "")
    staff, reason = authenticate(
        email,
        password,
        request.remote_addr,
        current_app.config["LOGIN_MAX_ATTEMPTS"],
        current_app.config["LOGIN_LOCKOUT_SECONDS"],
    )
    if staff is None:
        # Deliberately generic: never reveals whether the email exists (Part E).
        message = "Too many attempts -- try again later." if reason == "locked_out" else "Invalid email or password."
        return render_template("auth/login.html", error=message), 401

    has_mfa = staff.mfa_credential is not None and staff.mfa_credential.confirmed
    if has_mfa or staff.mfa_required:
        if not has_mfa:
            # MFA is mandatory for this account but not yet enrolled -- force enrollment before any session exists.
            session[_PENDING_MFA_SESSION_KEY] = str(staff.id)
            session["pending_mfa_expires"] = (utcnow() + timedelta(seconds=_PENDING_MFA_TTL_SECONDS)).isoformat()
            return redirect(url_for("auth.mfa_enroll_form"))
        session[_PENDING_MFA_SESSION_KEY] = str(staff.id)
        session["pending_mfa_expires"] = (utcnow() + timedelta(seconds=_PENDING_MFA_TTL_SECONDS)).isoformat()
        return redirect(url_for("auth.mfa_verify_form"))

    raw_token = create_session(staff)
    audit_record(
        actor_staff_user_id=staff.id,
        actor_role_snapshot=None,
        action_code="STAFF_LOGIN_SUCCESS",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
    response = redirect(_safe_next(request.args.get("next")) or url_for("dashboard.index"))
    response.set_cookie(
        COOKIE_NAME, raw_token, httponly=True, samesite="Lax", secure=current_app.config["SESSION_COOKIE_SECURE"]
    )
    return response


def _pending_mfa_staff() -> StaffUser | None:
    staff_id = session.get(_PENDING_MFA_SESSION_KEY)
    expires = session.get("pending_mfa_expires")
    if not staff_id or not expires:
        return None
    if utcnow().isoformat() > expires:
        session.pop(_PENDING_MFA_SESSION_KEY, None)
        return None
    return db_session.get(StaffUser, staff_id)


@bp.route("/mfa-verify", methods=["GET"])
def mfa_verify_form():
    if _pending_mfa_staff() is None:
        return redirect(url_for("auth.login_form"))
    return render_template("auth/mfa_verify.html", error=None)


@bp.route("/mfa-verify", methods=["POST"])
def mfa_verify_submit():
    staff = _pending_mfa_staff()
    if staff is None:
        return redirect(url_for("auth.login_form"))

    max_attempts = current_app.config["LOGIN_MAX_ATTEMPTS"]
    lockout_seconds = current_app.config["LOGIN_LOCKOUT_SECONDS"]
    if is_locked_out(staff.email, request.remote_addr, max_attempts, lockout_seconds):
        record_attempt(staff.email, request.remote_addr, success=False, reason="mfa_locked_out")
        return render_template("auth/mfa_verify.html", error="Too many attempts -- try again later."), 401

    code = request.form.get("code", "")
    raw_secret = decrypt_totp_secret(staff.mfa_credential.totp_secret_encrypted, current_app.config["SECRET_KEY"])
    ok = raw_secret is not None and verify_totp_code(raw_secret, code)
    if not ok:
        ok = _consume_recovery_code(staff, code)
    if not ok:
        record_attempt(staff.email, request.remote_addr, success=False, reason="mfa_invalid_code")
        return render_template("auth/mfa_verify.html", error="Invalid code."), 401
    record_attempt(staff.email, request.remote_addr, success=True)

    session.pop(_PENDING_MFA_SESSION_KEY, None)
    session.pop("pending_mfa_expires", None)
    raw_token = create_session(staff)
    mark_mfa_verified()
    staff.mfa_credential.last_used_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=staff.id,
        actor_role_snapshot=None,
        action_code="STAFF_LOGIN_MFA_SUCCESS",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
    response = redirect(url_for("dashboard.index"))
    response.set_cookie(
        COOKIE_NAME, raw_token, httponly=True, samesite="Lax", secure=current_app.config["SESSION_COOKIE_SECURE"]
    )
    return response


def _consume_recovery_code(staff: StaffUser, code: str) -> bool:
    if not code or staff.mfa_credential is None:
        return False
    code_hash = hash_recovery_code(code)
    for rc in staff.mfa_credential.recovery_codes:
        if rc.code_hash == code_hash and rc.used_at is None:
            rc.used_at = utcnow()
            db_session.commit()
            audit_record(
                actor_staff_user_id=staff.id,
                actor_role_snapshot=None,
                action_code="MFA_RECOVERY_CODE_USED",
                entity_type="staff_user",
                entity_public_id=str(staff.id),
            )
            return True
    return False


def _enroll_eligible_staff() -> StaffUser | None:
    """A staff account may enroll/re-enroll MFA when: (a) mid-login, MFA is
    freshly forced and no session exists yet, (b) already logged in with NO
    existing MFA credential (first-time voluntary enrollment -- nothing to
    prove recent possession of yet), or (c) already logged in WITH an
    existing credential AND a recent MFA confirmation. Re-enrolling an
    account that already has MFA, from a session with no recent
    confirmation, is refused -- otherwise a hijacked session could silently
    swap in an attacker's MFA device and lock the real owner out."""
    pending = _pending_mfa_staff()
    if pending is not None:
        return pending
    current = load_current_staff()
    if current is None:
        return None
    if current.mfa_credential is None:
        return current
    if has_recent_auth():
        return current
    return None


@bp.route("/mfa-enroll", methods=["GET"])
def mfa_enroll_form():
    staff = _enroll_eligible_staff()
    if staff is None:
        if load_current_staff() is not None:
            return redirect(url_for("auth.reauth_form", next=request.path))
        return redirect(url_for("auth.login_form"))
    raw_secret = generate_totp_secret()
    session["enroll_secret"] = raw_secret
    uri = provisioning_uri(raw_secret, staff.email)
    return render_template("auth/mfa_enroll.html", provisioning_uri=uri, secret=raw_secret, error=None)


@bp.route("/mfa-enroll", methods=["POST"])
def mfa_enroll_submit():
    staff = _enroll_eligible_staff()
    if staff is None:
        if load_current_staff() is not None:
            return redirect(url_for("auth.reauth_form", next=request.path))
        return redirect(url_for("auth.login_form"))
    raw_secret = session.get("enroll_secret")
    code = request.form.get("code", "")
    if not raw_secret or not verify_totp_code(raw_secret, code):
        return render_template(
            "auth/mfa_enroll.html",
            provisioning_uri=provisioning_uri(raw_secret or generate_totp_secret(), staff.email),
            secret=raw_secret,
            error="Invalid code -- scan the QR code again and try the current 6-digit code.",
        ), 401

    # Re-enrollment revokes the old credential (Part F).
    if staff.mfa_credential is not None:
        db_session.delete(staff.mfa_credential)
        db_session.flush()

    credential = MfaCredential(
        staff_user_id=staff.id,
        totp_secret_encrypted=encrypt_totp_secret(raw_secret, current_app.config["SECRET_KEY"]),
        enrolled_at=utcnow(),
        confirmed=True,
    )
    db_session.add(credential)
    db_session.flush()

    plain_codes = generate_recovery_codes()
    for plain in plain_codes:
        db_session.add(MfaRecoveryCode(mfa_credential_id=credential.id, code_hash=hash_recovery_code(plain)))
    db_session.commit()
    session.pop("enroll_secret", None)

    audit_record(
        actor_staff_user_id=staff.id,
        actor_role_snapshot=None,
        action_code="MFA_ENROLLED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )

    if _pending_mfa_staff() is not None:
        session.pop(_PENDING_MFA_SESSION_KEY, None)
        session.pop("pending_mfa_expires", None)
        raw_token = create_session(staff)
        mark_mfa_verified()
        response = redirect(url_for("auth.mfa_recovery_codes_shown"))
        response.set_cookie(
            COOKIE_NAME, raw_token, httponly=True, samesite="Lax", secure=current_app.config["SESSION_COOKIE_SECURE"]
        )
        session["_just_generated_recovery_codes"] = plain_codes
        return response

    mark_mfa_verified()
    session["_just_generated_recovery_codes"] = plain_codes
    return redirect(url_for("auth.mfa_recovery_codes_shown"))


@bp.route("/mfa-recovery-codes", methods=["GET"])
@require_login
def mfa_recovery_codes_shown():
    codes = session.pop("_just_generated_recovery_codes", None)
    return render_template("auth/mfa_recovery_codes.html", codes=codes)


@bp.route("/reauth", methods=["GET"])
@require_login
def reauth_form():
    return render_template("auth/reauth.html", error=None, next=request.args.get("next", "/"))


@bp.route("/reauth", methods=["POST"])
@require_login
def reauth_submit():
    staff = load_current_staff()
    code = request.form.get("code", "")
    if staff.mfa_credential is None or not staff.mfa_credential.confirmed:
        return render_template(
            "auth/reauth.html", error="MFA is not enabled on this account -- enable it to perform this action.",
            next=request.form.get("next", "/"),
        ), 403
    raw_secret = decrypt_totp_secret(staff.mfa_credential.totp_secret_encrypted, current_app.config["SECRET_KEY"])
    ok = raw_secret is not None and verify_totp_code(raw_secret, code)
    if not ok:
        return render_template(
            "auth/reauth.html", error="Invalid code.", next=request.form.get("next", "/")
        ), 401
    mark_mfa_verified()
    return redirect(_safe_next(request.form.get("next")) or url_for("dashboard.index"))


@bp.route("/logout", methods=["POST"])
@require_login
def logout():
    raw_token = request.cookies.get(COOKIE_NAME)
    staff = load_current_staff()
    if raw_token:
        revoke_session(raw_token, reason="logout")
    if staff is not None:
        audit_record(
            actor_staff_user_id=staff.id,
            actor_role_snapshot=None,
            action_code="STAFF_LOGOUT",
            entity_type="staff_user",
            entity_public_id=str(staff.id),
        )
    response = redirect(url_for("auth.login_form"))
    response.delete_cookie(COOKIE_NAME)
    return response


@bp.route("/change-password", methods=["GET"])
@require_login
def change_password_form():
    return render_template("auth/change_password.html", error=None)


@bp.route("/change-password", methods=["POST"])
@require_login
def change_password_submit():
    staff = load_current_staff()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    if not verify_password(current_password, staff.password_hash):
        return render_template("auth/change_password.html", error="Current password is incorrect."), 401
    try:
        staff.password_hash = hash_password(new_password)
    except PasswordPolicyError as exc:
        return render_template("auth/change_password.html", error=str(exc)), 400
    staff.must_change_password = False
    staff.session_version += 1  # invalidates every other existing session
    db_session.commit()
    raw_token = create_session(staff)
    audit_record(
        actor_staff_user_id=staff.id,
        actor_role_snapshot=None,
        action_code="STAFF_PASSWORD_CHANGED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
    response = redirect(url_for("dashboard.index"))
    response.set_cookie(
        COOKIE_NAME, raw_token, httponly=True, samesite="Lax", secure=current_app.config["SESSION_COOKIE_SECURE"]
    )
    return response


@bp.route("/accept-invitation/<token>", methods=["GET"])
def accept_invitation_form(token: str):
    invitation = _find_valid_invitation(token)
    if invitation is None:
        return render_template("auth/accept_invitation.html", error="Invitation is invalid or has expired.", token=None)
    return render_template("auth/accept_invitation.html", error=None, token=token, email=invitation.email)


@bp.route("/accept-invitation/<token>", methods=["POST"])
def accept_invitation_submit(token: str):
    invitation = _find_valid_invitation(token)
    if invitation is None:
        return render_template("auth/accept_invitation.html", error=_("Invitation is invalid or has expired."), token=None), 400

    display_name = request.form.get("display_name", "").strip()
    password = request.form.get("password", "")
    if not display_name:
        return render_template(
            "auth/accept_invitation.html", error="Name is required.", token=token, email=invitation.email
        ), 400
    try:
        password_hash = hash_password(password)
    except PasswordPolicyError as exc:
        return render_template(
            "auth/accept_invitation.html", error=str(exc), token=token, email=invitation.email
        ), 400

    if find_staff_by_email(invitation.email) is not None:
        return render_template(
            "auth/accept_invitation.html", error="An account already exists for this email.", token=token, email=invitation.email
        ), 400

    from app.staff.services import create_staff_from_invitation  # local import avoids a package-load cycle

    staff, profile = create_staff_from_invitation(invitation, display_name, password_hash)
    invitation.accepted_at = utcnow()
    invitation.created_staff_user_id = staff.id
    db_session.commit()

    audit_record(
        actor_staff_user_id=staff.id,
        actor_role_snapshot=None,
        action_code="STAFF_INVITATION_ACCEPTED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
    return redirect(url_for("auth.login_form"))


def _find_valid_invitation(token: str) -> StaffInvitation | None:
    if not token:
        return None
    stmt = select(StaffInvitation).where(StaffInvitation.token_hash == hash_token(token))
    invitation = db_session.execute(stmt).scalars().first()
    if invitation is None or invitation.accepted_at is not None or invitation.revoked_at is not None:
        return None
    if invitation.expires_at <= utcnow():
        return None
    return invitation
