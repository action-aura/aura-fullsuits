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
