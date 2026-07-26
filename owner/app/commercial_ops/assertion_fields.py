"""Signed-assertion commercial fields (Phase 8 Part W, Milestone 7).

Resolves the eight new fields the governing spec's Part W proposes adding
to the signed assertion payload (`commercial_policy_version`,
`renewal_status`, `plan_code`, `term_start`, `term_end`, `past_due_since`,
`commercial_grace_end`, `pilot_status`, `emergency_extension_id`), read-only
over data that already exists -- this module creates nothing, decides
nothing, and is never itself the source of truth for any of these values
(`Subscription`/`RenewalRequest`/`CommercialPolicy`/`PilotRecord`/
`EmergencyExtension` remain that). Called from
`licensing_service/assertions.py`'s `build_assertion_payload()`, which is
invoked fresh on every activation AND every check-in (never cached) --
these fields are therefore always current as of the moment the assertion
was built, the same "resolved fresh" principle already documented in
`commercial_ops/renewal_requests.py`'s module docstring.

No new Kotlin/Windows-client typed access is required for these fields --
see `docs/owner/phase8/assertion-schema-and-product-ux.md`: the Android
client never deserializes the assertion payload into a typed model (raw
passthrough to the embedded Python backend), and the Windows desktop app
consumes `commercial_runtime.licensing_contracts` directly in Python,
which reads payload fields by key already. Adding fields here is
additive and forward-compatible for both.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from app.commercial_ops.commercial_policy import resolve_policy_for_subscription
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_ops import EmergencyExtension, PilotRecord, RenewalRequest
from app.models.subscriptions import Subscription, SubscriptionStatusHistory


def _past_due_since(subscription: Subscription) -> datetime | None:
    if subscription.status != "PAST_DUE":
        return None
    row = db_session.execute(
        select(SubscriptionStatusHistory)
        .where(SubscriptionStatusHistory.subscription_id == subscription.id, SubscriptionStatusHistory.to_status == "PAST_DUE")
        .order_by(SubscriptionStatusHistory.created_at.desc())
    ).scalars().first()
    return row.created_at if row is not None else None


def _renewal_status(subscription: Subscription) -> str:
    latest = db_session.execute(
        select(RenewalRequest)
        .where(RenewalRequest.subscription_id == subscription.id)
        .order_by(RenewalRequest.created_at.desc())
    ).scalars().first()
    return latest.status if latest is not None else "NONE"


def _pilot_status(subscription: Subscription) -> str | None:
    pilot = db_session.execute(
        select(PilotRecord).where(PilotRecord.subscription_id == subscription.id)
    ).scalars().first()
    return pilot.status if pilot is not None else None


def _active_emergency_extension_id(subscription: Subscription, *, as_of: datetime) -> str | None:
    row = db_session.execute(
        select(EmergencyExtension).where(
            EmergencyExtension.subscription_id == subscription.id,
            EmergencyExtension.status == "ACTIVE",
            EmergencyExtension.expires_at > as_of,
        )
    ).scalars().first()
    return str(row.id) if row is not None else None


def resolve_commercial_assertion_fields(subscription: Subscription, *, as_of: datetime | None = None) -> dict:
    as_of = as_of or utcnow()
    as_of_date = as_of.date()

    policy = resolve_policy_for_subscription(subscription, as_of=as_of_date)
    past_due_since = _past_due_since(subscription)
    commercial_grace_end = None
    if past_due_since is not None and policy is not None:
        commercial_grace_end = past_due_since + timedelta(days=policy.payment_grace_days)

    return {
        "commercial_policy_version": policy.policy_version if policy is not None else None,
        "renewal_status": _renewal_status(subscription),
        "plan_code": subscription.plan.plan_code if subscription.plan_id else None,
        "term_start": subscription.start_date.isoformat() if subscription.start_date else None,
        "term_end": subscription.end_date.isoformat() if subscription.end_date else None,
        "past_due_since": past_due_since.isoformat() if past_due_since else None,
        "commercial_grace_end": commercial_grace_end.isoformat() if commercial_grace_end else None,
        "pilot_status": _pilot_status(subscription),
        "emergency_extension_id": _active_emergency_extension_id(subscription, as_of=as_of),
    }
