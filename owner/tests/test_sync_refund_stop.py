"""The sync relay applies a refund's DUE licence stop before it reads the subscription.

A full refund schedules "the licence keeps working 2 days, then stops"
(licensing/scheduled_stops.py). Check-in, activation and the update gate apply a
stop that has come due BEFORE reading statuses; the sync relay's _authenticate
did not, so for a shop past its 2 days whose subscription row still said ACTIVE
(nothing had run the stop yet) it kept syncing while its till was already locked
(verifier, 2026-10-30, LOW).

Paired with its allow side: a stop that is NOT yet due leaves the shop syncing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tests.conftest import build_activation_body, make_device_keypair
from tests.test_step0_ladder import _refund, _rows, _sell
from tests.test_sync_routes import _activate, _make_event, _push, _push_body
from tests.test_yearly_activation import _owner_world, owner_prices  # noqa: F401  (fixture)


@pytest.fixture()
def refunded_shop(app, client, seeded, signing_key, owner_prices):
    """A sold, activated Package 1 shop whose sale was fully refunded: one stop
    scheduled 2 days after the refund, subscription and licence still live."""
    w = _owner_world(app)
    sold = _sell(app, w, "p1")
    rows = _rows(app, sold["order_id"])
    key = make_device_keypair()
    import uuid

    resp = _activate(client, build_activation_body(
        key, full_key=sold["full_key"], installation_id=str(uuid.uuid4()), product_code="AURA_RETAIL"))
    assert resp.status_code == 200, resp.get_json()
    _refund(app, w, sold["order_id"], "250.00")
    return {"key": key, "installation": resp.get_json()["installation_id"], "sub": rows["subscription_id"],
            "licence": rows["license_id"]}


def _stop_moment(app, licence, when):
    with app.app_context():
        from app.extensions import db_session

        n = db_session.execute(text("UPDATE owner_license_scheduled_stops SET effective_at = :t WHERE license_id = :l "
                                    "AND to_status IS NOT NULL"), {"t": when, "l": licence}).rowcount
        db_session.commit()
        assert n == 1, "the refund should have scheduled exactly one licence stop"


def _sub_status(app, sub):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        return db_session.get(Subscription, sub).status


def test_a_refund_stop_that_is_not_yet_due_leaves_the_shop_syncing(app, client, refunded_shop):
    s = refunded_shop
    assert _sub_status(app, s["sub"]) == "ACTIVE"
    resp = _push(client, _push_body(s["key"], s["installation"], [_make_event()]))
    assert resp.status_code == 200, resp.get_json()


def test_a_due_refund_stop_refuses_sync_even_though_the_subscription_row_still_says_active(app, client, refunded_shop):
    s = refunded_shop
    _stop_moment(app, s["licence"], datetime.now(timezone.utc) - timedelta(hours=1))
    assert _sub_status(app, s["sub"]) == "ACTIVE"          # nothing has applied the stop yet: that is the point
    resp = _push(client, _push_body(s["key"], s["installation"], [_make_event()]))
    assert resp.status_code == 400 and resp.get_json() == {"reason_code": "SUBSCRIPTION_SUSPENDED"}
    assert _sub_status(app, s["sub"]) == "SUSPENDED"       # applied once, by the same function check-in uses
