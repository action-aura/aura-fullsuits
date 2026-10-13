"""SYNC-BOOT Phase 2: the hub forwarder parks and reports Owner's RESYNC_REQUIRED.

Before: Owner answered the forwarder's pull with 409 RESYNC_REQUIRED and the tick surfaced as a FAILED
tick every cycle, re-contacting Owner each time with the same cursor that can never succeed. After:
the first refusal is logged once and the pull half stops contacting THAT relay; the push half is
untouched (nothing the hub holds is lost by waiting); a different relay lifts the parking.

Mutations that turned a named test red: parking removed (Owner contacted every tick); parking not
keyed by relay (a new URL stays parked); parking also stops the push half.
"""
import logging
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

from commercial_runtime.sync.relay_client import RESYNC_REQUIRED, RelayRejected
from commercial_runtime.sync.site_relay import forwarder, store

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "products" / "retail" / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from database import schema as retail_schema  # noqa: E402


class Cloud:
    supports_stream = True

    def __init__(self, base_url, *, refuse=True):
        self.base_url = base_url
        self.refuse = refuse
        self.pulls = 0
        self.pushes = 0

    def pull(self, since, stream=None):
        self.pulls += 1
        if self.refuse:
            raise RelayRejected(RESYNC_REQUIRED, "behind", {"pruned_through": 40})
        return {"events": [], "cursor": since}

    def push(self, events):
        self.pushes += 1
        return {"stored": len(events), "received": len(events)}


@pytest.fixture(autouse=True)
def _unparked():
    forwarder._RESYNC_PARKED.clear()
    yield
    forwarder._RESYNC_PARKED.clear()


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "hub.db"))
    c.row_factory = sqlite3.Row
    retail_schema._migrate_add_site_relay(c)
    c.commit()
    yield c
    c.close()


def _push_one_local_event(conn):
    store.append_events(conn, [{"id": str(uuid.uuid4()), "entity_type": "category", "entity_id": str(uuid.uuid4()),
                                "event_type": "create", "payload": {"n": 1}, "created_at": "now"}],
                        origin_device_id="local-device")
    conn.commit()


def test_the_first_refusal_is_reported_and_later_ticks_do_not_contact_owner(conn, caplog):
    cloud = Cloud("https://owner.example")
    with caplog.at_level(logging.ERROR, logger=forwarder.logger.name):
        first = forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
        second = forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
        third = forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
    assert first["pull_error"] == second["pull_error"] == third["pull_error"] == RESYNC_REQUIRED
    assert cloud.pulls == 1                                    # asked ONCE, then parked
    refusals = [r for r in caplog.records if "STOPS PULLING" in r.getMessage()]
    assert len(refusals) == 1 and "40" in refusals[0].getMessage()


def test_parking_does_not_stop_the_push_half(conn):
    cloud = Cloud("https://owner.example")
    forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)          # parks the pull
    _push_one_local_event(conn)
    result = forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
    assert result["forwarded"] == 1 and result["push_error"] is None
    assert cloud.pushes == 1


def test_a_different_relay_lifts_the_parking(conn):
    old = Cloud("https://owner.example")
    forwarder.run_once(conn, old, apply_pulled=lambda e: None)
    assert old.pulls == 1
    new = Cloud("https://owner-new.example", refuse=False)
    result = forwarder.run_once(conn, new, apply_pulled=lambda e: None)
    assert new.pulls == 1 and result["pull_error"] is None             # allow half: not stuck forever


def test_other_failures_are_not_parked(conn):
    class Flaky(Cloud):
        def pull(self, since, stream=None):
            self.pulls += 1
            raise RelayRejected("INVALID_SIGNATURE", "no")
    cloud = Flaky("https://owner.example")
    forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
    forwarder.run_once(conn, cloud, apply_pulled=lambda e: None)
    assert cloud.pulls == 2
