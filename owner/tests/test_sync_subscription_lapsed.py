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
from datetime import datetime, timedelta, timezone

from tests.support_unpaid import assign_policy_with_extension, grant_emergency_extension, set_subscription_state
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
def test_cancelled_and_suspended_are_refused_with_their_own_reason_code(app, client, shop, status):
    """Owner decision 2026-10-30: the sync relay refuses CANCELLED and SUSPENDED
    subscriptions too, each with its own code (not SUBSCRIPTION_LAPSED).

    This REPLACES test_cancelled_and_suspended_are_not_this_gate, which asserted
    HTTP 200 for both. That test can no longer pass, on purpose: it pinned the
    old behaviour (the relay never read the subscription for them, so a shop whose
    till was locked kept syncing) which the owner has now reversed."""
    set_subscription_state(app, shop["license_id"], status)
    push = _push_ok(client, shop)
    assert push.status_code == 400 and push.get_json() == {"reason_code": f"SUBSCRIPTION_{status}"}
    pull = _pull(client, _pull_body(shop["key"], shop["installation"], since=0))
    assert pull.status_code == 400 and pull.get_json() == {"reason_code": f"SUBSCRIPTION_{status}"}


@pytest.mark.parametrize("status", ["CANCELLED", "SUSPENDED"])
def test_an_emergency_extension_does_not_reopen_cancelled_or_suspended(app, client, shop, status):
    set_subscription_state(app, shop["license_id"], status)
    grant_emergency_extension(app, shop["license_id"], shop["staff"])
    resp = _push_ok(client, shop)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == f"SUBSCRIPTION_{status}"


def test_a_suspended_shop_that_is_reactivated_catches_up_with_nothing_pruned(app, client, shop):
    """Re-activation after a suspension: no 409 RESYNC_REQUIRED, the pending event
    is still there (same watermark construction as the renewal test: only the
    suspended device's cursor protects e2)."""
    import uuid

    _, _, inst_b, key_b = _do_activation(app, client, shop["staff"], license_id=shop["license_id"],
                                         full_key=shop["full_key"], device_limit=2)
    e1, e2 = _make_event(), _make_event()
    assert _push(client, _push_body(shop["key"], shop["installation"], [e1, e2])).status_code == 200
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        seq1 = db_session.get(SyncEvent, uuid.UUID(e1["id"])).seq
        seq2 = db_session.get(SyncEvent, uuid.UUID(e2["id"])).seq
    assert _pull(client, _pull_body(key_b, inst_b, since=0)).status_code == 200
    assert _pull(client, _pull_body(key_b, inst_b, since=seq2)).status_code == 200
    assert _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1)).status_code == 200

    set_subscription_state(app, shop["license_id"], "SUSPENDED")
    refused = _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1))
    assert refused.get_json() == {"reason_code": "SUBSCRIPTION_SUSPENDED"}
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent
        from app.sync.pruning import prune_owner_sync_events

        prune_owner_sync_events(dry_run=False)
        db_session.expire_all()
        assert db_session.get(SyncEvent, uuid.UUID(e1["id"])) is None
        assert db_session.get(SyncEvent, uuid.UUID(e2["id"])) is not None

    set_subscription_state(app, shop["license_id"], "ACTIVE")
    resp = _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1))
    assert resp.status_code == 200, resp.get_json()  # not 409 RESYNC_REQUIRED
    e3 = _make_event()
    assert _push(client, _push_body(shop["key"], shop["installation"], [e3])).status_code == 200
    caught_up = _pull(client, _pull_body(key_b, inst_b, since=seq2))
    assert [e["id"] for e in caught_up.get_json()["events"]] == [e3["id"]]


def test_renewal_catches_up_with_nothing_pruned_past(app, client, shop):
    """Only the LAPSED device's cursor protects e2 from the prune, so this fails
    if lapsing ever moves the installation out of the syncable set (the design
    the routes.py comment rules out).

    A pushes e1 and e2. B has acknowledged both (cursor = e2.seq); A has
    acknowledged only e1 (cursor = e1.seq). The prune watermark is the MINIMUM
    acknowledged seq, so with A counted it is e1.seq: e1 goes, e2 stays. If A were
    dropped from the syncable set the watermark would jump to e2.seq and e2 would
    be pruned -- the device would get 409 RESYNC_REQUIRED after renewal instead
    of catching up (verifier F8: the earlier version of this check could never
    fail, because nobody had acknowledged past e2)."""
    import uuid

    _, _, inst_b, key_b = _do_activation(app, client, shop["staff"], license_id=shop["license_id"],
                                         full_key=shop["full_key"], device_limit=2)
    e1, e2 = _make_event(), _make_event()
    assert _push(client, _push_body(shop["key"], shop["installation"], [e1, e2])).status_code == 200
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        seq1 = db_session.get(SyncEvent, uuid.UUID(e1["id"])).seq
        seq2 = db_session.get(SyncEvent, uuid.UUID(e2["id"])).seq
    assert seq1 < seq2
    # B pulls everything and acknowledges up to e2; A acknowledges only up to e1.
    pulled = _pull(client, _pull_body(key_b, inst_b, since=0)).get_json()
    assert [e["id"] for e in pulled["events"]] == [e1["id"], e2["id"]]
    assert _pull(client, _pull_body(key_b, inst_b, since=seq2)).status_code == 200
    assert _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1)).status_code == 200

    set_subscription_state(app, shop["license_id"], "EXPIRED")
    refused = _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1))
    assert refused.get_json() == {"reason_code": "SUBSCRIPTION_LAPSED"}

    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent
        from app.sync.pruning import prune_owner_sync_events

        prune_owner_sync_events(dry_run=False)
        db_session.expire_all()
        assert db_session.get(SyncEvent, uuid.UUID(e1["id"])) is None  # the prune really ran and deleted what both acked
        assert db_session.get(SyncEvent, uuid.UUID(e2["id"])) is not None  # held by the lapsed device's cursor

    set_subscription_state(app, shop["license_id"], "ACTIVE")
    resp = _pull(client, _pull_body(shop["key"], shop["installation"], since=seq1))
    assert resp.status_code == 200, resp.get_json()  # not 409 RESYNC_REQUIRED
    assert resp.get_json().get("reason_code") is None
    assert [e["id"] for e in resp.get_json()["events"]] == []  # A's own e2 is excluded from its pull
    # B can still pull what is after its cursor and nothing is lost: push after renewal works too
    assert _push(client, _push_body(shop["key"], shop["installation"], [_make_event()])).status_code == 200


def test_an_active_emergency_extension_keeps_an_expired_shop_syncing(app, client, shop):
    """The device skips every commercial restriction while an emergency extension
    is in force (policy_evaluator.py), so the server must too (verifier N1)."""
    set_subscription_state(app, shop["license_id"], "EXPIRED")
    assert _push_ok(client, shop).status_code == 400  # control: refused without the extension
    grant_emergency_extension(app, shop["license_id"], shop["staff"])
    assert _push_ok(client, shop).status_code == 200


def test_the_offline_policy_extension_window_also_keeps_an_expired_shop_syncing(app, client, shop):
    set_subscription_state(app, shop["license_id"], "EXPIRED")
    assign_policy_with_extension(app, shop["license_id"], shop["staff"], until=datetime.now(timezone.utc) + timedelta(hours=6))
    assert _push_ok(client, shop).status_code == 200


def test_an_expired_offline_policy_extension_window_does_not(app, client, shop):
    set_subscription_state(app, shop["license_id"], "EXPIRED")
    assign_policy_with_extension(app, shop["license_id"], shop["staff"], until=datetime.now(timezone.utc) - timedelta(hours=1))
    resp = _push_ok(client, shop)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "SUBSCRIPTION_LAPSED"


@pytest.mark.parametrize("flags", [{"with_policy": False}, {"with_history": False}], ids=["no-policy", "no-history-row"])
def test_past_due_with_no_grace_end_is_treated_as_paid_and_does_not_crash(app, client, shop, flags):
    """PAST_DUE with no commercial policy (or no PAST_DUE history row) has no grace
    end. The device only restricts when it has one, so the relay must read the shop as
    paid -- and must not raise (comparing a time with None) while doing it."""
    set_subscription_state(app, shop["license_id"], "PAST_DUE", past_due_days_ago=30, staff_id=shop["staff"], **flags)
    assert _push_ok(client, shop).status_code == 200
    assert _pull(client, _pull_body(shop["key"], shop["installation"], since=0)).status_code == 200
