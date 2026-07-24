import json

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
