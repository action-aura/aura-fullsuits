"""Phase 9.5D Milestone 12 -- refund entitlement consequence policy tests.
Pure decision-function tests, no DB/app fixtures needed."""
from __future__ import annotations

from app.commercial_sales.entitlement_consequence import (
    NO_CONSEQUENCE,
    SUSPEND_ENTITLEMENTS,
    determine_entitlement_consequence,
)


def test_unfulfilled_order_full_refund_no_consequence():
    """Before fulfillment: nothing to suspend/revoke -- there is no
    Subscription/License yet (Milestone 13 hasn't created one)."""
    result = determine_entitlement_consequence(order_was_fulfilled=False, is_full_refund=True)
    assert result == NO_CONSEQUENCE


def test_unfulfilled_order_partial_refund_no_consequence():
    result = determine_entitlement_consequence(order_was_fulfilled=False, is_full_refund=False)
    assert result == NO_CONSEQUENCE


def test_fulfilled_order_full_refund_suspends():
    result = determine_entitlement_consequence(order_was_fulfilled=True, is_full_refund=True)
    assert result == SUSPEND_ENTITLEMENTS


def test_fulfilled_order_partial_refund_no_automatic_consequence():
    """Documented policy, not an oversight: a partial refund after
    fulfillment doesn't automatically suspend anything -- the customer
    received something real."""
    result = determine_entitlement_consequence(order_was_fulfilled=True, is_full_refund=False)
    assert result == NO_CONSEQUENCE
