"""Launch-readiness (2026-09-03): the activation response's optional
sync_relay_base_url field -- see products/retail/backend/config.py's
matching read-side comment for the full design. Owner tells a device where
its shop syncs at the exact moment the device has just proved it holds a
valid licence (activation), the same moment it already learns its
server-assigned installation_id.

Byte-identical-when-unset is the whole point: an Owner deploy that never
sets OWNER_SYNC_RELAY_PUBLIC_URL must produce an activation response with
the key OMITTED, not present-and-null/empty -- see the "absent, not null"
assertion below. Real HTTP surface, no mocks, same convention as
test_phase6_activation_protocol.py.
"""
from __future__ import annotations

import json

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff


def _activate(client, body):
    return client.post("/api/licensing/v1/activations", data=json.dumps(body), content_type="application/json")


def test_sync_relay_base_url_present_when_owner_configures_it(app, client, seeded, signing_key):
    app.config["SYNC_RELAY_PUBLIC_URL"] = "https://relay.actionaura.example"
    actor_id = make_staff(app, "sync-relay-actor1@example.com")
    _license_id, full_key = make_license(app, actor_id, device_limit=1)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="sync-relay-dev-001")

    resp = _activate(client, body)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "SUCCESS"
    assert data["sync_relay_base_url"] == "https://relay.actionaura.example"


def test_sync_relay_base_url_absent_not_null_when_owner_never_configures_it(app, client, seeded, signing_key):
    # BaseConfig defaults SYNC_RELAY_PUBLIC_URL to "" when the env var is
    # unset -- this is the byte-identical-to-today path every existing
    # Owner deploy takes; asserted explicitly so this test fails loudly if
    # some other test in the process ever leaks a value onto app.config.
    assert app.config.get("SYNC_RELAY_PUBLIC_URL", "") == ""
    actor_id = make_staff(app, "sync-relay-actor2@example.com")
    _license_id, full_key = make_license(app, actor_id, device_limit=1)
    private_key = make_device_keypair()
    body = build_activation_body(private_key, full_key=full_key, installation_id="sync-relay-dev-002")

    resp = _activate(client, body)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["result"] == "SUCCESS"
    # Absent, not null and not empty-string -- a client checking
    # `"sync_relay_base_url" in data` must see False, matching the
    # byte-identical-response contract the design requires.
    assert "sync_relay_base_url" not in data
