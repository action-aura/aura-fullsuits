import json

import pytest

from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
)
from commercial_runtime.licensing_contracts.status_presenter import present_status


def test_none_record_presents_not_configured():
    assert present_status(None) == {"current_state": "NOT_CONFIGURED"}


def test_presents_safe_fields_only():
    record = LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        owner_installation_id="inst-1",
        license_status="ACTIVE",
        entitlements_json=json.dumps({"max_devices": 2}),
    )
    result = present_status(record)
    assert result["current_state"] == "ACTIVE_ONLINE"
    assert result["installation_id"] == "inst-1"
    assert result["entitlements"] == {"max_devices": 2}


def test_never_exposes_assertion_envelope_or_device_fingerprint():
    record = LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        assertion_envelope_json=json.dumps({"payload": {"secret": "x"}}),
        device_public_key_fingerprint="fp-secret-ish",
    )
    result = present_status(record)
    assert "assertion_envelope_json" not in result
    assert "device_public_key_fingerprint" not in result
    assert "fp-secret-ish" not in json.dumps(result)


# ── Phase 1b: plan / term / seats, read from the persisted envelope ─────────

def _record(**over):
    base = dict(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
    )
    base.update(over)
    return LicenseStateRecord(**base)


def _envelope(**payload):
    return json.dumps({"payload": payload, "signature": "sig-must-not-leak"})


def test_plan_term_and_seat_count_come_from_the_persisted_envelope():
    result = present_status(_record(assertion_envelope_json=_envelope(
        plan_code="RETAIL_STANDARD", allowed_device_count=2,
        term_start="2026-09-01T00:00:00+00:00", term_end="2027-09-01T00:00:00+00:00",
        device_key_fingerprint="fp-secret-ish",
    )))
    assert result["plan_code"] == "RETAIL_STANDARD"
    assert result["allowed_device_count"] == 2
    assert result["term"] == {"start": "2026-09-01T00:00:00+00:00", "end": "2027-09-01T00:00:00+00:00"}
    dumped = json.dumps(result)
    assert "sig-must-not-leak" not in dumped and "fp-secret-ish" not in dumped
    assert "assertion_envelope_json" not in result


def test_no_envelope_means_unknown_not_zero():
    """DENY half of the seat count: no envelope must give None, never 0 -- 0
    would render as 'no devices allowed' for a shop that simply has no
    assertion cached yet."""
    result = present_status(_record())
    assert result["allowed_device_count"] is None
    assert result["plan_code"] is None
    assert result["term"] == {"start": None, "end": None}


def test_a_malformed_envelope_does_not_break_the_status():
    for bad in ("not json", "[1, 2]", json.dumps({"payload": "str"}), json.dumps({"payload": None}), json.dumps(None)):
        result = present_status(_record(assertion_envelope_json=bad))
        assert result["current_state"] == "ACTIVE_ONLINE", bad
        assert result["allowed_device_count"] is None, bad


def test_wrongly_typed_payload_values_are_unknown_not_echoed():
    result = present_status(_record(assertion_envelope_json=_envelope(
        allowed_device_count=True, plan_code=7, term_start=5, term_end=["x"])))
    assert result["allowed_device_count"] is None
    assert result["plan_code"] is None
    assert result["term"] == {"start": None, "end": None}


def test_the_status_carries_no_seat_count_only_what_the_licence_allows():
    """A `devices_used` field nothing ever filled in shipped as a permanent null
    that read like data (Phase 1b fix round F11). The seat COUNT belongs to the
    site-relay's device list; the licence status carries only the allowed
    number. DENY half: no key of that name, and a caller that still passes one
    fails loudly instead of being silently ignored. ALLOW half: the allowed
    number is still there."""
    result = present_status(_record(assertion_envelope_json=_envelope(allowed_device_count=3)))
    assert "devices_used" not in result
    assert result["allowed_device_count"] == 3
    with pytest.raises(TypeError):
        present_status(_record(), devices_used=1)
