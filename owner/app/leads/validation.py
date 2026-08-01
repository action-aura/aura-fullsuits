"""Phase 9.5C Milestone 4 -- bounded, explainable Lead field validation.

Deliberately not a general-purpose validation framework: a small set of
functions matching exactly what docs/owner/phase9_5c/lead-create-update-
contract.md requires, reused by both create_lead() and update_lead().
Raises LeadError (stable-code, request-context-free) on any violation.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from app.extensions import db_session
from app.leads.errors import LeadError
from app.models.employees import EmployeeProfile
from app.models.leads import LEAD_PRIORITIES, LEAD_SOURCES

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_NAME_LEN = 200
_MAX_PHONE_LEN = 32
_MAX_EMAIL_LEN = 200
_MAX_LOCATION_SUMMARY_LEN = 200
_ISO_4217_LEN = 3


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def validate_lead_fields(fields: dict, *, is_update: bool = False) -> dict:
    """Returns a cleaned copy of fields. Raises LeadError on any violation.
    is_update=True skips the "at least one contact method" rule for a
    partial update that doesn't touch phone/email at all."""
    cleaned = dict(fields)

    name = cleaned.get("organization_or_prospect_name")
    if name is not None:
        name = name.strip()
        if not is_update and not name:
            raise LeadError("LEAD_NAME_REQUIRED")
        if len(name) > _MAX_NAME_LEN:
            raise LeadError("LEAD_NAME_TOO_LONG", max_len=_MAX_NAME_LEN)
        cleaned["organization_or_prospect_name"] = name or None

    phone = cleaned.get("phone")
    if phone:
        phone = phone.strip()
        if len(phone) > _MAX_PHONE_LEN:
            raise LeadError("LEAD_PHONE_TOO_LONG", max_len=_MAX_PHONE_LEN)
        cleaned["phone"] = phone

    email = cleaned.get("email")
    if email:
        email = email.strip()
        if len(email) > _MAX_EMAIL_LEN or not _EMAIL_RE.match(email):
            raise LeadError("LEAD_EMAIL_INVALID")
        cleaned["email"] = email

    if not is_update and not (cleaned.get("phone") or cleaned.get("email")):
        raise LeadError("LEAD_CONTACT_METHOD_REQUIRED")

    source = cleaned.get("source")
    if source is not None and source not in LEAD_SOURCES:
        raise LeadError("LEAD_SOURCE_INVALID", source=source)

    priority = cleaned.get("priority")
    if priority is not None and priority not in LEAD_PRIORITIES:
        raise LeadError("LEAD_PRIORITY_INVALID", priority=priority)

    estimated_value = cleaned.get("estimated_value")
    if estimated_value is not None:
        try:
            estimated_value = Decimal(str(estimated_value))
        except (InvalidOperation, ValueError) as exc:
            raise LeadError("LEAD_ESTIMATED_VALUE_INVALID") from exc
        if estimated_value < 0:
            raise LeadError("LEAD_ESTIMATED_VALUE_INVALID")
        cleaned["estimated_value"] = estimated_value
        currency = cleaned.get("currency")
        if not currency or len(currency.strip()) != _ISO_4217_LEN:
            raise LeadError("LEAD_CURRENCY_REQUIRED_WITH_VALUE")
        cleaned["currency"] = currency.strip().upper()

    location_summary = cleaned.get("location_summary")
    if location_summary and len(location_summary) > _MAX_LOCATION_SUMMARY_LEN:
        raise LeadError("LEAD_LOCATION_SUMMARY_TOO_LONG", max_len=_MAX_LOCATION_SUMMARY_LEN)

    assigned_id = cleaned.get("assigned_employee_profile_id")
    if assigned_id is not None:
        if isinstance(assigned_id, str):
            assigned_id = uuid.UUID(assigned_id)
        profile = db_session.get(EmployeeProfile, assigned_id)
        if profile is None or profile.employment_status != "ACTIVE":
            raise LeadError("DESTINATION_EMPLOYEE_NOT_ACTIVE")
        cleaned["assigned_employee_profile_id"] = assigned_id

    return cleaned
