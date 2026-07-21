"""Staff management routes (Part G/H)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.staff import Role, StaffInvitation, StaffUser
from app.security.rbac import require_permission, require_recent_auth
from app.staff.services import SelfEscalationError, assign_roles, create_invitation, disable_staff, reset_mfa, revoke_invitation

bp = Blueprint("staff", __name__, url_prefix="/staff")


@bp.route("", methods=["GET"])
@require_permission("staff.view")
def list_staff():
    staff_list = db_session.execute(select(StaffUser).order_by(StaffUser.created_at.desc())).scalars().all()
    invitations = db_session.execute(
        select(StaffInvitation).where(StaffInvitation.accepted_at.is_(None)).order_by(StaffInvitation.created_at.desc())
    ).scalars().all()
    return render_template("staff/list.html", staff_list=staff_list, invitations=invitations)


@bp.route("/<uuid:staff_id>", methods=["GET"])
@require_permission("staff.view")
def detail(staff_id):
    staff = db_session.get(StaffUser, staff_id)
    if staff is None:
        return jsonify({"error": "not_found"}), 404
    roles = db_session.execute(select(Role)).scalars().all()
    return render_template("staff/detail.html", staff=staff, roles=roles)


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
    return render_template("staff/invitation_created.html", invitation=invitation, link=link)


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
    disable_staff(staff, request.form.get("reason", ""), actor.id)
    return redirect(url_for("staff.detail", staff_id=staff_id))


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
