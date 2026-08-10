import json
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


# -- AUDIT P0-3: fail closed on a corrupted persisted current_state --------
# LicenseState(record.current_state) raised a bare, unhandled ValueError
# before this fix whenever the persisted string didn't match any known
# LicenseState member (a partial write, or a future enum value an older
# client build doesn't recognize yet) -- an unhandled 500 on every guarded
# route for as long as the corruption persisted. Deny-by-default is this
# module's own stated design (see its module docstring); this proves the
# corrupt-state path actually denies rather than crashing OR silently
# granting access.


def test_corrupted_current_state_denies_mutation_403_not_500(flask_app, app_data_dir):
    _set_state(app_data_dir, "TOTALLY_NOT_A_REAL_STATE")
    resp = flask_app.test_client().post("/patients")
    assert resp.status_code == 403
    assert resp.get_json()["reason_code"] == "LICENSE_INACTIVE"


def test_corrupted_current_state_denies_read_too(flask_app, app_data_dir):
    # Unlike a genuinely-known RESTRICTED state (which still allows reads
    # via restricted_mode_allowlist), an UNPARSEABLE state must not be
    # special-cased into any allowlist -- deny-by-default means deny
    # everything until a human resets it, never guess it might be one of
    # the "safe" states.
    _set_state(app_data_dir, "TOTALLY_NOT_A_REAL_STATE")
    resp = flask_app.test_client().get("/patients")
    assert resp.status_code == 403


def test_corrupted_current_state_records_capability_denied_event(flask_app, app_data_dir):
    _set_state(app_data_dir, "TOTALLY_NOT_A_REAL_STATE")
    flask_app.test_client().post("/patients")
    events = LicensingEventRecorder(_db_path(app_data_dir)).recent()
    denied = [e for e in events if e.event_type == "CAPABILITY_DENIED"]
    assert len(denied) == 1
    assert denied[0].details["capability_code"] == "clinic.patient.create"
