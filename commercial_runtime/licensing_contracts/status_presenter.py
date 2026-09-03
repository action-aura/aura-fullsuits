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
    if record.sync_relay_base_url:
        # The address Owner told this device to sync to, learned at
        # activation. Exposed for the same reason `installation_id` above is:
        # it is a PUBLIC server address, not secret material, and a client
        # that has already authenticated to this local API needs it.
        #
        # ANDROID IS WHY THIS IS HERE. On Windows the value is read straight
        # out of licensing.db by `products/retail/backend/config.py`. Android's
        # Kotlin layer has no access to that file -- it reaches licence state
        # only through this local HTTP surface -- so without this field the
        # phone can never learn where to sync and its relay address stays
        # frozen at whatever was compiled into the APK. Omitted entirely when
        # unset, so an install that has never activated, or an Owner that
        # never configured a relay, sees exactly the response it saw before.
        result["sync_relay_base_url"] = record.sync_relay_base_url

    return result
