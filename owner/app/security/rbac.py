"""Server-side RBAC enforcement (Part G). Every sensitive route is decorated
here -- hiding a button in a template is never the only control (Principle 2)."""
from __future__ import annotations

from functools import wraps

from flask import abort, jsonify, redirect, request, url_for
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.extensions import db_session
from app.models.staff import Permission, Role, RolePermission, StaffRoleAssignment


def get_staff_permission_codes(staff_user) -> set[str]:
    if staff_user is None:
        return set()
    if staff_user.is_super_admin:
        return {p.code for p in db_session.execute(select(Permission)).scalars().all()}
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(StaffRoleAssignment, StaffRoleAssignment.role_id == Role.id)
        .where(StaffRoleAssignment.staff_user_id == staff_user.id)
    )
    return set(db_session.execute(stmt).scalars().all())


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        staff = load_current_staff()
        if staff is None:
            if _wants_json():
                return jsonify({"error": "authentication_required"}), 401
            # Presentation-only hint for login.html to show a calm "your
            # session ended, sign in again" message instead of a bare
            # login form -- never trusted for anything security-relevant,
            # the actual authorization decision above is already made.
            return redirect(url_for("auth.login_form", next=request.path, reason="session_expired"))
        return view(*args, **kwargs)

    return wrapped


def require_permission(permission_code: str):
    def decorator(view):
        @wraps(view)
        @require_login
        def wrapped(*args, **kwargs):
            staff = load_current_staff()
            if permission_code not in get_staff_permission_codes(staff):
                if _wants_json():
                    return jsonify({"error": "forbidden"}), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_any_permission(*permission_codes: str):
    """Phase 9.5C -- for the real _own/_all permission-pair shape (e.g.
    leads.view_own vs leads.view_all): a role may hold only one of the
    two (VIEWER has leads.view_all but not leads.view_own), so gating a
    route on a single fixed code locks that role out entirely even
    though the ownership-scoping logic underneath already handles both
    cases correctly. Passes if the actor holds ANY of the listed codes."""
    def decorator(view):
        @wraps(view)
        @require_login
        def wrapped(*args, **kwargs):
            staff = load_current_staff()
            codes = get_staff_permission_codes(staff)
            if not any(code in codes for code in permission_codes):
                if _wants_json():
                    return jsonify({"error": "forbidden"}), 403
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_recent_auth(view):
    """Require MFA confirmation within the recent-auth window for highly
    sensitive actions (Part F): role changes, disabling staff, license
    issuance/reveal, entitlement changes, audit export, database restore."""

    @wraps(view)
    @require_login
    def wrapped(*args, **kwargs):
        if not has_recent_auth():
            if _wants_json():
                return jsonify({"error": "recent_auth_required"}), 401
            return redirect(url_for("auth.reauth_form", next=request.path))
        return view(*args, **kwargs)

    return wrapped
