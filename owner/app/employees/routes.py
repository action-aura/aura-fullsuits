"""Phase 9.5B Milestones 5/6/9/10/11/13 -- management employee list/detail/
dashboard/lifecycle/session routes. Server-side RBAC on every route (UI
visibility is never the only control, Non-Negotiable Principle 6)."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import list_sessions_for_staff, load_current_staff, revoke_all_sessions_for_staff, revoke_session_by_id
from app.employees.dashboard import get_employee_dashboard_metrics
from app.employees.presence import bulk_presence_states, employee_presence_state
from app.employees.queries import list_employees
from app.employees.services import (
    InvalidEmploymentTransitionError,
    activate_employee,
    archive_employee,
    reactivate_employee,
    suspend_employee,
    terminate_employee,
    update_employee_profile,
)
from app.extensions import db_session
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.commissions import CommissionPlan
from app.models.employees import EmployeeProfile
from app.models.staff import Role, StaffInvitation, StaffSession, StaffUser
from app.security.rbac import require_permission, require_recent_auth
from app.security.super_admin_guard import LastSuperAdminError
from app.staff.services import EmployeeDraftValidationError, create_employee_invitation, reissue_employee_invitation

bp = Blueprint("employees", __name__, url_prefix="/employees")


def _manager_options():
    return db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.employment_status != "ARCHIVED").order_by(EmployeeProfile.full_name)
    ).scalars().all()


def _commission_plan_options():
    return db_session.execute(select(CommissionPlan).where(CommissionPlan.is_active.is_(True))).scalars().all()


def _role_options():
    return db_session.execute(select(Role).order_by(Role.name)).scalars().all()


@bp.route("", methods=["GET"])
@require_permission("employees.view_all")
def list_view():
    page = request.args.get("page", 1, type=int)
    filters = dict(
        search=request.args.get("q") or None,
        department=request.args.get("department") or None,
        employment_status=request.args.get("employment_status") or None,
        role_code=request.args.get("role_code") or None,
        presence=request.args.get("presence") or None,
        include_archived=request.args.get("include_archived") == "1",
        sort=request.args.get("sort", "full_name"),
    )
    result = list_employees(page=page, **filters)
    ids = [row.id for row in result["rows"]]
    presence_map = bulk_presence_states(ids)
    return render_template(
        "employees/list.html", result=result, presence_map=presence_map, filters=filters,
        departments=_departments(), roles=_role_options(),
    )


def _departments() -> list[str]:
    rows = db_session.execute(
        select(EmployeeProfile.department).where(EmployeeProfile.department.is_not(None)).distinct()
    ).scalars().all()
    return sorted(rows)


@bp.route("/new", methods=["GET"])
@require_permission("employees.create")
def new_form():
    return render_template("employees/new.html", error=None, roles=_role_options(), managers=_manager_options(), plans=_commission_plan_options())


@bp.route("", methods=["POST"])
@require_permission("employees.create")
@require_recent_auth
def create():
    actor = load_current_staff()
    email = request.form.get("email", "").strip()
    role_codes = request.form.getlist("role_codes")
    employee_fields = {
        "employee_number": request.form.get("employee_number", "").strip(),
        "full_name": request.form.get("full_name", "").strip(),
        "phone": request.form.get("phone", "").strip() or None,
        "job_title": request.form.get("job_title", "").strip() or None,
        "department": request.form.get("department", "").strip() or None,
        "employment_start_date": request.form.get("employment_start_date", "").strip(),
        "manager_employee_profile_id": request.form.get("manager_employee_profile_id") or None,
        "commission_plan_id": request.form.get("commission_plan_id") or None,
    }
    mfa_required = "mfa_required" in request.form
    try:
        invitation, raw_token = create_employee_invitation(email, role_codes, employee_fields, actor.id, mfa_required=mfa_required)
    except EmployeeDraftValidationError as exc:
        return render_template(
            "employees/new.html", error=str(exc), roles=_role_options(), managers=_manager_options(), plans=_commission_plan_options()
        ), 400
    link = url_for("auth.accept_invitation_form", token=raw_token, _external=True)
    return render_template("employees/invitation_created.html", invitation=invitation, link=link)


@bp.route("/dashboard", methods=["GET"])
@require_permission("employees.view_all")
def dashboard():
    metrics = get_employee_dashboard_metrics()
    return render_template("employees/dashboard.html", metrics=metrics)


def _get_profile_or_404(employee_id):
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        if request.accept_mimetypes.best == "application/json":
            return None, (jsonify({"error": "not_found"}), 404)
        return None, (render_template("employees/not_found.html"), 404)
    return profile, None


@bp.route("/<uuid:employee_id>", methods=["GET"])
@require_permission("employees.view_all")
def detail(employee_id):
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    staff = db_session.get(StaffUser, profile.staff_user_id)
    presence = employee_presence_state(profile.id)
    sessions = list_sessions_for_staff(profile.staff_user_id)
    audit_events = db_session.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == "employee_profile", AuditLog.entity_public_id == str(profile.id))
        .order_by(AuditLog.created_at.desc())
        .limit(50)
    ).scalars().all()
    return render_template(
        "employees/detail.html", profile=profile, staff=staff, presence=presence, sessions=sessions,
        roles=_role_options(), managers=[m for m in _manager_options() if m.id != profile.id], plans=_commission_plan_options(),
        audit_events=audit_events,
    )


@bp.route("/<uuid:employee_id>/edit", methods=["POST"])
@require_permission("employees.update")
def edit(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    fields = {}
    for key in ("full_name", "job_title", "department"):
        value = request.form.get(key)
        if value is not None:
            fields[key] = value.strip() or None
    manager_id = request.form.get("manager_employee_profile_id")
    if manager_id is not None:
        fields["manager_employee_profile_id"] = manager_id or None
        if manager_id and _creates_manager_cycle(profile.id, uuid.UUID(manager_id)):
            return jsonify({"error": "manager_cycle"}), 400
    expected_version = request.form.get("version", type=int)
    if expected_version is not None and expected_version != profile.version:
        return jsonify({"error": "version_conflict"}), 409
    update_employee_profile(profile, fields, actor_staff_user_id=actor.id)
    return redirect(url_for("employees.detail", employee_id=employee_id))


def _creates_manager_cycle(profile_id: uuid.UUID, proposed_manager_id: uuid.UUID) -> bool:
    if proposed_manager_id == profile_id:
        return True
    seen = {profile_id}
    current = db_session.get(EmployeeProfile, proposed_manager_id)
    while current is not None:
        if current.id in seen:
            return True
        seen.add(current.id)
        if current.manager_employee_profile_id is None:
            return False
        current = db_session.get(EmployeeProfile, current.manager_employee_profile_id)
    return False


@bp.route("/<uuid:employee_id>/activate", methods=["POST"])
@require_permission("employees.update")
def activate(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    try:
        activate_employee(profile, actor_staff_user_id=actor.id)
    except InvalidEmploymentTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/suspend", methods=["POST"])
@require_permission("employees.suspend")
@require_recent_auth
def suspend(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    try:
        suspend_employee(profile, request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except (InvalidEmploymentTransitionError, LastSuperAdminError) as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/reactivate", methods=["POST"])
@require_permission("employees.suspend")
@require_recent_auth
def reactivate(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    try:
        reactivate_employee(profile, actor_staff_user_id=actor.id)
    except InvalidEmploymentTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/terminate", methods=["POST"])
@require_permission("employees.terminate")
@require_recent_auth
def terminate(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    try:
        terminate_employee(profile, request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except (InvalidEmploymentTransitionError, LastSuperAdminError) as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/archive", methods=["POST"])
@require_permission("employees.terminate")
@require_recent_auth
def archive(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    try:
        archive_employee(profile, actor_staff_user_id=actor.id)
    except InvalidEmploymentTransitionError as exc:
        return jsonify({"error": str(exc)}), 400
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/sessions/revoke", methods=["POST"])
@require_permission("security_sessions.revoke")
@require_recent_auth
def revoke_all_sessions(employee_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    revoke_all_sessions_for_staff(profile.staff_user_id, reason="management_revoked")
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code="EMPLOYEE_SESSIONS_REVOKED",
        entity_type="employee_profile", entity_public_id=str(profile.id),
    )
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/<uuid:employee_id>/sessions/<uuid:session_id>/revoke", methods=["POST"])
@require_permission("security_sessions.revoke")
@require_recent_auth
def revoke_one_session(employee_id, session_id):
    actor = load_current_staff()
    profile, err = _get_profile_or_404(employee_id)
    if err:
        return err
    session_row = db_session.get(StaffSession, session_id)
    if session_row is None or session_row.staff_user_id != profile.staff_user_id:
        return jsonify({"error": "not_found"}), 404
    revoke_session_by_id(session_id, reason="management_revoked")
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code="EMPLOYEE_SESSION_REVOKED",
        entity_type="employee_profile", entity_public_id=str(profile.id),
        after_state={"session_id": str(session_id)},
    )
    return redirect(url_for("employees.detail", employee_id=employee_id))


@bp.route("/invitations", methods=["GET"])
@require_permission("employees.create")
def open_invitations():
    invitations = db_session.execute(
        select(StaffInvitation)
        .where(StaffInvitation.accepted_at.is_(None), StaffInvitation.revoked_at.is_(None))
        .order_by(StaffInvitation.created_at.desc())
    ).scalars().all()
    return render_template("employees/invitations.html", invitations=invitations, now=utcnow())


@bp.route("/invitations/<uuid:invitation_id>/reissue", methods=["POST"])
@require_permission("employees.create")
@require_recent_auth
def reissue_invitation(invitation_id):
    """A lost/expired setup link has NO EmployeeProfile yet (nothing was
    ever accepted) -- reissue operates on the open invitation itself, not an
    employee_id. See app.staff.services.reissue_employee_invitation()."""
    actor = load_current_staff()
    invitation = db_session.get(StaffInvitation, invitation_id)
    if invitation is None:
        return jsonify({"error": "not_found"}), 404
    try:
        new_invitation, raw_token = reissue_employee_invitation(invitation, actor.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    link = url_for("auth.accept_invitation_form", token=raw_token, _external=True)
    return render_template("employees/invitation_created.html", invitation=new_invitation, link=link)
