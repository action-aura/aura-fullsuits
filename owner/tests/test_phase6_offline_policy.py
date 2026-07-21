"""Part P OFFLINE POLICY."""
from __future__ import annotations

from tests.conftest import make_license, make_staff


def test_default_policy_seeded_not_unlimited(app, seeded):
    with app.app_context():
        from app.licensing_service.offline_policy import seed_default_offline_policy

        policy = seed_default_offline_policy()
        assert policy.offline_grace_seconds > 0
        assert policy.offline_grace_seconds < 365 * 24 * 3600  # sane, not "unlimited"
        assert policy.hard_expiry_behavior in ("WARN_ONLY", "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA")


def test_hard_expiry_behavior_enum_has_no_destructive_option():
    from app.licensing_service.offline_policy import HARD_EXPIRY_BEHAVIORS

    for behavior in HARD_EXPIRY_BEHAVIORS:
        assert "DELETE" not in behavior
        assert "CORRUPT" not in behavior
        assert "LOCK" not in behavior
        assert "WITHHOLD" not in behavior


def test_license_without_assignment_falls_back_to_default(app, seeded):
    actor_id = make_staff(app, "op1@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.offline_policy import get_policy_for_license
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        policy = get_policy_for_license(lic)
        assert policy.policy_code == "standard-v1"


def test_explicit_assignment_overrides_default(app, seeded):
    actor_id = make_staff(app, "op2@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.offline_policy import assign_policy, get_policy_for_license, seed_default_offline_policy
        from app.models.licensing import License
        from app.models.licensing_service import OfflinePolicy

        seed_default_offline_policy()
        custom = OfflinePolicy(
            policy_code="custom-strict-v1", policy_version=1, check_in_interval_seconds=3600, retry_interval_seconds=600,
            offline_grace_seconds=86400, warning_start_seconds=43200, hard_expiry_behavior="WARN_ONLY",
            assertion_refresh_threshold_seconds=3600,
        )
        db_session.add(custom)
        db_session.commit()

        lic = db_session.get(License, license_id)
        assign_policy(lic, "custom-strict-v1", actor_id)
        policy = get_policy_for_license(lic)
        assert policy.policy_code == "custom-strict-v1"
