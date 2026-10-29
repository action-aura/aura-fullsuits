"""Keep-selling, Owner side, step 1: the sync relay refuses an UNPAID licence.

Owner decision 2026-10-24: a shop whose subscription lapsed unpaid keeps
selling but loses multi-device sync; after renewal sync catches up with nothing
lost. The device pauses itself, but that probe fails open, so this server gate
is the authoritative one.

Every refusal test is paired with an allow-side test (ENGINEERING 1: a gate that
both denies and allows is half-tested by default): PAST_DUE inside grace and
ACTIVE must still sync, and a renewed licence must catch up without a
RESYNC_REQUIRED.
"""
from __future__ import annotations

import pytest

from tests.conftest import make_staff
from tests.support_unpaid import set_subscription_state
from tests.test_sync_routes import _do_activation, _make_event, _pull, _pull_body, _push, _push_body


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    staff_id = make_staff(app, "ks-sync@example.com")
    license_id, full_key, installation_id, key = _do_activation(app, client, staff_id, device_limit=2)
    return {"staff": staff_id, "license_id": license_id, "full_key": full_key, "installation": installation_id, "key": key}


def _push_ok(client, shop):
    return _push(client, _push_body(shop["key"], shop["installation"], [_make_event()]))


def test_active_licence_syncs(client, shop):
    assert _push_ok(client, shop).status_code == 200


def test_expired_subscription_is_refused_on_push_and_pull(app, client, shop):
    set_subscription_state(app, shop["license_id"], "EXPIRED")
    push = _push_ok(client, shop)
    assert push.status_code == 400 and push.get_json() == {"reason_code": "SUBSCRIPTION_LAPSED"}
    pull = _pull(client, _pull_body(shop["key"], shop["installation"], since=0))
    assert pull.status_code == 400 and pull.get_json() == {"reason_code": "SUBSCRIPTION_LAPSED"}


def test_past_due_past_grace_is_refused(app, client, shop):
    set_subscription_state(app, shop["license_id"], "PAST_DUE", past_due_days_ago=11, staff_id=shop["staff"])
    resp = _push_ok(client, shop)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "SUBSCRIPTION_LAPSED"


def test_past_due_inside_grace_still_syncs(app, client, shop):
    set_subscription_state(app, shop["license_id"], "PAST_DUE", past_due_days_ago=2, staff_id=shop["staff"])
    assert _push_ok(client, shop).status_code == 200


@pytest.mark.parametrize("status", ["CANCELLED", "SUSPENDED"])
def test_cancelled_and_suspended_are_not_this_gate(app, client, shop, status):
    """They keep today's behaviour: _authenticate never read the subscription for
    them, so sync is NOT newly refused with SUBSCRIPTION_LAPSED (their lock is the
    till's, and the licence's own status checks)."""
    set_subscription_state(app, shop["license_id"], status)
    resp = _push_ok(client, shop)
    assert resp.status_code == 200, resp.get_json()


def test_renewal_catches_up_with_nothing_pruned_past(app, client, shop):
    # Second device on the same licence so there is something to pull.
    _, _, inst_b, key_b = _do_activation(app, client, shop["staff"], license_id=shop["license_id"],
                                         full_key=shop["full_key"], device_limit=2)
    e1, e2 = _make_event(), _make_event()
    assert _push(client, _push_body(shop["key"], shop["installation"], [e1])).status_code == 200
    pulled = _pull(client, _pull_body(key_b, inst_b, since=0)).get_json()
    cursor = pulled["cursor"]
    # Both devices acknowledge e1, so e1 is legitimately prunable.
    assert _pull(client, _pull_body(key_b, inst_b, since=cursor)).status_code == 200
    assert _pull(client, _pull_body(shop["key"], shop["installation"], since=cursor)).status_code == 200
    assert _push(client, _push_body(shop["key"], shop["installation"], [e2])).status_code == 200

    set_subscription_state(app, shop["license_id"], "EXPIRED")
    assert _pull(client, _pull_body(key_b, inst_b, since=cursor)).get_json() == {"reason_code": "SUBSCRIPTION_LAPSED"}

    # A real prune run while the licence is lapsed must keep e2: the installations
    # are still in the syncable set, so their cursors still hold the watermark.
    with app.app_context():
        from app.sync.pruning import prune_owner_sync_events

        prune_owner_sync_events(dry_run=False)
        from app.extensions import db_session
        from app.models.sync import SyncEvent
        import uuid

        assert db_session.get(SyncEvent, uuid.UUID(e2["id"])) is not None

    set_subscription_state(app, shop["license_id"], "ACTIVE")
    resp = _pull(client, _pull_body(key_b, inst_b, since=cursor))
    assert resp.status_code == 200, resp.get_json()
    assert [e["id"] for e in resp.get_json()["events"]] == [e2["id"]]
