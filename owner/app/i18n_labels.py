"""Phase 9.5B-R Milestone 11 -- centralized domain label localization.

Stored/internal values (EmployeeProfile.employment_status, Role.code,
presence_state() return values, AuditLog.action_code) NEVER change --
only their DISPLAY label is localized here, in exactly one place, never
scattered per-template (Non-Negotiable Principle 1/4). Every function
below is safe against an unknown/future code: it falls back to the raw
code itself rather than raising or rendering blank (governing spec's own
"safe fallback... must not crash rendering" requirement).

Every dict is built INSIDE its function, not at module level -- a
module-level dict would call gettext() once at import time, before any
request/locale context exists, permanently freezing every label in
whatever locale happened to be active (or none) at import time. Building
it per-call means each request gets its own correctly-localized labels.
"""
from __future__ import annotations

from flask_babel import gettext as _


def employment_status_label(code: str) -> str:
    labels = {
        "PENDING": _("Setup Pending"),
        "ACTIVE": _("Active"),
        "SUSPENDED": _("Suspended"),
        "TERMINATED": _("Terminated"),
        "ARCHIVED": _("Archived"),
    }
    return labels.get(code, code)


def presence_label(code: str) -> str:
    labels = {
        "ONLINE": _("Online Now"),
        "RECENTLY_ACTIVE": _("Recently Active"),
        "OFFLINE": _("Offline"),
    }
    return labels.get(code, code)


def role_label(code: str) -> str:
    labels = {
        "SUPER_ADMIN": _("Super Administrator"),
        "SALES": _("Sales"),
        "SUPPORT": _("Support"),
        "FINANCE": _("Finance"),
        "VIEWER": _("Viewer"),
    }
    return labels.get(code, code)


def account_status_label(*, is_active: bool, disabled: bool) -> str:
    if is_active and not disabled:
        return _("Active")
    return _("Disabled")


def session_status_label(*, revoked: bool) -> str:
    return _("Revoked") if revoked else _("Active")


def mfa_status_label(*, enrolled: bool, required: bool) -> str:
    if enrolled:
        return _("Enrolled")
    return _("Required") if required else _("Not enrolled")


def invitation_status_label(*, expired: bool) -> str:
    return _("Expired") if expired else _("Open")


def audit_action_label(code: str) -> str:
    labels = {
        "EMPLOYEE_PROFILE_CREATED": _("Employee profile created"),
        "EMPLOYEE_PROFILE_UPDATED": _("Employee profile updated"),
        "EMPLOYEE_ACTIVATED": _("Employee activated"),
        "EMPLOYEE_SUSPENDED": _("Employee suspended"),
        "EMPLOYEE_REACTIVATED": _("Employee reactivated"),
        "EMPLOYEE_TERMINATED": _("Employee terminated"),
        "EMPLOYEE_ARCHIVED": _("Employee archived"),
        "EMPLOYEE_SELF_PROFILE_UPDATED": _("Employee updated their own profile"),
        "EMPLOYEE_SESSIONS_REVOKED": _("All sessions revoked"),
        "EMPLOYEE_SESSION_REVOKED": _("Session revoked"),
        "STAFF_INVITATION_CREATED": _("Invitation created"),
        "STAFF_INVITATION_REVOKED": _("Invitation revoked"),
        "STAFF_INVITATION_ACCEPTED": _("Invitation accepted"),
        "STAFF_ROLES_CHANGED": _("Roles changed"),
        "STAFF_DISABLED": _("Account disabled"),
        "STAFF_MFA_RESET": _("MFA reset"),
        "STAFF_LOGIN_SUCCESS": _("Signed in"),
        "STAFF_LOGIN_MFA_SUCCESS": _("Signed in with MFA"),
        "STAFF_LOGOUT": _("Signed out"),
        "STAFF_PASSWORD_CHANGED": _("Password changed"),
        "MFA_ENROLLED": _("MFA enrolled"),
        "MFA_RECOVERY_CODE_USED": _("Recovery code used"),
    }
    return labels.get(code, code)


def permission_category_label(category: str) -> str:
    labels = {
        "STAFF": _("Staff"),
        "EMPLOYEES": _("Employees"),
    }
    return labels.get(category, category)
