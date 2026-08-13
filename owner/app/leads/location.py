"""Phase 9.5C Milestone 12 -- explicit location capture validation and
management verification.

Non-Negotiable Domain Rules 8/9/14: no continuous tracking (this module
has no polling/watch loop, only a single validate-then-store call), a
browser-reported location is never labeled "verified GPS" by default
(verified defaults False at the model level, unchanged), and exact
coordinates are kept out of exceptions/audit payloads (every error here
carries structural info -- which field, what constraint -- never the
actual lat/long value; capture_location()'s own audit call already only
records {"source", "verified"}, never coordinates).
"""
from __future__ import annotations

import math
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from app.leads.errors import LocationValidationError
from app.models.leads import LOCATION_SOURCES

_MAX_MANUAL_ADDRESS_LEN = 2000
_MAX_CLIENT_TIME_SKEW = timedelta(hours=1)


def _to_decimal(value, field_error_code: str) -> Decimal:
    try:
        as_float = float(value)
    except (TypeError, ValueError) as exc:
        raise LocationValidationError(field_error_code) from exc
    if math.isnan(as_float) or math.isinf(as_float):
        raise LocationValidationError(field_error_code)
    return Decimal(str(value))


def validate_location_fields(fields: dict, *, now) -> dict:
    """Returns a cleaned copy. Raises LocationValidationError (stable-code,
    request-context-free) on any violation. Called by the Milestone 16
    route before capture_location()/create-location -- keeps the same
    layering discipline as validate_lead_fields()."""
    cleaned = dict(fields)

    if "latitude" in cleaned and cleaned["latitude"] is not None:
        lat = _to_decimal(cleaned["latitude"], "INVALID_LATITUDE")
        if lat < -90 or lat > 90:
            raise LocationValidationError("INVALID_LATITUDE")
        cleaned["latitude"] = lat

    if "longitude" in cleaned and cleaned["longitude"] is not None:
        lon = _to_decimal(cleaned["longitude"], "INVALID_LONGITUDE")
        if lon < -180 or lon > 180:
            raise LocationValidationError("INVALID_LONGITUDE")
        cleaned["longitude"] = lon

    if "accuracy_meters" in cleaned and cleaned["accuracy_meters"] is not None:
        acc = _to_decimal(cleaned["accuracy_meters"], "INVALID_ACCURACY")
        if acc < 0:
            raise LocationValidationError("INVALID_ACCURACY")
        cleaned["accuracy_meters"] = acc

    source = cleaned.get("source")
    if source is not None and source not in LOCATION_SOURCES:
        raise LocationValidationError("INVALID_SOURCE", source=source)

    client_captured_at = cleaned.get("client_captured_at")
    if client_captured_at is not None:
        skew = abs((now - client_captured_at))
        if skew > _MAX_CLIENT_TIME_SKEW:
            raise LocationValidationError("TIMESTAMP_TOO_FAR")

    manual_address = cleaned.get("manual_address")
    if manual_address and len(manual_address) > _MAX_MANUAL_ADDRESS_LEN:
        raise LocationValidationError("MANUAL_ADDRESS_TOO_LONG")

    return cleaned


def verify_location(location, reason: str, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID):
    """Management-only action (permission-gated at the route layer).
    Requires a reason, sets verified/verified_by/verified_at/
    verification_reason, and audits the location's UUID + verification
    outcome -- never the coordinates themselves."""
    from app.audit.services import record as audit_record
    from app.extensions import db_session
    from app.models.base import utcnow

    if not (reason or "").strip():
        raise LocationValidationError("REASON_REQUIRED_FOR_VERIFY")

    location.verified = True
    location.verification_method = "MANAGEMENT_REVIEW"
    location.verified_by_employee_profile_id = actor_employee_profile_id
    location.verified_at = utcnow()
    location.verification_reason = reason
    location.version += 1
    db_session.commit()

    parent_type = "lead" if location.lead_id else "customer"
    parent_id = location.lead_id or location.customer_id
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="LOCATION_VERIFIED",
        entity_type=parent_type,
        entity_public_id=str(parent_id),
        reason=reason,
        after_state={"location_id": str(location.id), "verified": True},
    )
    return location
