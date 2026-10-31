"""Staff management routes (Part G/H)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.staff import Role, StaffInvitation, StaffUser
from app.security.rbac import require_permission, require_recent_auth
from app.security.super_admin_guard import LastSuperAdminError
from app.staff import list_queries
from app.mail.invitations import queue_invitation_email
from app.staff.services import SelfEscalationError, assign_roles, create_invitation, disable_staff, reset_mfa, revoke_invitation

bp = Blueprint("staff", __name__, url_prefix="/staff")


@bp.route("", methods=["GET"])
@require_permission("staff.view")
def list_staff():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_staff_accounts(
        page=request.args.get("page", 1, type=int), status=status_filter, search=search, sort=sort, direction=direction
    )
    invitations = db_session.execute(
        select(StaffInvitation).where(StaffInvitation.accepted_at.is_(None)).order_by(StaffInvitation.created_at.desc())
    ).scalars().all()
    return render_template(
        "staff/list.html", result=result, status_filter=status_filter, search=search, invitations=invitations
    )


@bp.route("/<uuid:staff_id>", methods=["GET"])
@require_permission("staff.view")
def detail(staff_id):
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    roles = db_session.execute(select(Role)).scalars().all()
    from app.contracts.service import staff_is_bound_by_seller_agreement
    from app.seller_id.documents import current_photo

    return render_template(
        "staff/detail.html", staff=staff, roles=roles, id_photo=current_photo(staff.id),
        is_seller=staff_is_bound_by_seller_agreement(staff),
    )


@bp.route("/invite", methods=["POST"])
@require_permission("staff.create")
@require_recent_auth
def invite():
    actor = load_current_staff()
    email = request.form.get("email", "").strip()
    role_codes = request.form.getlist("role_codes")
    try:
        invitation, raw_token = create_invitation(email, role_codes, actor.id)
    except SelfEscalationError as exc:
        return jsonify({"error": str(exc)}), 403
    link = url_for("auth.accept_invitation_form", token=raw_token, _external=True)
    # Real-email wave: when this server can send email the link is mailed too.
    # It is shown on the page either way, so a mail problem never locks anyone out.
    email_queued = queue_invitation_email(invitation, link)
    return render_template("staff/invitation_created.html", invitation=invitation, link=link, email_queued=email_queued)


@bp.route("/invitations/<uuid:invitation_id>/revoke", methods=["POST"])
@require_permission("staff.create")
def revoke_invite(invitation_id):
    actor = load_current_staff()
    invitation = db_session.get(StaffInvitation, invitation_id)
    if invitation is None:
        return jsonify({"error": "not_found"}), 404
    revoke_invitation(invitation, actor.id)
    return redirect(url_for("staff.list_staff"))


@bp.route("/<uuid:staff_id>/roles", methods=["POST"])
@require_permission("staff.assign_roles")
@require_recent_auth
def update_roles(staff_id):
    actor = load_current_staff()
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    role_codes = request.form.getlist("role_codes")
    try:
        assign_roles(staff, role_codes, actor.id)
    except SelfEscalationError as exc:
        return jsonify({"error": str(exc)}), 403
    return redirect(url_for("staff.detail", staff_id=staff_id))


@bp.route("/<uuid:staff_id>/disable", methods=["POST"])
@require_permission("staff.disable")
@require_recent_auth
def disable(staff_id):
    actor = load_current_staff()
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    if staff.id == actor.id:
        return jsonify({"error": "cannot_disable_self"}), 400
    try:
        disable_staff(staff, request.form.get("reason", ""), actor.id)
    except LastSuperAdminError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("staff.detail", staff_id=staff_id))


@bp.route("/<uuid:staff_id>/password-reset-link", methods=["POST"])
@require_permission("staff.reset_mfa")
@require_recent_auth
def password_reset_link_route(staff_id):
    """The admin fallback that works with no email: shows a one-time password
    reset link ONCE, for the admin to hand over through a trusted channel. Same
    permission as Reset MFA. Never for your own account -- resetting your own
    password this way would turn a stolen admin session into a password."""
    from app.audit.services import record as audit_record
    from app.auth.password_reset import build_link, make_token

    actor = load_current_staff()
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    if staff.id == actor.id:
        return jsonify({"error": "cannot_reset_own_password_here"}), 403
    if not staff.is_active or staff.disabled_at is not None:
        return jsonify({"error": "account_disabled"}), 400
    link = build_link(make_token(staff))
    audit_record(
        actor_staff_user_id=actor.id,
        actor_role_snapshot=None,
        action_code="STAFF_PASSWORD_RESET_LINK_CREATED",
        entity_type="staff_user",
        entity_public_id=str(staff.id),
    )
    return render_template("staff/password_reset_link.html", staff=staff, link=link)


@bp.route("/<uuid:staff_id>/reset-mfa", methods=["POST"])
@require_permission("staff.reset_mfa")
@require_recent_auth
def reset_mfa_route(staff_id):
    actor = load_current_staff()
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    reset_mfa(staff, actor.id)
    return redirect(url_for("staff.detail", staff_id=staff_id))
