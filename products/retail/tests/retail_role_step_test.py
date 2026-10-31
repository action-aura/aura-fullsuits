"""rc.19 -- the "which computer is this?" step is kept on the device, not only in the browser.

Dress rehearsal 2026-10-31: the marker lived in webview localStorage only, so a reload landed on
"Connecting to your shop ... your account is on its way from your other device" -- false for a new
shop's first computer. The step is now a small device-local state machine in config.json
(pending / ADMIN / CASHIER) served by /api/onboarding/role-step. The browser half is pinned by
retail_account_flow_e2e.py (A6f, A7e, B5d, B5e); this file pins the backend half.

Both directions of the guard are tested: it must REMEMBER while first-run is open, and it must
FORGET / REFUSE once an admin exists (the allow-half and the deny-half).
"""
import json
import sys
import time
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from commercial_runtime.identity import onboarding_routes as orr  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    state = {"admin": False}
    monkeypatch.setattr(orr, "_admin_exists", lambda: state["admin"])
    app = Flask(__name__)
    app.register_blueprint(orr.onboarding_bp)
    c = app.test_client()
    c.state = state
    c.cfg = tmp_path / "config.json"
    return c


def test_nothing_stored_means_no_step(client):
    assert client.get("/api/onboarding/role-step").get_json() == {"role_step": None}


def test_pending_choice_and_email_survive_across_requests(client):
    r = client.post("/api/onboarding/role-step", json={"state": "pending", "email": "boss@x.test"})
    assert r.status_code == 200
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] == {"state": "pending", "email": "boss@x.test"}
    # choosing keeps the e-mail the activation form already captured
    client.post("/api/onboarding/role-step", json={"state": "ADMIN"})
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] == {"state": "ADMIN", "email": "boss@x.test"}
    # and it is really on disk (device-local config.json), not in process memory
    assert json.loads(client.cfg.read_text())["role_step"]["state"] == "ADMIN"


def test_unknown_state_is_refused(client):
    assert client.post("/api/onboarding/role-step", json={"state": "OWNER"}).status_code == 400
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] is None


def test_once_an_admin_exists_it_cannot_be_set_and_is_forgotten(client):
    client.post("/api/onboarding/role-step", json={"state": "CASHIER"})
    client.state["admin"] = True
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] is None
    assert json.loads(client.cfg.read_text()).get("role_step") is None
    r = client.post("/api/onboarding/role-step", json={"state": "pending"})
    assert r.status_code == 409
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] is None


def test_a_stale_step_ages_out(client):
    client.post("/api/onboarding/role-step", json={"state": "pending"})
    cfg = json.loads(client.cfg.read_text())
    cfg["role_step"]["at"] = time.time() - orr.ROLE_STEP_MAX_AGE_SECONDS - 5
    client.cfg.write_text(json.dumps(cfg))
    assert client.get("/api/onboarding/role-step").get_json()["role_step"] is None


def test_other_config_keys_are_untouched(client):
    client.cfg.write_text(json.dumps({"company_id": "abc", "branch_uid": "u1"}))
    client.post("/api/onboarding/role-step", json={"state": "pending"})
    orr.clear_role_step()
    cfg = json.loads(client.cfg.read_text())
    assert cfg["company_id"] == "abc" and cfg["branch_uid"] == "u1"
