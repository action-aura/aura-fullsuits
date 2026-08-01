"""Phase 9.5B Milestone 12 -- /api/operations/v1, the employee/presence
subset of the contract Phase 9.5A designed (internal-api-contract.md).
Cookie-session authenticated (StaffSession, same as every web route) --
deliberately NOT CSRF-exempt, unlike /api/licensing/v1 (device-signature
authenticated, a different security domain) -- a cookie-authenticated JSON
API is exactly the CSRF-vulnerable shape csrf.init_app(app) already protects
globally, so no exemption is added here."""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.auth.session import (
    current_session,
    list_sessions_for_staff,
    load_current_staff,
    revoke_all_sessions_for_staff,
    revoke_session_by_id,
)
from app.employees.presence import bulk_presence_states, employee_presence_state
from app.employees.queries import find_own_profile, list_employees
from app.employees.services import (
    InvalidEmploymentTransitionError,
    activate_employee,
    archive_employee,
    reactivate_employee,
    suspend_employee,
    terminate_employee,
    touch_presence,
    update_employee_profile,
    update_own_profile,
)
from app.extensions import db_session
from app.models.employees import EmployeeProfile
from app.models.staff import StaffSession, StaffUser
from app.security.rbac import get_staff_permission_codes, require_login, require_permission, require_recent_auth
from app.security.super_admin_guard import LastSuperAdminError
from app.staff.services import EmployeeDraftValidationError, SelfEscalationError, assign_roles, create_employee_invitation

bp = Blueprint("api_operations", __name__, url_prefix="/api/operations/v1")


def _iso(value):
    return value.isoformat() if value is not None else None


def _serialize_session(session_row: StaffSession, *, current_id=None) -> dict:
    return {
        "id": str(session_row.id),
        "created_at": _iso(session_row.created_at),
        "last_seen_at": _iso(session_row.last_seen_at),
        "expires_at": _iso(session_row.expires_at),
        "platform": session_row.platform,
        "revoked_at": _iso(session_row.revoked_at),
        "is_current": current_id is not None and session_row.id == current_id,
    }


def _serialize_employee(profile: EmployeeProfile, *, include_management_fields: bool) -> dict:
    staff = db_session.get(StaffUser, profile.staff_user_id)
    data = {
        "id": str(profile.id),
        "employee_number": profile.employee_number,
        "full_name": profile.full_name,
        "phone": profile.phone,
        "job_title": profile.job_title,
        "department": profile.department,
        "employment_start_date": profile.employment_start_date.isoformat() if profile.employment_start_date else None,
        "employment_status": profile.employment_status,
        "manager_employee_profile_id": str(profile.manager_employee_profile_id) if profile.manager_employee_profile_id else None,
        "presence": employee_presence_state(profile.id),
        "version": profile.version,
    }
    if include_management_fields:
        data.update(
            {
                "email": staff.email if staff else None,
                "is_active": staff.is_active if staff else None,
                "mfa_required": staff.mfa_required if staff else None,
                "commission_plan_id": str(profile.commission_plan_id) if profile.commission_plan_id else None,
                "notes": profile.notes,
                "created_at": _iso(profile.created_at),
                "archived_at": _iso(profile.archived_at),
            }
        )
    return data


# -- current user ------------------------------------------------------

@bp.route("/me", methods=["GET"])
@require_login
def me():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return jsonify(
        {
            "staff_id": str(staff.id),
            "email": staff.email,
            "display_name": staff.display_name,
            "is_super_admin": staff.is_super_admin,
            "mfa_required": staff.mfa_required,
            "permissions": sorted(get_staff_permission_codes(staff)),
            "employee_profile": _serialize_employee(profile, include_management_fields=False) if profile else None,
        }
    )


@bp.route("/me", methods=["PATCH"])
@require_login
def update_me():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    update_own_profile(profile, staff, display_name=body.get("display_name"), phone=body.get("phone"))
    return jsonify(_serialize_employee(profile, include_management_fields=False))


@bp.route("/me/sessions", methods=["GET"])
@require_login
def my_sessions():
    staff = load_current_staff()
    current = current_session()
    rows = list_sessions_for_staff(staff.id)
    return jsonify({"rows": [_serialize_session(r, current_id=current.id if current else None) for r in rows]})


@bp.route("/me/sessions/<uuid:session_id>/revoke", methods=["POST"])
@require_login
def revoke_my_session(session_id):
    staff = load_current_staff()
    current = current_session()
    if current is not None and current.id == session_id:
        return jsonify({"error": "VALIDATION_ERROR", "message": "Use /auth/logout to end your own current session."}), 400
    rows = list_sessions_for_staff(staff.id)
    if not any(r.id == session_id for r in rows):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    revoke_session_by_id(session_id, reason="self_revoked")
    return jsonify({"revoked": True})


# -- presence ------------------------------------------------------

@bp.route("/presence/heartbeat", methods=["POST"])
@require_login
def presence_heartbeat():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    if profile is None or profile.employment_status != "ACTIVE":
        return jsonify({"error": "EMPLOYEE_NOT_ACTIVE"}), 403
    session_row = current_session()
    body = request.get_json(silent=True) or {}
    app_instance_id = str(body.get("app_instance_id", ""))[:128]
    if not app_instance_id:
        return jsonify({"error": "VALIDATION_ERROR", "message": "app_instance_id is required"}), 400
    platform = body.get("platform", "WEB")
    if platform not in ("WEB", "ANDROID", "IOS"):
        return jsonify({"error": "VALIDATION_ERROR", "message": "invalid platform"}), 400
    touch_presence(
        profile.id, staff_session_id=session_row.id, app_instance_id=app_instance_id, platform=platform,
        app_version=body.get("app_version"),
    )
    return jsonify({"presence": employee_presence_state(profile.id)})


# -- management: employees ------------------------------------------------------

@bp.route("/employees", methods=["GET"])
@require_permission("employees.view_all")
def list_employees_route():
    page = request.args.get("page", 1, type=int)
    result = list_employees(
        page=page,
        search=request.args.get("q") or None,
        department=request.args.get("department") or None,
        employment_status=request.args.get("employment_status") or None,
        role_code=request.args.get("role_code") or None,
        presence=request.args.get("presence") or None,
        include_archived=request.args.get("include_archived") == "1",
    )
    return jsonify(
        {
            "rows": [_serialize_employee(row, include_management_fields=True) for row in result["rows"]],
            "page": result["page"],
            "page_size": result["page_size"],
            "total": result["total"],
            "total_pages": result["total_pages"],
        }
    )


@bp.route("/employees", methods=["POST"])
@require_permission("employees.create")
@require_recent_auth
def create_employee_route():
    actor = load_current_staff()
    body = request.get_json(silent=True) or {}
    email = body.get("email", "").strip()
    role_codes = body.get("role_codes", [])
    employee_fields = {
        k: body.get(k) for k in ("employee_number", "full_name", "phone", "job_title", "department", "employment_start_date", "manager_employee_profile_id", "commission_plan_id")
    }
    try:
        invitation, _raw_token = create_employee_invitation(
            email, role_codes, employee_fields, actor.id, mfa_required=body.get("mfa_required", True)
        )
    except EmployeeDraftValidationError as exc:
        return jsonify({"error": "VALIDATION_ERROR", "message": str(exc)}), 400
    except SelfEscalationError as exc:
        return jsonify({"error": "PERMISSION_DENIED", "message": str(exc)}), 403
    return jsonify({"invitation_id": str(invitation.id), "email": invitation.email}), 201


@bp.route("/employees/presence", methods=["GET"])
@require_permission("employees.view_presence")
def employees_presence():
    ids = db_session.execute(
        select(EmployeeProfile.id).where(EmployeeProfile.employment_status != "ARCHIVED")
    ).scalars().all()
    return jsonify({"presence": {str(k): v for k, v in bulk_presence_states(ids).items()}})


@bp.route("/employees/<uuid:employee_id>", methods=["GET"])
@require_permission("employees.view_all")
def get_employee_route(employee_id):
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_employee(profile, include_management_fields=True))


@bp.route("/employees/<uuid:employee_id>", methods=["PATCH"])
@require_permission("employees.update")
def update_employee_route(employee_id):
    actor = load_current_staff()
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    if "version" in body and body["version"] != profile.version:
        return jsonify({"error": "VERSION_CONFLICT"}), 409
    fields = {k: v for k, v in body.items() if k in ("full_name", "phone", "job_title", "department", "manager_employee_profile_id") }
    update_employee_profile(profile, fields, actor_staff_user_id=actor.id)
    return jsonify(_serialize_employee(profile, include_management_fields=True))


def _lifecycle_action(employee_id, action_fn, *, reason_required: bool):
    actor = load_current_staff()
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        if reason_required:
            action_fn(profile, body.get("reason", ""), actor_staff_user_id=actor.id)
        else:
            action_fn(profile, actor_staff_user_id=actor.id)
    except InvalidEmploymentTransitionError as exc:
        return jsonify({"error": "INVALID_STATE_TRANSITION", "message": str(exc)}), 409
    except LastSuperAdminError as exc:
        return jsonify({"error": "LAST_SUPER_ADMIN_REQUIRED", "message": str(exc)}), 400
    return jsonify(_serialize_employee(profile, include_management_fields=True))


@bp.route("/employees/<uuid:employee_id>/activate", methods=["POST"])
@require_permission("employees.update")
def activate_route(employee_id):
    return _lifecycle_action(employee_id, activate_employee, reason_required=False)


@bp.route("/employees/<uuid:employee_id>/suspend", methods=["POST"])
@require_permission("employees.suspend")
@require_recent_auth
def suspend_route(employee_id):
    return _lifecycle_action(employee_id, suspend_employee, reason_required=True)


@bp.route("/employees/<uuid:employee_id>/reactivate", methods=["POST"])
@require_permission("employees.suspend")
@require_recent_auth
def reactivate_route(employee_id):
    return _lifecycle_action(employee_id, reactivate_employee, reason_required=False)


@bp.route("/employees/<uuid:employee_id>/terminate", methods=["POST"])
@require_permission("employees.terminate")
@require_recent_auth
def terminate_route(employee_id):
    return _lifecycle_action(employee_id, terminate_employee, reason_required=True)


@bp.route("/employees/<uuid:employee_id>/archive", methods=["POST"])
@require_permission("employees.terminate")
@require_recent_auth
def archive_route(employee_id):
    return _lifecycle_action(employee_id, archive_employee, reason_required=False)


@bp.route("/employees/<uuid:employee_id>/sessions/revoke", methods=["POST"])
@require_permission("security_sessions.revoke")
@require_recent_auth
def revoke_employee_sessions_route(employee_id):
    actor = load_current_staff()
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    revoke_all_sessions_for_staff(profile.staff_user_id, reason="management_revoked")
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code="EMPLOYEE_SESSIONS_REVOKED",
        entity_type="employee_profile", entity_public_id=str(profile.id),
    )
    return jsonify({"revoked": True})


@bp.route("/employees/<uuid:employee_id>/roles", methods=["GET"])
@require_permission("employees.view_all")
def get_employee_roles_route(employee_id):
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    staff = db_session.get(StaffUser, profile.staff_user_id)
    return jsonify({"role_codes": sorted(a.role.code for a in staff.role_assignments)})


@bp.route("/employees/<uuid:employee_id>/roles", methods=["PUT"])
@require_permission("employees.assign_role")
@require_recent_auth
def put_employee_roles_route(employee_id):
    actor = load_current_staff()
    profile = db_session.get(EmployeeProfile, employee_id)
    if profile is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    staff = db_session.get(StaffUser, profile.staff_user_id)
    body = request.get_json(silent=True) or {}
    role_codes = body.get("role_codes", [])
    try:
        assign_roles(staff, role_codes, actor_staff_user_id=actor.id)
    except SelfEscalationError as exc:
        return jsonify({"error": "PERMISSION_DENIED", "message": str(exc)}), 403
    return jsonify({"role_codes": sorted(role_codes)})
