import json
import logging
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, jsonify

from commercial_runtime.licensing_contracts.events import LicensingEventRecorder
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)

ALLOWLIST = frozenset({"clinic.records.read"})


def _db_path(app_data_dir: str) -> Path:
    return Path(app_data_dir) / "database" / "subsystems" / "licensing.db"


@pytest.fixture
def app_data_dir(tmp_path):
    return str(tmp_path / "appdata")


@pytest.fixture
def flask_app(app_data_dir):
    require_license_capability = make_capability_guard(app_data_dir)
    app = Flask(__name__)

    @app.route("/patients", methods=["POST"])
    @require_license_capability("clinic.patient.create", restricted_mode_allowlist=ALLOWLIST)
    def create_patient():
        return jsonify({"status": "success"})

    @app.route("/patients", methods=["GET"])
    @require_license_capability("clinic.records.read", restricted_mode_allowlist=ALLOWLIST)
    def list_patients():
        return jsonify({"status": "success"})

    @app.route("/settings", methods=["POST"])
    @require_license_capability(
        "clinic.settings.update",
        restricted_mode_allowlist=ALLOWLIST,
        required_entitlement="digital_receipts_enabled",
    )
    def update_settings():
        return jsonify({"status": "success"})

    return app


def _set_state(app_data_dir: str, state: str, entitlements: dict | None = None) -> None:
    repo = LicenseStateRepository(_db_path(app_data_dir))
    repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code="AURA_CLINIC",
            platform="WINDOWS",
            current_state=state,
            entitlements_json=json.dumps(entitlements or {}),
        )
    )


def _set_raw_current_state(app_data_dir: str, raw_value) -> None:
    """Writes `raw_value` (including None) directly into
    licensing_state.current_state, bypassing LicenseStateRepository.save()
    -- which only ever writes a valid LicenseState.value string -- and
    bypassing the real schema's `current_state TEXT NOT NULL` constraint,
    which SQLite enforces on every write (INSERT or UPDATE), not just at
    literal-type-checking time, so a normal write through the constrained
    table could never produce a NULL current_state.

    Only a genuinely damaged file (partial write mid-transaction, bit rot,
    a foreign tool editing the row, a future/older schema with different
    constraints) could ever put NULL there for real -- exactly the AUDIT-031
    scenario under test, hence a hand-rolled unconstrained table here rather
    than going through the repository.
    """
    db_path = _db_path(app_data_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS licensing_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                licensing_schema_version INTEGER NOT NULL,
                product_code TEXT NOT NULL,
                platform TEXT NOT NULL,
                owner_environment_id TEXT,
                owner_installation_id TEXT,
                device_public_key_fingerprint TEXT,
                current_state TEXT,
                assertion_envelope_json TEXT,
                assertion_id TEXT,
                assertion_issued_at TEXT,
                assertion_not_before TEXT,
                assertion_expires_at TEXT,
                trusted_time_anchor_server_time TEXT,
                last_successful_checkin_at TEXT,
                last_sync_result TEXT,
                last_public_reason_code TEXT,
                offline_policy_json TEXT,
                entitlements_json TEXT,
                license_status TEXT,
                installation_status TEXT,
                subscription_status TEXT,
                trusted_signing_key_ids_json TEXT,
                restriction_state_metadata_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform,
                                          current_state, updated_at)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET current_state = excluded.current_state
            """,
            (LICENSING_SCHEMA_VERSION, "AURA_CLINIC", "WINDOWS", raw_value, "2024-01-01T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def test_no_license_record_denies_mutation(flask_app):
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_active_online_allows_mutation(flask_app, app_data_dir):
    _set_state(app_data_dir, "ACTIVE_ONLINE")
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 200


def test_restricted_denies_mutation_but_allows_read(flask_app, app_data_dir):
    _set_state(app_data_dir, "RESTRICTED")
    client = flask_app.test_client()
    assert client.post("/patients").status_code == 403
    assert client.get("/patients").status_code == 200


def test_capability_denied_records_event(flask_app, app_data_dir):
    _set_state(app_data_dir, "RESTRICTED")
    flask_app.test_client().post("/patients")
    events = LicensingEventRecorder(_db_path(app_data_dir)).recent()
    denied = [e for e in events if e.event_type == "CAPABILITY_DENIED"]
    assert len(denied) == 1
    assert denied[0].details["capability_code"] == "clinic.patient.create"


def test_direct_route_call_cannot_bypass_guard(flask_app, app_data_dir):
    # Simulates "calling the API directly" (Part T's explicit requirement) --
    # there is no separate UI-only check to route around; hitting the route
    # itself, with no special headers or session state, is the only way in,
    # and it goes through the same decorator every other caller does.
    _set_state(app_data_dir, "SUSPENDED")
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403


def test_entitlement_gate_denies_when_missing(flask_app, app_data_dir):
    _set_state(app_data_dir, "ACTIVE_ONLINE", entitlements={})
    resp = flask_app.test_client().post("/settings")
    assert resp.status_code == 403
    assert resp.get_json()["reason_code"] == "CAPABILITY_NOT_ENTITLED"


def test_entitlement_gate_allows_when_present(flask_app, app_data_dir):
    _set_state(app_data_dir, "ACTIVE_ONLINE", entitlements={"digital_receipts_enabled": True})
    resp = flask_app.test_client().post("/settings")
    assert resp.status_code == 200


# --- AUDIT-031: unparseable current_state must deny cleanly, never crash ---
#
# Reproduces the live defect exactly: `licensing.db` holding a current_state
# string that is not a LicenseState member ("ACTIVE" -- plausible-looking,
# not a real member; the real members are ACTIVE_ONLINE/ACTIVE_OFFLINE) used
# to raise an unhandled ValueError inside the decorator on every guarded
# route -> bare HTTP 500, no JSON body. The fix routes this to
# LOCAL_STATE_CORRUPT, which is deliberately IN DATA_PRESERVED_FAMILY, so
# these tests prove both halves of that choice: mutations get a clean 403,
# and read/backup access is NOT lost along with it.


def test_unrecognized_stored_state_denies_mutation_with_clean_json_403(flask_app, app_data_dir):
    _set_state(app_data_dir, "ACTIVE")  # plausible, but not a LicenseState member
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403
    assert resp.content_type.startswith("application/json")
    body = resp.get_json()
    assert body is not None
    assert body["reason_code"] == "LICENSE_INACTIVE"
    assert body["status"] == "error"


def test_unrecognized_stored_state_still_allows_read(flask_app, app_data_dir):
    # This is the half a careless fix (e.g. deny-everything on any bad
    # state) would lose: LOCAL_STATE_CORRUPT stays in DATA_PRESERVED_FAMILY,
    # so an allowlisted read/backup/export capability keeps working.
    _set_state(app_data_dir, "ACTIVE")
    resp = flask_app.test_client().get("/patients")
    assert resp.status_code == 200


def test_empty_string_state_denies_mutation_with_clean_json_403(flask_app, app_data_dir):
    _set_state(app_data_dir, "")
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403
    assert resp.content_type.startswith("application/json")
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_empty_string_state_still_allows_read(flask_app, app_data_dir):
    _set_state(app_data_dir, "")
    resp = flask_app.test_client().get("/patients")
    assert resp.status_code == 200


def test_null_state_denies_mutation_with_clean_json_403(flask_app, app_data_dir):
    _set_raw_current_state(app_data_dir, None)
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403
    assert resp.content_type.startswith("application/json")
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_null_state_still_allows_read(flask_app, app_data_dir):
    _set_raw_current_state(app_data_dir, None)
    resp = flask_app.test_client().get("/patients")
    assert resp.status_code == 200


def test_missing_record_resolves_not_configured_not_corrupt(flask_app, app_data_dir):
    # A record that was never created (fresh install) must resolve to
    # NOT_CONFIGURED, never LOCAL_STATE_CORRUPT -- a fresh install is not a
    # damaged one. Both states behave identically through evaluate_capability
    # (both are in DATA_PRESERVED_FAMILY) so the observable proof is that NO
    # LOCAL_STATE_CORRUPT event gets recorded for a plain missing record --
    # if flask_guard mislabeled "missing" as "corrupt", this would fire.
    resp = flask_app.test_client().get("/patients")
    assert resp.status_code == 200
    events = LicensingEventRecorder(_db_path(app_data_dir)).recent()
    assert not any(e.event_type == "LOCAL_STATE_CORRUPT" for e in events)


def test_unrecognized_state_records_corruption_event_without_echoing_raw_value(flask_app, app_data_dir):
    _set_state(app_data_dir, "ACTIVE")
    flask_app.test_client().post("/patients")
    events = LicensingEventRecorder(_db_path(app_data_dir)).recent()
    corrupt_events = [e for e in events if e.event_type == "LOCAL_STATE_CORRUPT"]
    assert len(corrupt_events) == 1
    # The untrusted raw value read off disk ("ACTIVE") must never appear in
    # the recorded event's details -- only a hardcoded, trusted marker may
    # (see flask_guard._resolve_current_state's comment on why).
    assert "ACTIVE" not in json.dumps(corrupt_events[0].details)


@pytest.mark.parametrize("hostile_value", ["patient", "diagnosis", "license_key=abc", "sale_total"])
def test_corrupt_state_containing_forbidden_marker_still_denies_cleanly(
    flask_app, app_data_dir, hostile_value
):
    """The corruption handler RECORDS an event, and events.record() rejects
    details containing a FORBIDDEN_DETAIL_MARKERS substring by raising
    LicensingEventError. So if the raw stored value were ever passed into
    that event's details, a corrupt row that happens to contain "patient",
    "diagnosis", "license_key", "sale_total" (etc.) would raise INSIDE the
    handler that exists to stop a crash -- turning the AUDIT-031 500 back
    into a 500 by a longer route, and only for the subset of corrupt values
    that trip the privacy scan.

    That is not hypothetical for a damaged file: a partial write or a
    restore that splices a fragment of an adjacent TEXT column into
    current_state can plausibly leave real business/clinical wording there,
    which is exactly the wording FORBIDDEN_DETAIL_MARKERS lists. Pinned
    here across both product vocabularies (clinic: patient/diagnosis;
    retail: sale_total) because this is shared code and the retail markers
    are as reachable as the clinic ones.
    """
    _set_state(app_data_dir, hostile_value)
    client = flask_app.test_client()
    resp = client.post("/patients")
    assert resp.status_code == 403, "hostile corrupt value must deny, not crash"
    assert resp.content_type.startswith("application/json")
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"
    # ...and the recovery path survives it too, same as any other corrupt value.
    assert client.get("/patients").status_code == 200
    # The event must actually have been written -- if record() had raised and
    # been swallowed somewhere, the deny above could still look correct while
    # the corruption went unlogged.
    events = LicensingEventRecorder(_db_path(app_data_dir)).recent()
    corrupt_events = [e for e in events if e.event_type == "LOCAL_STATE_CORRUPT"]
    assert corrupt_events
    # Scoped to the corruption events on purpose: the CAPABILITY_DENIED event
    # recorded alongside legitimately carries capability_code
    # "clinic.patient.create", which contains "patient" by design (that is
    # precisely what events._guard_details's trusted_keys exemption exists
    # for). Scanning every event indiscriminately would flag that hardcoded,
    # trusted literal and assert the wrong thing.
    assert hostile_value not in json.dumps([e.details for e in corrupt_events])


def test_unrecognized_state_logs_warning_naming_offending_value(flask_app, app_data_dir, caplog):
    _set_state(app_data_dir, "ACTIVE")
    with caplog.at_level(logging.WARNING, logger="commercial_runtime.licensing_contracts.flask_guard"):
        flask_app.test_client().post("/patients")
    messages = [record.getMessage() for record in caplog.records]
    assert any("'ACTIVE'" in message and "LOCAL_STATE_CORRUPT" in message for message in messages)
