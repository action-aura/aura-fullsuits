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


# The ONLY assertion-payload fields the Plan & devices screen may read, and
# they are copied out one by one -- the envelope itself (signature, device
# fingerprint, offline policy) never crosses this module's boundary.
# Phase 1b (2026-09-29): the Plan & devices screen needs "which plan, until
# when, how many devices". All three already sit in the PERSISTED signed
# envelope (assertion_verifier.ALLOWED_PAYLOAD_FIELDS), so this is a read of
# existing state -- no schema change, no new column, no new network call.
def _payload_of(record: LicenseStateRecord) -> dict:
    """The verified assertion's payload, or {} when there is none / it cannot
    be read. Deliberately forgiving: a licence screen that 500s because one
    stored blob is malformed would hide the very state the owner came to see.
    The envelope was signature-verified BEFORE it was persisted; this only
    reads it back, so a malformed blob is corruption, not an attack."""
    raw = record.assertion_envelope_json
    if not raw:
        return {}
    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    return payload if isinstance(payload, dict) else {}


def present_status(record: Optional[LicenseStateRecord]) -> dict:
    """No seat COUNT here, on purpose. The licence store does not know how many
    computers are paired (that is the site-relay's device list), and a
    `devices_used` field that no caller ever filled in would ship as a
    permanent null that looks like data. The Plan & devices screen counts from
    the relay list it already fetches; `allowed_device_count` below is the only
    seat figure the licence itself carries."""
    if record is None:
        return {"current_state": "NOT_CONFIGURED"}

    result = {field: getattr(record, field) for field in _SAFE_TOP_LEVEL_FIELDS}
    result["entitlements"] = json.loads(record.entitlements_json) if record.entitlements_json else {}

    payload = _payload_of(record)
    count = payload.get("allowed_device_count")
    # bool is an int subclass in Python; a stray True must not read as "1 device".
    result["allowed_device_count"] = count if isinstance(count, int) and not isinstance(count, bool) else None
    result["plan_code"] = payload.get("plan_code") if isinstance(payload.get("plan_code"), str) else None
    result["term"] = {
        "start": payload.get("term_start") if isinstance(payload.get("term_start"), str) else None,
        "end": payload.get("term_end") if isinstance(payload.get("term_end"), str) else None,
    }
    # AUDIT-033 fix round (verifier V finding #1): does the stored Owner lease
    # name this computer's registry device id? False means this computer cannot
    # sign shop-authority changes yet; the desktop shell uses it to ask Owner
    # again right away at boot instead of waiting for the next poll. Not
    # secret: it is a yes/no, never the id itself.
    attested = payload.get("registry_device_id")
    result["registry_device_attested"] = isinstance(attested, str) and bool(attested)

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
