"""LicensingStatusPresenter (Part C) -- pure formatting of persisted state
into a safe, JSON-serializable display dict. Never authoritative: this
module makes no decision, calls no other licensing module's logic, and
exists purely so every product's frontend renders the same shape instead of
each one reaching into LicenseStateRecord's raw fields directly.
"""
from __future__ import annotations

import json
from typing import Optional

from .state_repository import LicenseStateRecord

# User-facing copy is a product/UI concern (Part G's screen list) -- this
# module only supplies the stable status vocabulary and safe raw fields;
# it does not embed English strings that would need localization here.

_SAFE_TOP_LEVEL_FIELDS = (
    "product_code",
    "platform",
    "current_state",
    "license_status",
    "installation_status",
    "subscription_status",
    "last_successful_checkin_at",
    "last_sync_result",
    "last_public_reason_code",
)


def present_status(record: Optional[LicenseStateRecord]) -> dict:
    if record is None:
        return {"current_state": "NOT_CONFIGURED"}

    result = {field: getattr(record, field) for field in _SAFE_TOP_LEVEL_FIELDS}
    result["entitlements"] = json.loads(record.entitlements_json) if record.entitlements_json else {}

    if record.assertion_expires_at:
        result["assertion_expires_at"] = record.assertion_expires_at
    if record.owner_installation_id:
        # Only the installation id, never any assertion/device secret material.
        result["installation_id"] = record.owner_installation_id

    return result
