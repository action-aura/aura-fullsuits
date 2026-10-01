"""AUDIT-033 / S-AUTH, D3 -- a signed authority event survives the LAN hub.

The hub stores a pushed event in `site_sync_events`, serves it to the other tills
and forwards it upstream. None of those hops may drop, alter or re-render
`payload._auth`: the receiver at the far end verifies the ORIGINAL signer's
signature, never the hub's. The hub's own self-sync (it signs its own outbox
through the same `read_outbox`) is covered by the signing tests in
test_sync_authority_retail.py.

Same real-schema bootstrap as test_site_relay_forwarder.py.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_hub.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_DATA_DIR = Path(tempfile.mkdtemp(prefix="sauth_hub_test_"))
os.environ["AURA_APP_DATA"] = str(_DATA_DIR)

from products.retail.backend.database import schema as retail_schema  # noqa: E402

from commercial_runtime.sync import authority  # noqa: E402
from commercial_runtime.sync.site_relay import forwarder, store  # noqa: E402
from commercial_runtime.sync.tests._signing import SigningWorld  # noqa: E402


def teardown_module(module):
    shutil.rmtree(_DATA_DIR, ignore_errors=True)


retail_schema.init_retail()
_DB_PATH = retail_schema._get_path("retail")


@pytest.fixture
def conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("DELETE FROM site_sync_events")
    c.execute("UPDATE site_forward_cursor SET forwarded_to_seq = 0, cloud_pull_seq = 0 WHERE id = 1")
    c.commit()
    try:
        yield c
    finally:
        c.close()


class _Cloud:
    def __init__(self):
        self.pushed = []

    def push(self, events):
        self.pushed.extend(events)
        return {"stored": len(events), "received": len(events)}

    def pull(self, since):
        return {"events": [], "cursor": since}


def _signed_event(world, signer):
    ev = {"id": str(uuid.uuid4()), "entity_type": "till_allocation", "entity_id": str(uuid.uuid4()),
          "event_type": "create", "created_at": datetime.now(timezone.utc).isoformat(),
          "payload": {"id": "al-1", "till_number": 2, "admin_epoch": 1, "row_version": 1}}
    return world.sign(ev, signer)


def test_auth_survives_hub_storage_serving_and_forwarding(conn, tmp_path):
    world = SigningWorld(tmp_path)
    sender = world.new_device("dev-main")
    event = _signed_event(world, sender)

    store.append_events(conn, [event], origin_device_id="dev-main")
    conn.commit()

    # served to another LAN till
    [served] = store.read_events_since(conn, since=0, exclude_device_id="dev-other", limit=10)
    served_event = {k: served[k] for k in ("id", "entity_type", "entity_id", "event_type", "payload", "created_at")}
    assert served_event["payload"]["_auth"] == event["payload"]["_auth"]
    assert isinstance(authority.verify_event(served_event, trust_store=world.trust_store, own_licence=world.licence),
                      authority.VerifiedSigner)

    # forwarded upstream under the hub's own transport signature: payload untouched
    cloud = _Cloud()
    assert forwarder.forward_up(conn, cloud) == 1
    [forwarded] = cloud.pushed
    assert forwarded["payload"] == event["payload"]
    verdict = authority.verify_event(forwarded, trust_store=world.trust_store, own_licence=world.licence)
    assert isinstance(verdict, authority.VerifiedSigner) and verdict.dev == "dev-main"


def test_a_hub_that_alters_the_payload_in_transit_is_caught(conn, tmp_path):
    """The point of signing end to end: the hub is not trusted to be honest."""
    world = SigningWorld(tmp_path)
    event = _signed_event(world, world.new_device("dev-main"))
    tampered = dict(event, payload=dict(event["payload"], till_number=9))
    store.append_events(conn, [tampered], origin_device_id="dev-main")
    conn.commit()
    cloud = _Cloud()
    forwarder.forward_up(conn, cloud)
    verdict = authority.verify_event(cloud.pushed[0], trust_store=world.trust_store, own_licence=world.licence)
    assert isinstance(verdict, authority.Refusal) and verdict.reason == authority.REASON_BAD_SIGNATURE
