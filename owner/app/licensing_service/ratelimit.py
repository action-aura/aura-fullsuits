"""Distributed rate limiting (Part M, ADR-6.3: PostgreSQL-backed counters,
shared across every Owner worker process since all workers share one
database -- satisfying 'distributed' without a Redis dependency).

bucket_key is always a safe, non-secret derived identifier -- never a
plaintext license key (Part M's explicit prohibition). Callers pass a
route-category-specific policy so activation, check-in, and signing-key
discovery each get independent budgets.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.extensions import db_session
from app.models.licensing_service import RateLimitCounter


@dataclass(frozen=True)
class RateLimitPolicy:
    name: str
    max_requests: int
    window_seconds: int


# Configuration as data, not scattered constants (Part M's explicit instruction).
POLICIES: dict[str, RateLimitPolicy] = {
    "activation": RateLimitPolicy("activation", max_requests=10, window_seconds=60),
    "activation_invalid_license": RateLimitPolicy("activation_invalid_license", max_requests=5, window_seconds=300),
    "activation_invalid_signature": RateLimitPolicy("activation_invalid_signature", max_requests=5, window_seconds=300),
    "check_in": RateLimitPolicy("check_in", max_requests=30, window_seconds=60),
    "signing_keys": RateLimitPolicy("signing_keys", max_requests=60, window_seconds=60),
    "service_info": RateLimitPolicy("service_info", max_requests=60, window_seconds=60),
    # Phase 9R M11: bounded download-authorization attempts per client, and
    # a separate, tighter bucket for the fetch step itself (fewer requests
    # legitimately needed -- one fetch per authorization, so a burst here
    # is a stronger signal of abuse than a burst of authorization requests).
    "release_download_authorize": RateLimitPolicy("release_download_authorize", max_requests=20, window_seconds=60),
    "release_download_fetch": RateLimitPolicy("release_download_fetch", max_requests=10, window_seconds=60),
    # Update-channel wave: discovery is a daily poll per install, but many
    # installs can share one shop/office IP (the bucket key is the client
    # address), so the budget is sized like check-in, not like a download.
    "release_latest": RateLimitPolicy("release_latest", max_requests=30, window_seconds=60),
    # Crash-reports wave: a till sends at most one batch per ~30 minutes, but
    # many installs can share one shop/office IP (the bucket key is the client
    # address) and a fleet that all hit the same bug after a bad release will
    # all retry together -- sized like check-in, not like a download.
    "crash_reports": RateLimitPolicy("crash_reports", max_requests=30, window_seconds=60),
    # Real-email wave: the mail relay's per-ADDRESS request budget (a flood
    # guard in front of authentication). The real per-installation caps
    # (50/day, 10 identity/day, same recipient 1 per 2 min) are enforced in
    # app/mail/relay.py after the device is authenticated.
    "mail_relay": RateLimitPolicy("mail_relay", max_requests=30, window_seconds=60),
    # Website-intake wave: the actionaura.me Pages Function forwards each demo
    # request once. Cloudflare egress IPs are shared, so this is a flood guard,
    # not a per-visitor limit -- the website already limits each visitor to 5
    # requests per 10 minutes before forwarding anything.
    "website_intake": RateLimitPolicy("website_intake", max_requests=30, window_seconds=60),
    # Step 2 CA1: customer website sign-in. Each attempt takes a slot from all
    # three BEFORE the password is checked (so parallel guesses cannot all slip
    # through -- the take is one atomic statement). The keys are hashes (see
    # safe_bucket_component): (email, IP) 5 per 15 min, email alone 50 per hour
    # (a botnet spread over many IPs), IP alone 30 per 15 min (one source
    # walking a list of emails). These are CUSTOMER buckets: they share nothing
    # with the staff login lockout, so a flood on the website cannot lock staff.
    "customer_signin_pair": RateLimitPolicy("customer_signin_pair", max_requests=5, window_seconds=900),
    "customer_signin_email": RateLimitPolicy("customer_signin_email", max_requests=50, window_seconds=3600),
    "customer_signin_ip": RateLimitPolicy("customer_signin_ip", max_requests=30, window_seconds=900),
    # Forgot-password: 3 per address per hour (the website always answers the
    # same, so this is silent), plus a per-IP flood guard.
    "customer_forgot_address": RateLimitPolicy("customer_forgot_address", max_requests=3, window_seconds=3600),
    "customer_forgot_ip": RateLimitPolicy("customer_forgot_ip", max_requests=20, window_seconds=3600),
    # Set-password / email-confirm links: tokens are 256-bit so guessing is not
    # the worry; this bounds work and noise per source.
    "customer_token_ip": RateLimitPolicy("customer_token_ip", max_requests=20, window_seconds=900),
    # New customer flow (S5): the apps' account sign-in. Besides the website's
    # shared email / IP lockout (service.authenticate), each DEVICE KEY gets its own
    # budget so one machine cannot walk a list of emails even from many addresses:
    # sign-ins 10 per 15 minutes; ticket actions (password change, contract
    # acceptance) 20 per 15 minutes. The per-address flood guards below sit in
    # front of authentication, sized like activation.
    "account_signin_device": RateLimitPolicy("account_signin_device", max_requests=10, window_seconds=900),
    "account_ticket_ops": RateLimitPolicy("account_ticket_ops", max_requests=20, window_seconds=900),
    "account_signin_flood": RateLimitPolicy("account_signin_flood", max_requests=30, window_seconds=60),
    # Website downloads (S4): authorizations per customer account, and a flood
    # guard on the streaming fetch (many parallel tabs share a phone carrier IP).
    "website_download_authorize": RateLimitPolicy("website_download_authorize", max_requests=20, window_seconds=3600),
    "website_download_fetch": RateLimitPolicy("website_download_fetch", max_requests=60, window_seconds=60),
}


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds
        super().__init__("RATE_LIMITED")


def safe_bucket_component(value: str) -> str:
    """Never places a raw secret (e.g. a plaintext license key) into a rate
    limit bucket key -- always a one-way hash prefix."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch_seconds = int(now.timestamp())
    bucket_start = epoch_seconds - (epoch_seconds % window_seconds)
    return datetime.fromtimestamp(bucket_start, tz=timezone.utc)


# ONE conditional upsert, not read-then-write. The old code SELECTed the row,
# compared row.count in Python, then wrote `row.count += 1` -- which the ORM
# sends as an absolute `SET count = <value it read + 1>`. Requests released
# together all read the same count, all passed, and all wrote the same value:
# measured 12 of 12 concurrent requests accepted against a limit of 5, and
# 5 to 12 of 12 accepted with only the last slot (4 of 5 used) left
# (tests/test_licensing_ratelimit_atomic.py). These buckets are the
# brute-force guard on licence activation / check-in, so the limit has to
# hold exactly when someone is hammering it in parallel.
#
# Here Postgres takes the row lock on conflict and re-evaluates
# `count < :limit` against the latest committed value, so each request
# either takes a slot or gets no row back -- no two can take the same one.
# A refused request writes nothing (the stored count stops at the limit).
# Same pattern as the Leads Finder spending cap (feat/step0-leads-cap,
# app/leads/discovery_budget.py), on the same table and the same unique
# (bucket_key, window_start) constraint. Implemented here rather than shared:
# that branch is unmerged; one small helper both can call is the natural
# follow-up once it lands.
#
# Nightly housekeeping (`flask commercial housekeeping --apply`) deletes
# counters whose window_start is more than a day old. window_start here is
# the window's START and every policy's window is minutes long, so a row is
# only purged long after its window ended -- pinned by
# test_housekeeping_purge_cannot_reset_a_live_window, which also refuses any
# policy whose window is a day or longer.
_TAKE_SLOT_SQL = text(
    """
    INSERT INTO owner_rate_limit_counters (id, bucket_key, window_start, count)
    VALUES (:id, :bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key, window_start) DO UPDATE
       SET count = owner_rate_limit_counters.count + 1, updated_at = now()
     WHERE owner_rate_limit_counters.count < :limit
    RETURNING count
    """
)


def check_and_increment(policy_name: str, bucket_key: str, *, now: datetime | None = None) -> None:
    """Raises RateLimitExceeded if the bucket is over budget; otherwise
    takes one slot, committed. Fixed-window counter; race-free because the
    check and the increment are one statement (see _TAKE_SLOT_SQL)."""
    policy = POLICIES[policy_name]
    now = now or datetime.now(timezone.utc)
    window_start = _window_start(now, policy.window_seconds)
    full_key = f"{policy_name}:{bucket_key}"

    taken = db_session.execute(
        _TAKE_SLOT_SQL,
        {"id": uuid.uuid4(), "bucket_key": full_key, "window_start": window_start, "limit": policy.max_requests},
    ).first()
    db_session.commit()

    if taken is None:
        retry_after = int((window_start + timedelta(seconds=policy.window_seconds) - now).total_seconds())
        raise RateLimitExceeded(max(retry_after, 1))


def purge_old_counters(*, older_than: datetime) -> int:
    result = db_session.query(RateLimitCounter).filter(RateLimitCounter.window_start < older_than).delete()
    db_session.commit()
    return result
