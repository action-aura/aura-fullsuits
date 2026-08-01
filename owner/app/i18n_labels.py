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


def subscription_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "PILOT": _("Pilot"),
        "ACTIVE": _("Active"),
        "PAST_DUE": _("Past Due"),
        "SUSPENDED": _("Suspended"),
        "EXPIRED": _("Expired"),
        "COMPLETED": _("Completed"),
        "CANCELLED": _("Cancelled"),
    }
    return labels.get(code, code)


def license_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "ISSUED": _("Issued"),
        "ACTIVE": _("Active"),
        "SUSPENDED": _("Suspended"),
        "REVOKED": _("Revoked"),
        "EXPIRED": _("Expired"),
        "REPLACED": _("Replaced"),
    }
    return labels.get(code, code)


def installation_status_label(code: str) -> str:
    labels = {
        "REGISTERED": _("Registered"),
        "PENDING_ACTIVATION": _("Pending Activation"),
        "ACTIVE": _("Active"),
        "SUSPENDED": _("Suspended"),
        "DEACTIVATED": _("Deactivated"),
        "REPLACED": _("Replaced"),
    }
    return labels.get(code, code)


def customer_status_label(code: str) -> str:
    """Real values confirmed against the customers/list.html status filter
    options (the authoritative, currently-implemented set)."""
    labels = {
        "LEAD": _("Lead"),
        "PROSPECT": _("Prospect"),
        "PILOT": _("Pilot"),
        "ACTIVE": _("Active"),
        "SUSPENDED": _("Suspended"),
        "CLOSED": _("Closed"),
        "ARCHIVED": _("Archived"),
    }
    return labels.get(code, code)


def renewal_status_label(code: str) -> str:
    """Real values confirmed against commercial_ops/renewals_list.html's
    status filter options (the authoritative, currently-implemented set)."""
    labels = {
        "DRAFT": _("Draft"),
        "QUOTED": _("Quoted"),
        "AWAITING_CONFIRMATION": _("Awaiting Confirmation"),
        "AWAITING_PAYMENT": _("Awaiting Payment"),
        "PAYMENT_RECORDED": _("Payment Recorded"),
        "APPROVED": _("Approved"),
        "APPLIED": _("Applied"),
        "REJECTED": _("Rejected"),
        "CANCELLED": _("Cancelled"),
        "VOIDED": _("Voided"),
    }
    return labels.get(code, code)


def pilot_status_label(code: str) -> str:
    """Real values confirmed against commercial_ops/pilots_list.html's
    status filter options."""
    labels = {
        "DRAFT": _("Draft"),
        "APPROVED": _("Approved"),
        "ACTIVE": _("Active"),
        "EXTENDED": _("Extended"),
        "CONVERTED": _("Converted"),
        "COMPLETED": _("Completed"),
        "CANCELLED": _("Cancelled"),
    }
    return labels.get(code, code)


def notification_severity_label(code: str) -> str:
    labels = {
        "INFO": _("Info"),
        "WARNING": _("Warning"),
        "CRITICAL": _("Critical"),
    }
    return labels.get(code, code)


def notification_status_label(code: str) -> str:
    """Real values confirmed against
    commercial_ops/notifications_list.html's status filter options."""
    labels = {
        "OPEN": _("Open"),
        "IN_PROGRESS": _("In Progress"),
        "ACKNOWLEDGED": _("Acknowledged"),
        "RESOLVED": _("Resolved"),
        "DISMISSED": _("Dismissed"),
    }
    return labels.get(code, code)


def pending_activation_status_label(code: str) -> str:
    labels = {
        "PENDING_REVIEW": _("Pending Review"),
        "APPROVED": _("Approved"),
        "REJECTED": _("Rejected"),
    }
    return labels.get(code, code)


def backup_status_label(code: str) -> str:
    labels = {
        "SUCCESS": _("Success"),
        "FAILED": _("Failed"),
        "IN_PROGRESS": _("In Progress"),
    }
    return labels.get(code, code)


def signing_key_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "ACTIVE": _("Active"),
        "RETIRED": _("Retired"),
        "REVOKED": _("Revoked"),
    }
    return labels.get(code, code)


def device_key_status_label(code: str) -> str:
    labels = {
        "ACTIVE": _("Active"),
        "REVOKED": _("Revoked"),
    }
    return labels.get(code, code)


def timeline_category_label(code: str) -> str:
    labels = {
        "SUBSCRIPTION": _("Subscription"),
        "LICENSE": _("License"),
        "INSTALLATION": _("Installation"),
        "RENEWAL": _("Renewal"),
        "PILOT": _("Pilot"),
        "NOTIFICATION": _("Notification"),
    }
    return labels.get(code, code)


def activation_mode_label(code: str) -> str:
    labels = {
        "AUTOMATIC": _("Automatic"),
        "MANUAL_APPROVAL": _("Manual Approval"),
        "RISK_REVIEW": _("Risk Review"),
    }
    return labels.get(code, code)


def emergency_extension_status_label(code: str) -> str:
    labels = {
        "ACTIVE": _("Active"),
        "REVOKED": _("Revoked"),
    }
    return labels.get(code, code)


def pilot_conversion_decision_label(code: str) -> str:
    labels = {
        "PENDING": _("Pending"),
        "CONVERT": _("Convert"),
        "DO_NOT_CONVERT": _("Do Not Convert"),
    }
    return labels.get(code, code)


def renewal_date_rule_label(code: str) -> str:
    labels = {
        "EARLY_RENEWAL_FROM_CURRENT_END": _("Early renewal (from current end date)"),
        "LATE_RENEWAL_FROM_APPROVAL_DATE": _("Late renewal (from approval date)"),
    }
    return labels.get(code, code)


def activation_event_type_label(code: str) -> str:
    labels = {
        "ACTIVATION": _("Activation"),
        "CHECK_IN": _("Check-in"),
        "DEACTIVATION": _("Deactivation"),
    }
    return labels.get(code, code)


def health_status_label(code: str) -> str:
    labels = {
        "OK": _("OK"),
        "DEGRADED": _("Degraded"),
        "DOWN": _("Down"),
        "UNKNOWN": _("Unknown"),
    }
    return labels.get(code, code)


def billing_model_label(code: str) -> str:
    labels = {
        "ONE_TIME": _("One-time"),
        "MONTHLY": _("Monthly"),
        "ANNUAL": _("Annual"),
        "PILOT": _("Pilot"),
        "CUSTOM": _("Custom"),
    }
    return labels.get(code, code)


def product_commercial_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "PLANNED": _("Planned"),
        "PILOT": _("Pilot"),
        "AVAILABLE": _("Available"),
        "RETIRED": _("Retired"),
    }
    return labels.get(code, code)


def plan_lifecycle_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "PLANNED": _("Planned"),
        "PILOT": _("Pilot"),
        "AVAILABLE": _("Available"),
        "RETIRED": _("Retired"),
    }
    return labels.get(code, code)


def addon_availability_status_label(code: str) -> str:
    labels = {
        "DRAFT": _("Draft"),
        "PLANNED": _("Planned"),
        "PILOT": _("Pilot"),
        "AVAILABLE": _("Available"),
        "RETIRED": _("Retired"),
    }
    return labels.get(code, code)


def payment_status_label(code: str) -> str:
    labels = {
        "PENDING": _("Pending"),
        "CONFIRMED": _("Confirmed"),
        "FAILED": _("Failed"),
        "REFUNDED": _("Refunded"),
        "VOIDED": _("Voided"),
    }
    return labels.get(code, code)


def audit_result_label(code: str) -> str:
    labels = {
        "SUCCESS": _("Success"),
        "FAILURE": _("Failure"),
    }
    return labels.get(code, code)


def activation_event_result_label(code: str) -> str:
    labels = {
        "ACCEPTED": _("Accepted"),
        "REJECTED": _("Rejected"),
        "PENDING": _("Pending Review"),
        "SUCCESS": _("Success"),
        "FAILURE": _("Failure"),
    }
    return labels.get(code, code)


def generic_audit_action_label(code: str) -> str:
    """Fallback label for owner-wide audit action codes shown on the
    system-wide audit/dashboard views (broader set than the employee-scoped
    codes already covered by audit_action_label). Safe .get() fallback means
    any action code not yet explicitly enumerated here still renders --
    unlocalized but never blank or crashing -- exactly like every other
    label function in this module."""
    labels = {
        "STAFF_LOGIN_SUCCESS": _("Signed in"),
        "STAFF_LOGIN_MFA_SUCCESS": _("Signed in with MFA"),
        "STAFF_LOGOUT": _("Signed out"),
        "STAFF_PASSWORD_CHANGED": _("Password changed"),
        "STAFF_DISABLED": _("Account disabled"),
        "STAFF_MFA_RESET": _("MFA reset"),
        "STAFF_ROLES_CHANGED": _("Roles changed"),
        "STAFF_INVITATION_CREATED": _("Invitation created"),
        "STAFF_INVITATION_ACCEPTED": _("Invitation accepted"),
        "STAFF_INVITATION_REVOKED": _("Invitation revoked"),
        "CUSTOMER_CREATED": _("Customer created"),
        "CUSTOMER_UPDATED": _("Customer updated"),
        "CUSTOMER_ARCHIVED": _("Customer archived"),
        "SUBSCRIPTION_CREATED": _("Subscription created"),
        "SUBSCRIPTION_TRANSITIONED": _("Subscription status changed"),
        "SUBSCRIPTION_RENEWED": _("Subscription renewed"),
        "PAYMENT_RECORDED": _("Payment recorded"),
        "PAYMENT_CORRECTED": _("Payment corrected"),
        "LICENSE_ISSUED": _("License issued"),
        "LICENSE_TRANSITIONED": _("License status changed"),
        "LICENSE_REPLACED": _("License replaced"),
        "LICENSE_CREATED": _("License created"),
        "LICENSE_KEY_ISSUED": _("License key issued"),
        "INSTALLATION_REGISTERED": _("Installation registered"),
        "INSTALLATION_TRANSITIONED": _("Installation status changed"),
        "BACKUP_CREATED": _("Backup created"),
        "BACKUP_RESTORED": _("Backup restored"),
        "EMPLOYEE_PROFILE_CREATED": _("Employee profile created"),
        "EMPLOYEE_ACTIVATED": _("Employee activated"),
        "CHECK_IN_ACCEPTED": _("Check-in accepted"),
        "ACTIVATION_ACCEPTED": _("Activation accepted"),
        "DEVICE_SLOT_EXCEPTION_CREATED": _("Device-slot exception created"),
    }
    return labels.get(code, code)
