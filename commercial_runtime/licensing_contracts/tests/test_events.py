from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.licensing_contracts.events import (
    LicensingEventError,
    LicensingEventRecorder,
)


@pytest.fixture
def recorder(tmp_path):
    return LicensingEventRecorder(tmp_path / "database" / "subsystems" / "licensing.db")


def test_record_and_recent_round_trip(recorder):
    recorder.record("ACTIVATION_SUCCEEDED", {"product_code": "AURA_RETAIL"})
    events = recorder.recent()
    assert len(events) == 1
    assert events[0].event_type == "ACTIVATION_SUCCEEDED"
    assert events[0].details == {"product_code": "AURA_RETAIL"}


def test_unknown_event_type_rejected(recorder):
    with pytest.raises(LicensingEventError):
        recorder.record("SOMETHING_MADE_UP")


def test_forbidden_marker_in_details_rejected(recorder):
    with pytest.raises(LicensingEventError):
        recorder.record("CAPABILITY_DENIED", {"note": "patient record blocked"})


def test_license_key_never_persisted(recorder):
    with pytest.raises(LicensingEventError):
        recorder.record("ACTIVATION_FAILED", {"license_key": "AURA-XXXX"})


def test_recent_orders_newest_first(recorder):
    recorder.record("ACTIVATION_STARTED")
    recorder.record("ACTIVATION_SUCCEEDED")
    events = recorder.recent()
    assert events[0].event_type == "ACTIVATION_SUCCEEDED"
    assert events[1].event_type == "ACTIVATION_STARTED"


def test_recent_respects_limit(recorder):
    for _ in range(5):
        recorder.record("CHECK_IN_SUCCEEDED")
    assert len(recorder.recent(limit=3)) == 3


def test_prune_older_than_removes_old_events(recorder):
    old_time = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    with recorder._conn() as conn:
        conn.execute(
            "INSERT INTO licensing_events (event_type, occurred_at, details_json) VALUES (?, ?, ?)",
            ("CHECK_IN_SUCCEEDED", old_time, "{}"),
        )
    recorder.record("CHECK_IN_SUCCEEDED")  # fresh event
    removed = recorder.prune_older_than(days=180)
    assert removed == 1
    assert len(recorder.recent()) == 1


def test_empty_details_defaults_to_empty_dict(recorder):
    recorder.record("DEVICE_DEACTIVATED")
    assert recorder.recent()[0].details == {}


def test_trusted_keys_exempts_capability_code_style_values(recorder):
    # A capability code is a hardcoded literal from our own fixed vocabulary
    # (clinic.patient.create, clinic.prescription.create, ...) -- it must be
    # recordable even though it contains substrings ("patient",
    # "prescription") that are forbidden when they appear as real data.
    recorder.record(
        "CAPABILITY_DENIED",
        {"capability_code": "clinic.patient.create"},
        trusted_keys=frozenset({"capability_code"}),
    )
    assert recorder.recent()[0].details == {"capability_code": "clinic.patient.create"}


def test_trusted_keys_does_not_exempt_other_fields(recorder):
    with pytest.raises(LicensingEventError):
        recorder.record(
            "CAPABILITY_DENIED",
            {"capability_code": "clinic.patient.create", "note": "patient record blocked"},
            trusted_keys=frozenset({"capability_code"}),
        )


def test_without_trusted_keys_capability_code_style_value_still_rejected(recorder):
    # Confirms the exemption is opt-in, not a silent global weakening --
    # omitting trusted_keys keeps the original strict behavior.
    with pytest.raises(LicensingEventError):
        recorder.record("CAPABILITY_DENIED", {"capability_code": "clinic.patient.create"})
