"""Phase 9.5C -- stable-code exceptions for the leads/CRM service layer.

Reuses the shared StableCodeError base from commercial_ops (Phase
9.5B-R3) rather than defining a second copy -- same request-context-free
contract: raise with a stable code + params, str(exc) is always a real
English diagnostic, localization happens only at the route boundary via
app.i18n_labels.
"""
from __future__ import annotations

from app.commercial_ops.errors import StableCodeError

LEAD_TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"POTENTIAL", "NOT_INTERESTED_NOW", "LOST"},
    "POTENTIAL": {"FOLLOW_UP", "NOT_INTERESTED_NOW", "LOST"},
    "FOLLOW_UP": {"UNDER_OBSERVATION", "NOT_INTERESTED_NOW", "LOST"},
    "UNDER_OBSERVATION": {"QUALIFIED", "NOT_INTERESTED_NOW", "LOST"},
    "QUALIFIED": {"NOT_INTERESTED_NOW", "LOST"},
    "NOT_INTERESTED_NOW": {"ARCHIVED"},
    "LOST": {"ARCHIVED"},
    "CONFIRMED": {"ARCHIVED"},
    "ARCHIVED": set(),
}

REASON_REQUIRED_TARGETS = {"LOST"}


class LeadError(StableCodeError):
    _MESSAGES = {
        "INVALID_LEAD_TRANSITION": "Cannot change lead status from {from_status} to {to_status}.",
        "REASON_REQUIRED_FOR_LOST": "A reason is required to mark a lead as lost.",
        "STALE_LEAD_VERSION": "This lead was changed by someone else. Reload and try again.",
        "LEAD_NOT_FOUND": "Lead not found.",
        "LEAD_ACCESS_DENIED": "You do not have access to this lead.",
        "DESTINATION_EMPLOYEE_NOT_ACTIVE": "Cannot assign a lead to an inactive employee.",
        "REASON_REQUIRED_FOR_REASSIGN": "A reason is required to reassign a lead.",
    }


class CustomerCrmError(StableCodeError):
    _MESSAGES = {
        "STALE_CUSTOMER_VERSION": "This customer was changed by someone else. Reload and try again.",
        "CUSTOMER_ACCESS_DENIED": "You do not have access to this customer.",
        "DESTINATION_EMPLOYEE_NOT_ACTIVE": "Cannot assign a customer to an inactive employee.",
        "REASON_REQUIRED_FOR_REASSIGN": "A reason is required to reassign a customer.",
    }


class LocationValidationError(StableCodeError):
    _MESSAGES = {
        "INVALID_LATITUDE": "Latitude must be a finite number between -90 and 90.",
        "INVALID_LONGITUDE": "Longitude must be a finite number between -180 and 180.",
        "INVALID_ACCURACY": "Accuracy must be a finite number greater than or equal to zero.",
        "INVALID_SOURCE": "Unknown location source: {source}.",
        "TIMESTAMP_TOO_FAR": "Client-reported capture time is too far from server time.",
        "MANUAL_ADDRESS_TOO_LONG": "Manual address is too long.",
        "REASON_REQUIRED_FOR_VERIFY": "A reason is required to verify or revoke a location."
    }


def validate_lead_transition(from_status: str, to_status: str, reason: str | None) -> None:
    allowed = LEAD_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise LeadError("INVALID_LEAD_TRANSITION", from_status=from_status, to_status=to_status)
    if to_status in REASON_REQUIRED_TARGETS and not (reason or "").strip():
        raise LeadError("REASON_REQUIRED_FOR_LOST")
