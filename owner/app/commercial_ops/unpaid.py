"""One definition of "this subscription is UNPAID" for every Owner-side gate.

Owner decision 2026-10-24 (Aura Growth Plan, docs/licensing/keep-selling-owner-
handover.md on feat/keep-selling): a shop whose subscription lapsed UNPAID keeps
selling, but loses multi-device sync and FEATURE updates; SAFETY fixes still
reach it. The device already decides this locally from the signed assertion
(commercial_runtime/licensing_contracts/policy_evaluator.py): subscription
status EXPIRED, or PAST_DUE with `now > commercial_grace_end`. The Owner-side
gates (sync refusal, update filtering) MUST agree with the device, or a shop is
told one thing by its till and another by the server -- so this helper is the
single place both gates ask, and it takes `commercial_grace_end` from
`resolve_commercial_assertion_fields` (the very function that signs the value
the device compares against) instead of re-deriving the grace arithmetic.

Deliberately NOT unpaid:
  * PAST_DUE inside its grace window (the device treats that as fully active);
  * PAST_DUE with no grace end (no policy / no history row): the device only
    restricts when commercial_grace_end is not None, so we do the same --
    fail towards "paid", never lock a shop on a value we cannot compute;
  * CANCELLED and SUSPENDED: owner decision, they keep today's behaviour
    (locked till, refused sync/updates) -- they are handled by their own,
    older checks and must not be reclassified here.
"""
from __future__ import annotations

from datetime import datetime

from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
from app.models.base import utcnow
from app.models.subscriptions import Subscription


def subscription_is_unpaid(subscription: Subscription | None, *, as_of: datetime | None = None) -> bool:
    if subscription is None:
        return False
    if subscription.status == "EXPIRED":
        return True
    if subscription.status != "PAST_DUE":
        return False
    as_of = as_of or utcnow()
    grace_end_iso = resolve_commercial_assertion_fields(subscription, as_of=as_of)["commercial_grace_end"]
    if grace_end_iso is None:
        return False
    # Same comparison the device makes: strictly after the grace end.
    return as_of > datetime.fromisoformat(grace_end_iso)
