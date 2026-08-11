"""Phase 9 Milestone 3 -- liveness/readiness contract tests."""
from __future__ import annotations


def test_live_is_always_ok(client):
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_ready_reports_ok_with_healthy_dependencies(client, seeded, signing_key):
    # trust_anchor.json is a real build-time artifact (gitignored) pinned to
    # a specific key_id -- it will not match this test's ephemeral
    # per-test signing key, so it correctly still FAILs here (that check has
    # its own dedicated coverage in test_commercial_ops_preflight.py). This
    # test only asserts what the readiness endpoint itself is responsible
    # for: real DB/migration checks present and OK when the DB genuinely is.
    resp = client.get("/health/ready")
    body = resp.get_json()
    assert resp.status_code in (200, 503)
    checks_by_name = {c["name"]: c["status"] for c in body["checks"]}
    assert checks_by_name["database_connectivity"] == "OK"
    assert checks_by_name["migration_at_head"] == "OK"
    assert checks_by_name["active_signing_key_exists"] == "OK"


def test_ready_reports_not_ready_without_a_signing_key(client, seeded):
    # No signing_key fixture here -- a genuinely unprovisioned environment
    # (fresh checkout, no `flask licensing generate-signing-key` run yet)
    # must fail closed, not silently report ready.
    resp = client.get("/health/ready")
    body = resp.get_json()
    assert resp.status_code == 503
    assert body["ready"] is False
    assert {"name": "active_signing_key_exists", "status": "FAIL"} in body["checks"]


def test_ready_never_leaks_a_secret_value(client, seeded):
    resp = client.get("/health/ready")
    raw = resp.get_data(as_text=True)
    assert "insecure-pepper" not in raw
    assert "aura_owner_dev" not in raw  # no DB credential/connection string
    assert "postgresql" not in raw
    for check in resp.get_json()["checks"]:
        assert set(check.keys()) == {"name", "status"}  # no free-text "detail" exposed


def test_ready_fails_closed_when_database_unreachable(client, app, monkeypatch):
    from app import health as health_module

    monkeypatch.setattr(health_module, "_check_database", lambda: (False, "simulated outage"))
    resp = client.get("/health/ready")
    body = resp.get_json()
    assert resp.status_code == 503
    assert body["ready"] is False
    assert {"name": "database_connectivity", "status": "FAIL"} in body["checks"]
    # A DB failure must short-circuit the migration check rather than crash on it.
    assert not any(c["name"] == "migration_at_head" for c in body["checks"])
