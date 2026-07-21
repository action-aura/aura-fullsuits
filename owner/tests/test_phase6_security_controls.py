"""Part Y REPLAY PROTECTION / RATE LIMITING / IDEMPOTENCY."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


def test_nonce_reuse_rejected(app):
    with app.app_context():
        from app.licensing_service.replay import ReplayError, consume_nonce

        consume_nonce("nonce-abc-1234567890", scope="activation", ttl_seconds=600)
        with pytest.raises(ReplayError, match="NONCE_REUSED"):
            consume_nonce("nonce-abc-1234567890", scope="activation", ttl_seconds=600)


def test_same_nonce_different_scope_is_allowed(app):
    """Nonces are scoped -- an activation nonce and a check-in nonce with the
    same string don't collide (different (nonce, scope) unique key)."""
    with app.app_context():
        from app.licensing_service.replay import consume_nonce

        consume_nonce("shared-nonce-1234567890", scope="activation", ttl_seconds=600)
        consume_nonce("shared-nonce-1234567890", scope="check_in", ttl_seconds=600)  # must not raise


def test_different_nonce_same_payload_allowed(app):
    with app.app_context():
        from app.licensing_service.replay import consume_nonce

        consume_nonce("nonce-one-1234567890ab", scope="activation", ttl_seconds=600)
        consume_nonce("nonce-two-1234567890ab", scope="activation", ttl_seconds=600)  # must not raise


def test_timestamp_too_old_rejected(app):
    from app.licensing_service.replay import ReplayError, validate_timestamp

    now = datetime.now(timezone.utc)
    old = now - timedelta(seconds=1000)
    with pytest.raises(ReplayError, match="TIMESTAMP_OUTSIDE_ALLOWED_WINDOW"):
        validate_timestamp(old, skew_seconds=300, now=now)


def test_timestamp_too_far_future_rejected(app):
    from app.licensing_service.replay import ReplayError, validate_timestamp

    now = datetime.now(timezone.utc)
    future = now + timedelta(seconds=1000)
    with pytest.raises(ReplayError, match="TIMESTAMP_OUTSIDE_ALLOWED_WINDOW"):
        validate_timestamp(future, skew_seconds=300, now=now)


def test_timestamp_within_window_accepted(app):
    from app.licensing_service.replay import validate_timestamp

    now = datetime.now(timezone.utc)
    close = now - timedelta(seconds=100)
    validate_timestamp(close, skew_seconds=300, now=now)  # must not raise


def test_replay_protection_available_check(app):
    with app.app_context():
        from app.licensing_service.replay import check_replay_protection_available

        assert check_replay_protection_available() is True


def test_rate_limit_enforced_after_max_requests(app):
    with app.app_context():
        from app.licensing_service import ratelimit

        ratelimit.POLICIES["test_policy"] = ratelimit.RateLimitPolicy("test_policy", max_requests=3, window_seconds=60)
        for _ in range(3):
            ratelimit.check_and_increment("test_policy", "bucket-a")
        with pytest.raises(ratelimit.RateLimitExceeded):
            ratelimit.check_and_increment("test_policy", "bucket-a")


def test_rate_limit_buckets_are_independent(app):
    with app.app_context():
        from app.licensing_service import ratelimit

        ratelimit.POLICIES["test_policy2"] = ratelimit.RateLimitPolicy("test_policy2", max_requests=1, window_seconds=60)
        ratelimit.check_and_increment("test_policy2", "bucket-x")
        ratelimit.check_and_increment("test_policy2", "bucket-y")  # different bucket, must not raise


def test_rate_limit_never_stores_plaintext_secret(app):
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service import ratelimit
        from app.models.licensing_service import RateLimitCounter
        from sqlalchemy import select

        secret = "AURA-CLN-1-SECRET-KEY-VALUE-HERE"
        bucket = ratelimit.safe_bucket_component(secret)
        assert secret not in bucket
        ratelimit.POLICIES["test_policy3"] = ratelimit.RateLimitPolicy("test_policy3", max_requests=5, window_seconds=60)
        ratelimit.check_and_increment("test_policy3", bucket)
        rows = db_session.execute(select(RateLimitCounter)).scalars().all()
        for row in rows:
            assert secret not in row.bucket_key


def test_idempotency_replay_returns_existing_no_conflict(app):
    with app.app_context():
        from app.licensing_service import idempotency

        idempotency.record_idempotency("idem-key-1", "ACTIVATION", "fingerprint-a", "install-1", "SUCCESS", '{"ok":true}')
        existing = idempotency.check_idempotency("idem-key-1", "ACTIVATION", "fingerprint-a")
        assert existing is not None
        assert existing.cached_response_json == '{"ok":true}'


def test_idempotency_conflict_on_different_fingerprint(app):
    with app.app_context():
        from app.licensing_service import idempotency

        idempotency.record_idempotency("idem-key-2", "ACTIVATION", "fingerprint-a", "install-1", "SUCCESS", "{}")
        with pytest.raises(idempotency.IdempotencyConflictError):
            idempotency.check_idempotency("idem-key-2", "ACTIVATION", "fingerprint-different")


def test_idempotency_no_match_returns_none(app):
    with app.app_context():
        from app.licensing_service import idempotency

        assert idempotency.check_idempotency("never-seen-key", "ACTIVATION", "fp") is None
