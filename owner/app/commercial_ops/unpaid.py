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
from app.commercial_ops.emergency_extensions import get_active_extension
from app.licensing_service.offline_policy import get_policy_for_license
from app.models.base import utcnow
from app.models.subscriptions import Subscription


def has_emergency_extension(subscription: Subscription, license_row, *, as_of: datetime) -> bool:
    """True while the device would skip every commercial restriction because of an
    emergency extension -- the SAME two sources it is signed from:
      * an ACTIVE, unexpired EmergencyExtension row for the subscription
        (offline_policy.serialize_policy_for_subscription overrides the signed
        policy with it);
      * the licence's own OfflinePolicy carrying emergency_extension_allowed with
        a future emergency_extension_until.
    policy_evaluator.py wraps its EXPIRED / PAST_DUE checks in
    `if not emergency_extension_effective`, so a shop on an extension is told
    "fine" by its till; the server must say the same (verifier finding N1)."""
    if get_active_extension(subscription.id, now=as_of) is not None:
        return True
    if license_row is None:
        return False
    policy = get_policy_for_license(license_row)
    return bool(
        policy.emergency_extension_allowed
        and policy.emergency_extension_until is not None
        and as_of < policy.emergency_extension_until
    )


def subscription_is_unpaid(subscription: Subscription | None, *, license_row=None, as_of: datetime | None = None) -> bool:
    """`license_row` is the licence the caller already holds; it is only needed so
    the OfflinePolicy emergency extension can be honoured (the Subscription-level
    extension row needs no licence)."""
    if subscription is None:
        return False
    as_of = as_of or utcnow()
    if subscription.status == "EXPIRED":
        unpaid = True
    elif subscription.status == "PAST_DUE":
        grace_end_iso = resolve_commercial_assertion_fields(subscription, as_of=as_of)["commercial_grace_end"]
        # Same comparison the device makes: strictly after the grace end; no grace
        # end at all means the device does not restrict, so neither do we.
        unpaid = grace_end_iso is not None and as_of > datetime.fromisoformat(grace_end_iso)
    else:
        return False
    # Only asked when otherwise unpaid, so paid shops cost no extra queries.
    return unpaid and not has_emergency_extension(subscription, license_row, as_of=as_of)
