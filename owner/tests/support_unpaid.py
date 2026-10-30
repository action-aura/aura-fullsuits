"""Shared helpers for the keep-selling gate tests (sync refusal + update gate).

Puts a licence's subscription into an exact commercial state by writing the
rows the real expiry scan would write, so the tests exercise the same
`commercial_grace_end` computation the device is signed with -- not a stub.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

GRACE_DAYS = 10


def set_subscription_state(app, license_id, status, *, past_due_days_ago=None, staff_id=None):
    """status in ACTIVE/EXPIRED/CANCELLED/SUSPENDED/PAST_DUE. For PAST_DUE,
    `past_due_days_ago` is how long ago the PAST_DUE history row was written;
    with GRACE_DAYS=10, 11 is past grace and 2 is inside it."""
    with app.app_context():
        from app.commercial_ops.commercial_policy import create_commercial_policy, resolve_policy_for_subscription
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import SubscriptionStatusHistory

        lic = db_session.get(License, license_id)
        sub = lic.subscription
        if status == "PAST_DUE" and resolve_policy_for_subscription(sub) is None:
            create_commercial_policy(
                policy_code=f"ks-policy-{license_id}", warning_offsets_days=[7, 0], notify_role_codes=["SUPPORT"],
                effective_date=date(2026, 1, 1), actor_staff_user_id=staff_id, payment_grace_days=GRACE_DAYS,
            )
        sub.status = status
        if status == "PAST_DUE":
            db_session.add(SubscriptionStatusHistory(
                subscription_id=sub.id, from_status="ACTIVE", to_status="PAST_DUE", reason="test setup",
                created_at=datetime.now(timezone.utc) - timedelta(days=past_due_days_ago),
            ))
        db_session.commit()


def grant_emergency_extension(app, license_id, staff_id, *, hours=24):
    """An ACTIVE EmergencyExtension on the licence's subscription -- the same row
    the device is signed an overridden policy from."""
    with app.app_context():
        from app.commercial_ops.emergency_extensions import create_emergency_extension
        from app.extensions import db_session
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        create_emergency_extension(
            subscription=lic.subscription, license=lic, reason="keep-selling test", duration_hours=hours,
            actor_staff_user_id=staff_id,
        )


def assign_policy_with_extension(app, license_id, staff_id, *, until):
    """A licence-level OfflinePolicy that itself carries an emergency extension
    window ending `until` (the second source the device honours)."""
    with app.app_context():
        import uuid

        from app.extensions import db_session
        from app.licensing_service.offline_policy import assign_policy
        from app.models.licensing import License
        from app.models.licensing_service import OfflinePolicy

        code = f"KS-EXT-{uuid.uuid4().hex[:8]}"
        db_session.add(OfflinePolicy(
            policy_code=code, policy_version=1, check_in_interval_seconds=86400, retry_interval_seconds=3600,
            offline_grace_seconds=86400, warning_start_seconds=3600, hard_expiry_behavior="WARN_ONLY",
            clock_rollback_tolerance_seconds=300, assertion_refresh_threshold_seconds=86400,
            emergency_extension_allowed=True, emergency_extension_until=until,
        ))
        db_session.commit()
        assign_policy(db_session.get(License, license_id), code, staff_id)
