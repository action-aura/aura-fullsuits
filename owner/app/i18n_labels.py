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


def staff_account_filter_status_label(code: str) -> str:
    """UI modernization Stage D.6 -- label for the /staff list screen's
    status FILTER dropdown (?status=ACTIVE|DISABLED), distinct from
    account_status_label()'s (is_active, disabled) keyword-only signature
    (which labels one already-known account's own badge, not a filter
    option code) -- components/table.html's filter_bar() macro calls its
    status_label_fn as callable(code), the same single-argument contract
    every other domain's *_status_label already satisfies."""
    labels = {"ACTIVE": _("Active"), "DISABLED": _("Disabled")}
    return labels.get(code, code)


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


def lead_status_label(code: str) -> str:
    """Phase 9.5C -- real values from app.models.leads.LEAD_STATUSES."""
    labels = {
        "NEW": _("New"),
        "NOT_INTERESTED_NOW": _("Not interested now"),
        "POTENTIAL": _("Potential"),
        "FOLLOW_UP": _("Follow-up"),
        "UNDER_OBSERVATION": _("Under observation"),
        "QUALIFIED": _("Qualified"),
        "CONFIRMED": _("Confirmed"),
        "LOST": _("Lost"),
        "ARCHIVED": _("Archived"),
    }
    return labels.get(code, code)


def lead_source_label(code: str) -> str:
    """Phase 9.5C -- real values from app.models.leads.LEAD_SOURCES."""
    labels = {
        "WEBSITE": _("Website"),
        "REFERRAL": _("Referral"),
        "COLD_OUTREACH": _("Cold outreach"),
        "EVENT": _("Event"),
        "OTHER": _("Other"),
    }
    return labels.get(code, code)


def lead_priority_label(code: str) -> str:
    """Phase 9.5C -- real values from app.models.leads.LEAD_PRIORITIES."""
    labels = {
        "LOW": _("Low"),
        "MEDIUM": _("Medium"),
        "HIGH": _("High"),
    }
    return labels.get(code, code)


def interaction_type_label(code: str) -> str:
    """Phase 9.5C -- real values from app.models.leads.INTERACTION_TYPES."""
    labels = {
        "CALL": _("Call"),
        "EMAIL": _("Email"),
        "MEETING": _("Meeting"),
        "WHATSAPP_MANUAL_NOTE": _("WhatsApp (manual note)"),
        "OTHER": _("Other"),
    }
    return labels.get(code, code)


def followup_status_label(code: str) -> str:
    """Phase 9.5C -- derived status values (app.leads.engagement.followup_status)."""
    labels = {
        "OPEN": _("Open"),
        "COMPLETED": _("Completed"),
        "CANCELLED": _("Cancelled"),
    }
    return labels.get(code, code)


def note_visibility_label(code: str) -> str:
    """Phase 9.5C -- real values from app.leads.errors.NOTE_VISIBILITIES."""
    labels = {
        "AUTHOR_ONLY": _("Only me"),
        "ASSIGNED_RECORD_USERS": _("Anyone with record access"),
        "MANAGEMENT_ONLY": _("Management only"),
    }
    return labels.get(code, code)


def location_source_label(code: str) -> str:
    """Phase 9.5C -- real values from app.models.leads.LOCATION_SOURCES."""
    labels = {
        "GPS": _("Device location (GPS)"),
        "NETWORK": _("Approximate (network)"),
        "MANUAL": _("Manually entered"),
        "IMPORTED": _("Imported"),
    }
    return labels.get(code, code)


def location_verification_label(verified: bool) -> str:
    """Phase 9.5C -- verified is a boolean column, not an enum, but its
    display value is exactly as translatable as any other status label."""
    return _("Verified") if verified else _("Unverified")


def duplicate_review_marker_label(code: str) -> str:
    """Phase 9.5C -- the one bounded duplicate-detection privacy marker
    (app.customers.services.DUPLICATE_REVIEW_MARKER)."""
    labels = {
        "POSSIBLE_EXISTING_RECORD_REQUIRES_MANAGEMENT_REVIEW": _("Possible existing record -- requires management review"),
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


def sync_quarantine_status_label(code: str) -> str:
    labels = {
        "PENDING": _("Pending"),
        "REPLAYED": _("Replayed"),
        "DISCARDED": _("Discarded"),
    }
    return labels.get(code, code)


def localize_pilot_lifecycle_error(code: str, **params) -> str:
    """Presentation-boundary translation for app.commercial_ops.pilot_lifecycle
    .PilotLifecycleError -- called ONLY from a real Flask request handler
    (commercial_ops/ui_routes.py), never from the service layer itself
    (Phase 9.5B-R2/R3: gettext() inside the service layer broke every
    non-HTTP caller). Safe fallback: an unrecognized code renders as
    itself, never raises."""
    messages = {
        "SUBSCRIPTION_NOT_PILOT_STATUS": _("Subscription must already be in PILOT status to create a pilot record (was %(current_status)s)."),
        "PILOT_END_BEFORE_START": _("Pilot end date must be after the pilot start date."),
        "REASON_REQUIRED_TO_EXTEND": _("A reason is required to extend a pilot."),
        "MAX_EXTENSIONS_REACHED": _("Pilot has already been extended %(count)s time(s) (maximum allowed: %(max)s). No indefinite rolling pilot."),
        "NEW_END_DATE_NOT_AFTER_CURRENT": _("The new end date must be after the current pilot end date."),
        "RENEWAL_NOT_APPLIED": _("The renewal request must be applied before this pilot can be marked converted."),
        "RENEWAL_SUBSCRIPTION_MISMATCH": _("This renewal request does not belong to this pilot's subscription."),
        "REASON_REQUIRED_TO_CANCEL": _("A reason is required to cancel a pilot."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_pilot_transition_error(code: str, **params) -> str:
    messages = {
        "INVALID_PILOT_TRANSITION": _("Cannot change the pilot status from %(from_status)s to %(to_status)s."),
        "INVALID_PILOT_EXTEND_STATUS": _("Cannot extend a pilot in status %(status)s."),
        "INVALID_PILOT_CONVERT_STATUS": _("Cannot convert a pilot in status %(status)s."),
    }
    template = messages.get(code)
    if template is None:
        return code
    labeled_params = {k: (pilot_status_label(v) if k in ("status", "from_status", "to_status") else v) for k, v in params.items()}
    return template % labeled_params if labeled_params else template


def localize_renewal_transition_error(code: str, **params) -> str:
    messages = {
        "INVALID_RENEWAL_TRANSITION": _("Cannot change the renewal request status from %(from_status)s to %(to_status)s."),
        "USE_APPROVE_FUNCTION": _("Use the approve action to move this renewal request to Approved."),
        "INVALID_RENEWAL_APPROVE_STATUS": _("Cannot approve a renewal request in status %(status)s."),
        "INVALID_RENEWAL_APPLY_STATUS": _("Cannot apply a renewal request in status %(status)s; it must be Approved first."),
    }
    template = messages.get(code)
    if template is None:
        return code
    labeled_params = {k: (renewal_status_label(v) if k in ("status", "from_status", "to_status") else v) for k, v in params.items()}
    return template % labeled_params if labeled_params else template


def localize_device_slot_error(code: str, **params) -> str:
    messages = {
        "REASON_REQUIRED_TO_RELEASE": _("A reason is required to release a device slot."),
        "REASON_REQUIRED_TO_REPLACE": _("A reason is required to replace a device slot."),
        "EXTRA_SLOTS_MUST_BE_POSITIVE": _("Extra slots must be a positive number."),
        "REASON_REQUIRED_TO_CREATE_EXCEPTION": _("A reason is required to create a device slot exception."),
        "EXPIRES_AT_BEFORE_STARTS_AT": _("The expiry date must be after the start date."),
        "EXCEPTION_EXCEEDS_MAX_DAYS": _("Device slot exceptions may not exceed %(max_days)s days -- temporary means temporary."),
        "INVALID_REVOKE_STATUS": _("Cannot revoke a device slot exception in status %(status)s."),
        "REASON_REQUIRED_TO_REVOKE": _("A reason is required to revoke a device slot exception."),
        "ADDITIONAL_DEVICES_MUST_BE_POSITIVE": _("Enter a positive number of devices to add."),
        "REASON_REQUIRED_TO_ADD_DEVICES": _("A reason is required to add devices to a license."),
        "DEVICE_LIMIT_BELOW_ACTIVE_COUNT": _(
            "Cannot set this license's device limit to %(new_limit)s -- %(active_count)s device(s) are "
            "already active on it. Add enough devices to cover current usage."
        ),
        "LICENSE_NOT_FOUND": _("License not found."),
        "SUBSCRIPTION_NOT_FOUND": _("Subscription not found for this license."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_emergency_extension_error(code: str, **params) -> str:
    messages = {
        "REASON_REQUIRED_TO_CREATE": _("A reason is required to create an emergency extension."),
        "INVALID_DURATION_HOURS": _("Duration must be between 1 and %(max_hours)s hours (explicit, short-lived only)."),
        "LICENSE_REVOKED": _("Cannot create an emergency extension for a revoked license."),
        "ALREADY_HAS_ACTIVE_EXTENSION": _("This subscription already has an active emergency extension until %(expires_at)s."),
        "INVALID_REVOKE_STATUS": _("Cannot revoke an emergency extension in status %(status)s."),
        "REASON_REQUIRED_TO_REVOKE": _("A reason is required to revoke an emergency extension."),
    }
    template = messages.get(code)
    if template is None:
        return code
    safe_params = {k: v for k, v in params.items() if k != "extension_id"}
    return template % safe_params if safe_params else template


def localize_pending_activation_error(code: str, **params) -> str:
    messages = {
        "INVALID_APPROVE_STATUS": _("Cannot approve a pending activation in status %(status)s."),
        "INSTALLATION_NOT_AWAITING_ACTIVATION": _("This installation is no longer awaiting activation."),
        "LICENSE_NOT_FOUND": _("The license for this pending activation could not be found."),
        "DEVICE_LIMIT_REACHED": _("This license has already reached its device limit."),
        "INVALID_REJECT_STATUS": _("Cannot reject a pending activation in status %(status)s."),
        "REASON_REQUIRED_TO_REJECT": _("A reason is required to reject a pending activation."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_activation_policy_error(code: str, **params) -> str:
    messages = {
        "UNKNOWN_ACTIVATION_MODE": _("Unknown activation mode: %(mode)s"),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_notification_error(code: str, **params) -> str:
    messages = {
        "INVALID_ACKNOWLEDGE_STATUS": _("Cannot acknowledge a notification in status %(status)s."),
        "ALREADY_RESOLVED_OR_DISMISSED": _("This notification is already %(status)s."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


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
        "LICENSE_STATUS_CHANGED": _("License status changed"),
        "LICENSE_REPLACED": _("License replaced"),
        "LICENSE_CREATED": _("License created"),
        "LICENSE_KEY_ISSUED": _("License key issued"),
        "INSTALLATION_REGISTERED": _("Installation registered"),
        "INSTALLATION_STATUS_CHANGED": _("Installation status changed"),
        "BACKUP_CREATED": _("Backup created"),
        "BACKUP_RESTORED": _("Backup restored"),
        "EMPLOYEE_PROFILE_CREATED": _("Employee profile created"),
        "EMPLOYEE_ACTIVATED": _("Employee activated"),
        "CHECK_IN_ACCEPTED": _("Check-in accepted"),
        "ACTIVATION_ACCEPTED": _("Activation accepted"),
        "DEVICE_SLOT_EXCEPTION_CREATED": _("Device-slot exception created"),
        # Phase 9.5E -- Expenses
        "EXPENSE_CREATED": _("Expense created"),
        "EXPENSE_SUBMITTED": _("Expense submitted for approval"),
        "EXPENSE_REVISED": _("Expense revised"),
        "EXPENSE_VOIDED": _("Expense voided"),
        "EXPENSE_APPROVAL_APPROVED": _("Expense approved"),
        "EXPENSE_APPROVAL_REJECTED": _("Expense rejected"),
        "EXPENSE_APPROVAL_RETURNED": _("Expense returned for correction"),
        "EXPENSE_APPROVAL_CANCELLED": _("Expense approval request cancelled"),
        "EXPENSE_PAYMENT_RECORDED": _("Expense payment recorded"),
        "EXPENSE_PAYMENT_REVERSED": _("Expense payment reversed"),
        "EXPENSE_ATTACHMENT_UPLOADED": _("Expense attachment uploaded"),
        "EXPENSE_ATTACHMENT_DOWNLOADED": _("Expense attachment downloaded"),
        "EXPENSE_ATTACHMENT_ARCHIVED": _("Expense attachment archived"),
        "EXPENSE_PAYEE_CREATED": _("Expense payee created"),
        "EXPENSE_PAYEE_DEACTIVATED": _("Expense payee deactivated"),
        "EXPENSE_DUPLICATE_WARNING_OVERRIDDEN": _("Duplicate-expense warning overridden"),
        # Phase 9.5E -- Daily Cash Closing
        "CASH_CLOSING_CREATED": _("Cash closing created"),
        "CASH_CLOSING_SUBMITTED": _("Cash closing submitted"),
        "CASH_CLOSING_APPROVED": _("Cash closing approved"),
        "CASH_CLOSING_REJECTED": _("Cash closing rejected"),
        "CASH_CLOSING_CLOSED": _("Cash closing closed"),
        "CASH_CLOSING_REOPENED": _("Cash closing reopened"),
        "CASH_CLOSING_ADJUSTMENT_CREATED": _("Cash closing adjustment created"),
        "CASH_CLOSING_ADJUSTMENT_APPROVED": _("Cash closing adjustment approved"),
        # Phase 9.5E -- Operational Reports
        "REPORT_SNAPSHOT_GENERATED": _("Report snapshot generated"),
        "REPORT_SNAPSHOT_REGENERATED": _("Report snapshot regenerated"),
        # Phase 9.5E -- Management Notes
        "MANAGEMENT_NOTE_CREATED": _("Management note created"),
        "MANAGEMENT_NOTE_UPDATED": _("Management note updated"),
        "MANAGEMENT_NOTE_ASSIGNED": _("Management note assigned"),
        "MANAGEMENT_NOTE_STATUS_CHANGED": _("Management note status changed"),
        "MANAGEMENT_NOTE_COMMENT_ADDED": _("Comment added to management note"),
    }
    return labels.get(code, code)


def localize_lead_error(code: str, **params) -> str:
    """Phase 9.5C -- presentation-boundary localization for
    app.leads.errors.LeadError, called only from route handlers (never
    the service layer -- Non-Negotiable Rule 10)."""
    messages = {
        "INVALID_LEAD_TRANSITION": _("Cannot change lead status from %(from_status)s to %(to_status)s."),
        "REASON_REQUIRED_FOR_LOST": _("A reason is required to mark a lead as lost."),
        "STALE_LEAD_VERSION": _("This lead was changed by someone else. Reload and try again."),
        "LEAD_NOT_FOUND": _("Lead not found."),
        "LEAD_ACCESS_DENIED": _("You do not have access to this lead."),
        "DESTINATION_EMPLOYEE_NOT_ACTIVE": _("Cannot assign a lead to an inactive employee."),
        "REASON_REQUIRED_FOR_REASSIGN": _("A reason is required to reassign a lead."),
        "LEAD_NAME_REQUIRED": _("A prospect or organization name is required."),
        "LEAD_NAME_TOO_LONG": _("Name must be at most %(max_len)s characters."),
        "LEAD_PHONE_TOO_LONG": _("Phone must be at most %(max_len)s characters."),
        "LEAD_EMAIL_INVALID": _("Enter a valid email address."),
        "LEAD_CONTACT_METHOD_REQUIRED": _("At least one contact method (phone or email) is required."),
        "LEAD_SOURCE_INVALID": _("Unknown lead source: %(source)s."),
        "LEAD_PRIORITY_INVALID": _("Unknown lead priority: %(priority)s."),
        "LEAD_ESTIMATED_VALUE_INVALID": _("Estimated value must be a non-negative number."),
        "LEAD_CURRENCY_REQUIRED_WITH_VALUE": _("A 3-letter currency code is required when an estimated value is set."),
        "LEAD_LOCATION_SUMMARY_TOO_LONG": _("Location summary must be at most %(max_len)s characters."),
        "IDEMPOTENCY_CONFLICT": _("This request conflicts with an earlier request using the same idempotency key."),
        "INTERACTION_TYPE_INVALID": _("Unknown interaction type: %(interaction_type)s."),
        "INTERACTION_SUMMARY_TOO_LONG": _("Summary must be at most %(max_len)s characters."),
        "INTERACTION_OCCURRED_AT_TOO_FUTURE": _("Interaction time cannot be in the future."),
        "FOLLOWUP_DUE_AT_REQUIRED": _("A due date/time is required for a follow-up."),
        "FOLLOWUP_ALREADY_CANCELLED": _("This follow-up was already cancelled."),
        "FOLLOWUP_ALREADY_COMPLETED": _("This follow-up was already completed."),
        "REASON_REQUIRED_FOR_FOLLOWUP_CANCEL": _("A reason is required to cancel a follow-up."),
        "CONTACT_NAME_REQUIRED": _("Contact name is required."),
        "CONTACT_NAME_TOO_LONG": _("Contact name must be at most %(max_len)s characters."),
        "NOTE_VISIBILITY_INVALID": _("Unknown note visibility: %(visibility)s."),
        "NOTE_BODY_TOO_LONG": _("Note must be at most %(max_len)s characters."),
        "NOTE_BODY_REQUIRED": _("Note text is required."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_customer_crm_error(code: str, **params) -> str:
    """Phase 9.5C -- presentation-boundary localization for
    app.leads.errors.CustomerCrmError."""
    messages = {
        "STALE_CUSTOMER_VERSION": _("This customer was changed by someone else. Reload and try again."),
        "CUSTOMER_ACCESS_DENIED": _("You do not have access to this customer."),
        "DESTINATION_EMPLOYEE_NOT_ACTIVE": _("Cannot assign a customer to an inactive employee."),
        "REASON_REQUIRED_FOR_REASSIGN": _("A reason is required to reassign a customer."),
        "NOTE_VISIBILITY_INVALID": _("Unknown note visibility: %(visibility)s."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_location_error(code: str, **params) -> str:
    """Phase 9.5C -- presentation-boundary localization for
    app.leads.errors.LocationValidationError. Templates never reference
    the actual coordinate value -- only field names/bounds (Non-Negotiable
    Domain Rule 14: exact coordinates never reach user-facing text)."""
    messages = {
        "INVALID_LATITUDE": _("Latitude must be a finite number between -90 and 90."),
        "INVALID_LONGITUDE": _("Longitude must be a finite number between -180 and 180."),
        "INVALID_ACCURACY": _("Accuracy must be a finite number greater than or equal to zero."),
        "INVALID_SOURCE": _("Unknown location source: %(source)s."),
        "TIMESTAMP_TOO_FAR": _("Client-reported capture time is too far from server time."),
        "MANUAL_ADDRESS_TOO_LONG": _("Manual address is too long."),
        "REASON_REQUIRED_FOR_VERIFY": _("A reason is required to verify or revoke a location."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def quote_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commercial_sales.QUOTE_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "SENT": _("Sent"),
        "ACCEPTED": _("Accepted"),
        "REJECTED": _("Rejected"),
        "EXPIRED": _("Expired"),
        "CANCELLED": _("Cancelled"),
    }
    return labels.get(code, code)


def sales_order_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commercial_sales.SALES_ORDER_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "CONFIRMED": _("Confirmed"),
        "CANCELLED": _("Cancelled"),
        "FULFILLED": _("Fulfilled"),
    }
    return labels.get(code, code)


def commercial_invoice_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commercial_sales.INVOICE_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "ISSUED": _("Issued"),
        "PARTIALLY_PAID": _("Partially paid"),
        "PAID": _("Paid"),
        "VOID": _("Void"),
        "REFUNDED": _("Refunded"),
        "PARTIALLY_REFUNDED": _("Partially refunded"),
    }
    return labels.get(code, code)


def commercial_refund_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commercial_sales.REFUND_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "APPROVED": _("Approved"),
        "PAID": _("Paid"),
        "VOID": _("Void"),
    }
    return labels.get(code, code)


def commercial_approval_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commercial_sales.APPROVAL_STATUSES."""
    labels = {
        "PENDING": _("Pending"),
        "APPROVED": _("Approved"),
        "REJECTED": _("Rejected"),
        "CANCELLED": _("Cancelled"),
        "EXPIRED": _("Expired"),
    }
    return labels.get(code, code)


def commission_entry_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commissions.COMMISSION_ENTRY_STATUSES."""
    labels = {
        "PENDING": _("Pending"),
        "EARNED": _("Earned"),
        "APPROVED": _("Approved"),
        "PAID": _("Paid"),
        "REVERSED": _("Reversed"),
        "CANCELLED": _("Cancelled"),
        "DISPUTED": _("Disputed"),
    }
    return labels.get(code, code)


def commission_payout_batch_status_label(code: str) -> str:
    """Phase 9.5D -- real values from app.models.commissions.PAYOUT_BATCH_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "APPROVED": _("Approved"),
        "PAID": _("Paid"),
    }
    return labels.get(code, code)


def localize_commercial_sales_error(code: str, **params) -> str:
    """Phase 9.5D -- presentation-boundary localization for
    app.commercial_sales.errors.CommercialSalesError, matching
    localize_lead_error()'s established pattern: the service layer's own
    str(exc) is deliberately English-only and request-context-free
    (commercial_ops/errors.py StableCodeError's own docstring); a
    user-facing Jinja route localizes via exc.code/exc.params here
    instead."""
    messages = {
        "INVALID_QUANTITY": _("Quantity must be a positive whole number."),
        "NON_FINITE_AMOUNT": _("Amount must be a finite number."),
        "NEGATIVE_DOCUMENT_TOTAL": _("Document total cannot be negative."),
        "DISCOUNT_EXCEEDS_GROSS": _("Discount cannot exceed the applicable gross amount."),
        "REFUND_EXCEEDS_REFUNDABLE": _("Refund amount (%(amount)s) exceeds the refundable balance (%(refundable)s)."),
        "ALLOCATION_EXCEEDS_PAYMENT": _("Allocation amount (%(amount)s) exceeds the unallocated payment balance (%(available)s)."),
        "ALLOCATION_EXCEEDS_OUTSTANDING": _("Allocation amount (%(amount)s) exceeds the invoice outstanding balance (%(outstanding)s)."),
        "CURRENCY_MISMATCH": _("Currency %(given)s does not match the required currency %(expected)s."),
        "PAYMENT_CUSTOMER_MISMATCH": _("This payment belongs to a different customer than the invoice."),
        "INVALID_CURRENCY_CODE": _("Currency must be a 3-letter ISO 4217 code."),
        "EMPTY_DOCUMENT": _("A document must have at least one line."),
        "INVALID_QUOTE_TRANSITION": _("Cannot change quote status from %(from_status)s to %(to_status)s."),
        "INVALID_ORDER_TRANSITION": _("Cannot change order status from %(from_status)s to %(to_status)s."),
        "INVALID_INVOICE_TRANSITION": _("Cannot change invoice status from %(from_status)s to %(to_status)s."),
        "INVALID_REFUND_TRANSITION": _("Cannot change refund status from %(from_status)s to %(to_status)s."),
        "INVALID_APPROVAL_TRANSITION": _("Cannot change approval status from %(from_status)s to %(to_status)s."),
        "STALE_VERSION": _("This record was changed by someone else. Reload and try again."),
        "RECORD_NOT_FOUND": _("Record not found."),
        "RECORD_ACCESS_DENIED": _("You do not have access to this record."),
        "IDEMPOTENCY_CONFLICT": _("This request conflicts with an earlier request using the same idempotency key."),
        "REASON_REQUIRED": _("A reason is required for this action."),
        "INVALID_PAYMENT_METHOD": _("Payment method must be one of the allowed values."),
        "SELF_APPROVAL_FORBIDDEN": _("You cannot approve your own request."),
        "APPROVAL_REQUIRED": _("This action requires approval before it can proceed."),
        "APPROVAL_STALE": _("The approval no longer matches the current version of this record."),
        "DISCOUNT_LIMIT_EXCEEDED": _("Discount exceeds your permitted limit and requires approval."),
        "PRICE_OVERRIDE_REQUIRES_APPROVAL": _("A custom price requires approval."),
        "ZERO_PRICE_REQUIRES_APPROVAL": _("A zero-price line requires approval."),
        "QUOTE_NOT_ACCEPTED": _("The quote must be accepted before an order can be created."),
        "CUSTOMER_REQUIRED": _("A confirmed customer is required before an order can be created."),
        "CATALOG_ITEM_INACTIVE": _("This product or plan is not currently sellable."),
        "PRICE_VERSION_EXPIRED": _("This price is no longer effective."),
        "PAYMENT_NOT_CONFIRMED": _("The payment must be confirmed before it can be allocated."),
        "PAYMENT_ALREADY_CONFIRMED": _("This payment has already been confirmed."),
        "SELF_CONFIRMATION_FORBIDDEN": _("You cannot confirm a payment you submitted yourself."),
        "FULFILLMENT_NOT_ELIGIBLE": _("This order is not eligible for fulfillment yet: %(reason)s."),
        "FULFILLMENT_ALREADY_COMPLETE": _("This order line has already been fulfilled."),
        "EMPLOYEE_PROFILE_REQUIRED": _("This action requires a real employee profile."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_commission_error(code: str, **params) -> str:
    """Phase 9.5D -- presentation-boundary localization for
    app.commissions.errors.CommissionError, matching
    localize_commercial_sales_error()'s pattern."""
    messages = {
        "COMMISSION_RULE_TYPE_NOT_IMPLEMENTED": _("Commission rule type %(rule_type)s is not implemented in this phase."),
        "INVALID_COMMISSION_RATE": _("Commission rate must be a percentage greater than 0 and at most 100."),
        "INVALID_COMMISSION_FIXED_AMOUNT": _("Commission fixed amount must be a positive value with a valid currency."),
        "NO_ACTIVE_COMMISSION_RULE": _("The employee has no active commission plan/rule assignment for this date."),
        "COMMISSION_ALREADY_EARNED_FOR_ALLOCATION": _("A commission entry already exists for this payment allocation."),
        "COMMISSION_INVALID_TRANSITION": _("Cannot change commission entry status from %(from_status)s to %(to_status)s."),
        "COMMISSION_SELF_APPROVAL_FORBIDDEN": _("You cannot approve or adjust your own commission entry."),
        "COMMISSION_PAYOUT_REFERENCE_REQUIRED": _("An external payout reference is required to record a commission payout."),
        "COMMISSION_REASON_REQUIRED": _("A reason is required for this commission action."),
        "COMMISSION_ALREADY_IN_PAYOUT_BATCH": _("This commission entry is already included in a payout batch."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def expense_status_label(code: str) -> str:
    """Phase 9.5E -- real values from app.models.expenses.EXPENSE_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "SUBMITTED": _("Submitted"),
        "RETURNED": _("Returned for correction"),
        "APPROVED": _("Approved"),
        "PARTIALLY_PAID": _("Partially paid"),
        "PAID": _("Paid"),
        "REJECTED": _("Rejected"),
        "VOID": _("Void"),
    }
    return labels.get(code, code)


def expense_approval_status_label(code: str) -> str:
    labels = {
        "PENDING": _("Pending"),
        "APPROVED": _("Approved"),
        "REJECTED": _("Rejected"),
        "RETURNED": _("Returned"),
        "CANCELLED": _("Cancelled"),
    }
    return labels.get(code, code)


def expense_payment_method_label(code: str) -> str:
    labels = {
        "CASH": _("Cash"),
        "BANK_TRANSFER": _("Bank transfer"),
        "CARD_OFFLINE": _("Card (offline)"),
        "CHEQUE": _("Cheque"),
        "OTHER": _("Other"),
    }
    return labels.get(code, code)


def payee_type_label(code: str) -> str:
    labels = {
        "EXTERNAL": _("External vendor"),
        "EMPLOYEE": _("Employee beneficiary"),
    }
    return labels.get(code, code)


def cash_closing_status_label(code: str) -> str:
    """Phase 9.5E -- real values from app.models.cash_closing.CASH_CLOSING_STATUSES."""
    labels = {
        "DRAFT": _("Draft"),
        "SUBMITTED": _("Submitted"),
        "REVIEW_REQUIRED": _("Review required"),
        "APPROVED": _("Approved"),
        "REJECTED": _("Rejected"),
        "REOPENED": _("Reopened"),
        "CLOSED": _("Closed"),
    }
    return labels.get(code, code)


def report_type_label(code: str) -> str:
    labels = {
        "DAILY_OPERATIONAL_SUMMARY": _("Daily operational summary"),
        "DAILY_CASH_CLOSING_EXCEPTIONS": _("Daily cash closing exceptions"),
        "WEEKLY_OPERATIONAL_SUMMARY": _("Weekly operational summary"),
        "MONTHLY_OPERATIONAL_SUMMARY": _("Monthly operational summary"),
    }
    return labels.get(code, code)


def management_note_status_label(code: str) -> str:
    """Phase 9.5E -- real values from app.models.management_notes.MANAGEMENT_NOTE_STATUSES."""
    labels = {
        "OPEN": _("Open"),
        "IN_PROGRESS": _("In progress"),
        "DONE": _("Done"),
        "ARCHIVED": _("Archived"),
    }
    return labels.get(code, code)


def management_note_visibility_label(code: str) -> str:
    """Phase 9.5E -- real values from app.models.management_notes.MANAGEMENT_NOTE_VISIBILITIES."""
    labels = {
        "MANAGEMENT_ONLY": _("Management only"),
        "SPECIFIC_EMPLOYEES": _("Specific employees"),
        "ALL_STAFF": _("All staff"),
    }
    return labels.get(code, code)


def management_note_priority_label(code: str) -> str:
    labels = {
        "LOW": _("Low"),
        "MEDIUM": _("Medium"),
        "HIGH": _("High"),
    }
    return labels.get(code, code)


def localize_expense_error(code: str, **params) -> str:
    """Phase 9.5E -- presentation-boundary localization for
    app.expenses.errors.ExpenseError and app.cash_closing's use of the same
    class, matching localize_commercial_sales_error()'s exact pattern."""
    messages = {
        "INVALID_EXPENSE_TRANSITION": _("Cannot change expense status from %(from_status)s to %(to_status)s."),
        "REASON_REQUIRED": _("A reason is required for this action."),
        "RECORD_NOT_FOUND": _("Record not found."),
        "RECORD_ACCESS_DENIED": _("You do not have access to this record."),
        "NON_FINITE_AMOUNT": _("Amount must be a finite, positive number."),
        "CURRENCY_MISMATCH": _("Currency %(given)s does not match the expense currency %(expected)s."),
        "INVALID_CURRENCY_CODE": _("Currency must be a 3-letter ISO 4217 code."),
        "PAYEE_REQUIRED": _("A payee is required."),
        "PAYEE_INACTIVE": _("This payee is not active."),
        "CATEGORY_INACTIVE": _("This expense category is not active."),
        "PAYEE_TYPE_INVALID": _("Payee type must be EXTERNAL or EMPLOYEE."),
        "EMPLOYEE_BENEFICIARY_REQUIRED": _("An employee is required for an employee-beneficiary payee."),
        "EXTERNAL_CONTACT_NOT_ALLOWED_FOR_EMPLOYEE": _("An employee-beneficiary payee cannot have an external contact reference."),
        "INVALID_EXPENSE_APPROVAL_TRANSITION": _("Cannot change approval status from %(from_status)s to %(to_status)s."),
        "SELF_APPROVAL_FORBIDDEN": _("You cannot approve your own expense request."),
        "BENEFICIARY_APPROVAL_FORBIDDEN": _("You cannot approve an expense where you are the recorded beneficiary."),
        "APPROVAL_STALE": _("This expense was changed after the approval was requested. Reload and try again."),
        "APPROVED_AMOUNT_EXCEEDS_REQUESTED": _("Approved amount (%(approved)s) cannot exceed the requested amount (%(requested)s)."),
        "APPROVER_INELIGIBLE": _("This account is not eligible to approve this expense: %(reason)s."),
        "APPROVER_MISSING_PERMISSION": _("This account does not have permission to approve expenses."),
        "APPROVER_SUSPENDED_OR_TERMINATED": _("This approver employee profile is suspended or terminated."),
        "APPROVER_MISSING_EMPLOYEE_PROFILE": _("This approver has no active employee profile."),
        "EXPENSE_NOT_PENDING_APPROVAL": _("This expense has no pending approval request."),
        "EXPENSE_NOT_APPROVED": _("The expense must be approved before a payment can be recorded."),
        "EXPENSE_TERMINAL_STATE": _("This expense is in a terminal state (%(status)s) and cannot be paid."),
        "PAYMENT_EXCEEDS_OUTSTANDING": _("Payment amount (%(amount)s) exceeds the outstanding approved balance (%(outstanding)s)."),
        "IDEMPOTENCY_CONFLICT": _("This request conflicts with an earlier request using the same idempotency key."),
        "PAYMENT_ALREADY_REVERSED": _("This payment has already been reversed."),
        "SELF_PAYMENT_RECORDING_FORBIDDEN": _("You cannot record payment for your own expense request."),
        "ATTACHMENT_TOO_LARGE": _("Attachment exceeds the maximum allowed size."),
        "ATTACHMENT_TYPE_NOT_ALLOWED": _("This file type is not allowed for attachments."),
        "ATTACHMENT_CONTENT_MISMATCH": _("The file actual content does not match its declared type."),
        "ATTACHMENT_EMPTY": _("Attachment file is empty."),
        "ATTACHMENT_NOT_FOUND": _("Attachment not found."),
        "ATTACHMENT_ACCESS_DENIED": _("You do not have access to this attachment."),
        "ATTACHMENT_ARCHIVED": _("This attachment has been archived."),
        "INVALID_STORAGE_KEY": _("Invalid storage key."),
        "INVALID_CASH_CLOSING_TRANSITION": _("Cannot change cash closing status from %(from_status)s to %(to_status)s."),
        "CASH_CLOSING_ALREADY_EXISTS": _("A cash closing already exists for %(business_date)s %(currency)s."),
        "OPENING_CASH_OVERRIDE_REQUIRES_REASON": _("A manual opening-cash override requires a reason."),
        "OPENING_CASH_OVERRIDE_REQUIRES_PERMISSION": _("You do not have permission to override opening cash."),
        "VARIANCE_EXPLANATION_REQUIRED": _("A nonzero variance requires an explanation."),
        "SELF_APPROVAL_FORBIDDEN_CLOSING": _("You cannot approve a cash closing you prepared."),
        "CLOSING_IMMUTABLE": _("This cash closing is approved or closed and cannot be edited directly -- reopen it first."),
        "REOPEN_REQUIRES_REASON": _("Reopening a cash closing requires a reason."),
        "REOPEN_REQUIRES_PERMISSION": _("You do not have permission to reopen a cash closing."),
        "REOPEN_REQUIRES_RECENT_AUTHENTICATION": _("Reopening a cash closing requires recent re-authentication."),
        "LATE_TRANSACTION_REQUIRES_REOPEN": _("A closed business date requires reopening the closing before recording a late transaction."),
        "DUPLICATE_OVERRIDE_REQUIRES_REASON": _("Overriding a duplicate-expense warning requires a reason."),
        "SNAPSHOT_ALREADY_PUBLISHED": _("A snapshot already exists for this canonical key."),
        "SNAPSHOT_REGENERATION_REQUIRES_PERMISSION": _("You do not have permission to regenerate a report snapshot."),
        "SNAPSHOT_REGENERATION_REQUIRES_REASON": _("Regenerating a report snapshot requires a reason."),
        "EMPLOYEE_PROFILE_REQUIRED": _("This action requires a real employee profile."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template


def localize_management_note_error(code: str, **params) -> str:
    """Phase 9.5E -- presentation-boundary localization for
    app.management_notes.errors.ManagementNoteError."""
    messages = {
        "INVALID_MANAGEMENT_NOTE_TRANSITION": _("Cannot change note status from %(from_status)s to %(to_status)s."),
        "RECORD_NOT_FOUND": _("Record not found."),
        "RECORD_ACCESS_DENIED": _("You do not have access to this record."),
        "VISIBILITY_INVALID": _("Visibility must be Management only, Specific employees, or All staff."),
        "SPECIFIC_EMPLOYEES_REQUIRES_GRANTS": _("Specific-employees visibility requires at least one employee grant."),
        "TITLE_REQUIRED": _("A title is required."),
        "BODY_REQUIRED": _("A body is required."),
        "STALE_VERSION": _("This note was changed by someone else. Reload and try again."),
        "AUTHOR_SPOOF_FORBIDDEN": _("The author cannot be supplied by the client."),
    }
    template = messages.get(code)
    if template is None:
        return code
    return template % params if params else template
