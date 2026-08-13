"""Phase 9.5B Milestone 7 -- employee self-profile. No employee_id ever
appears in these routes -- every lookup resolves from the authenticated
session's own StaffUser, structurally preventing IDOR by construction (an
employee cannot reach another employee's profile by editing a URL, because
there is no ID in the URL to edit)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from app.auth.session import current_session, list_sessions_for_staff, load_current_staff, revoke_session_by_id
from app.employees.presence import employee_presence_state
from app.employees.queries import find_own_profile
from app.employees.services import update_own_profile
from app.security.rbac import require_login

bp = Blueprint("profile", __name__, url_prefix="/profile")


@bp.route("", methods=["GET"])
@require_login
def index():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    presence = employee_presence_state(profile.id) if profile else None
    return render_template("profile/index.html", staff=staff, profile=profile, presence=presence, error=None)


@bp.route("", methods=["POST"])
@require_login
def update():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    if profile is None:
        return jsonify({"error": "no_employee_profile"}), 404
    update_own_profile(
        profile, staff, display_name=request.form.get("display_name"), phone=request.form.get("phone")
    )
    return redirect(url_for("profile.index"))


@bp.route("/sessions", methods=["GET"])
@require_login
def sessions():
    staff = load_current_staff()
    current = current_session()
    rows = list_sessions_for_staff(staff.id)
    return render_template("profile/sessions.html", sessions=rows, current_session_id=current.id if current else None)


@bp.route("/sessions/<uuid:session_id>/revoke", methods=["POST"])
@require_login
def revoke_session_route(session_id):
    staff = load_current_staff()
    current = current_session()
    if current is not None and current.id == session_id:
        return jsonify({"error": "cannot_revoke_current_session_use_logout"}), 400
    rows = list_sessions_for_staff(staff.id)
    if not any(r.id == session_id for r in rows):
        return jsonify({"error": "not_found"}), 404
    revoke_session_by_id(session_id, reason="self_revoked")
    return redirect(url_for("profile.sessions"))
