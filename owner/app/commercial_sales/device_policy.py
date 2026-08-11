"""Phase 9.5A Milestone 22/3 -- DevicePolicyService.

resolve_device_policy() mirrors resolve_activation_mode()'s own
product-specific-then-global-default lookup pattern. Reports only, never
mutates -- and is NOT wired into the live licensing_service/activation.py
enforcement path this phase (see multi-device-policy-design.md's explicit
enforcement-wiring boundary). License.device_limit /
resolve_effective_device_limit() remain the only real enforcement authority.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select

from app.extensions import db_session
from app.models.activation_governance import (
    DevicePolicyPlatformRule,
    DevicePolicyProfile,
    SubscriptionDevicePolicyOverride,
)
from app.models.base import utcnow
from app.models.subscriptions import Subscription


@dataclass
class EffectiveDevicePolicy:
    max_total_devices: int | None
    platform_rule_map: dict[str, int] = field(default_factory=dict)
    approval_required_after_device_number: int | None = None


def resolve_device_policy(subscription: Subscription, as_of: datetime | None = None) -> EffectiveDevicePolicy:
    as_of = as_of or utcnow()
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of

    profile = db_session.execute(
        select(DevicePolicyProfile)
        .where(
            DevicePolicyProfile.plan_id == subscription.plan_id,
            DevicePolicyProfile.effective_date <= as_of_date,
        )
        .where((DevicePolicyProfile.retired_date.is_(None)) | (DevicePolicyProfile.retired_date > as_of_date))
        .order_by(DevicePolicyProfile.effective_date.desc())
    ).scalars().first()

    if profile is None:
        profile = db_session.execute(
            select(DevicePolicyProfile)
            .where(
                DevicePolicyProfile.plan_id.is_(None),
                DevicePolicyProfile.effective_date <= as_of_date,
            )
            .where((DevicePolicyProfile.retired_date.is_(None)) | (DevicePolicyProfile.retired_date > as_of_date))
            .order_by(DevicePolicyProfile.effective_date.desc())
        ).scalars().first()

    if profile is None:
        # No row at all = preserve prior behavior, matching ActivationPolicy's
        # own "no row = AUTOMATIC" precedent.
        policy = EffectiveDevicePolicy(max_total_devices=None, platform_rule_map={}, approval_required_after_device_number=None)
    else:
        rules = db_session.execute(
            select(DevicePolicyPlatformRule).where(DevicePolicyPlatformRule.device_policy_profile_id == profile.id)
        ).scalars().all()
        policy = EffectiveDevicePolicy(
            max_total_devices=profile.max_total_devices,
            platform_rule_map={r.platform_category: r.max_devices for r in rules},
            approval_required_after_device_number=profile.approval_required_after_device_number,
        )

    override = db_session.execute(
        select(SubscriptionDevicePolicyOverride)
        .where(
            SubscriptionDevicePolicyOverride.subscription_id == subscription.id,
            SubscriptionDevicePolicyOverride.effective_from <= as_of,
        )
        .where(
            (SubscriptionDevicePolicyOverride.effective_until.is_(None))
            | (SubscriptionDevicePolicyOverride.effective_until > as_of)
        )
        .order_by(SubscriptionDevicePolicyOverride.effective_from.desc())
    ).scalars().first()

    if override is not None:
        if override.max_total_devices is not None:
            policy.max_total_devices = override.max_total_devices
        if override.approval_required_after_device_number is not None:
            policy.approval_required_after_device_number = override.approval_required_after_device_number

    return policy
