"""Lead discovery spending cap -- a per-Jordan-day and per-Jordan-month
budget of Google Places searches, counted in Postgres (Step 0, 2026-10-25).

Why this exists: every Leads Finder search is one billed Google Places Text
Search (Enterprise SKU, see app/leads/discovery.py's docstring), and the
SALES role holds `leads.discover`. Without a cap, a key pasted into the
env file could be spent without limit by any seller, a stuck double-click or
a script replaying the form -- the first anyone would hear of it is Google's
invoice. With it, the number of billed requests per Jordan day/month can
never pass what config says, across every Gunicorn worker and restart.

How it counts -- three decisions, each load-bearing:

1. ONE conditional upsert per period, not read-then-write:

       INSERT ... VALUES (..., 1)
       ON CONFLICT (bucket_key, window_start) DO UPDATE
          SET count = count + 1 WHERE count < :cap
       RETURNING count

   Postgres takes the row lock and re-checks `count < :cap` against the
   latest committed value, so two concurrent searches cannot both take the
   last slot (a read-then-`row.count += 1` lets both read 99 and both write
   100 -- measured in tests/test_lead_discovery_budget.py's thread test).
   The day row is always locked before the month row, in every
   transaction, so two searches can never deadlock on each other. If the
   month refuses after the day succeeded, the whole transaction is rolled
   back: a refused search never eats into today's budget.

2. Stored in the existing owner_rate_limit_counters table (no schema
   change). Its unique (bucket_key, window_start) constraint is exactly the
   upsert target this needs. But that table is swept every night by
   `flask commercial housekeeping --apply` (deploy/oracle-free/
   run_scheduled_ops.sh), which deletes rows whose window_start is more
   than a day old. A month counter stamped with the month's START would be
   deleted on the 2nd of every month and the budget would quietly start
   over each night. So for these buckets the PERIOD is in bucket_key
   ("lead_discovery:month:2026-10") and window_start holds the moment the
   period ENDS (Jordan midnight starting the next day/month, in UTC): the
   row survives until a day after its period is over, then housekeeping
   removes it. Pinned by test_housekeeping_purge_cannot_reset_the_month_mid_month.

3. Periods are Jordan's calendar (Asia/Amman, UTC+3): the day resets at
   21:00 UTC, the month on the 1st in Amman -- the same business calendar
   as app.operational_reports.periods.jordan_today().

A reserved slot is NOT handed back if Google then fails (timeout, 5xx):
whether Google billed a request that timed out is unknowable from here, so
the budget counts attempts, the conservative reading.

Reaching the cap writes ONE audit row per Jordan day (LEAD_DISCOVERY_CAP_
REACHED), not one per refused click: a marker row in the same table
(INSERT ... ON CONFLICT DO NOTHING) is committed in the same transaction as
the audit row, so concurrent refusals produce exactly one.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from app.extensions import db_session
from app.leads.discovery import LeadDiscoveryError, discovery_caps
from app.models.base import utcnow
from app.operational_reports.periods import OWNER_TIMEZONE, jordan_date

logger = logging.getLogger(__name__)

_DAY_PREFIX = "lead_discovery:day:"
_MONTH_PREFIX = "lead_discovery:month:"
_CAP_AUDIT_PREFIX = "lead_discovery:cap_audit:"

_RESERVE_SQL = text(
    """
    INSERT INTO owner_rate_limit_counters (id, bucket_key, window_start, count)
    VALUES (:id, :bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key, window_start) DO UPDATE
       SET count = owner_rate_limit_counters.count + 1, updated_at = now()
     WHERE owner_rate_limit_counters.count < :cap
    RETURNING count
    """
)

_MARK_ONCE_SQL = text(
    """
    INSERT INTO owner_rate_limit_counters (id, bucket_key, window_start, count)
    VALUES (:id, :bucket_key, :window_start, 1)
    ON CONFLICT (bucket_key, window_start) DO NOTHING
    RETURNING id
    """
)

_USAGE_SQL = text(
    """
    SELECT bucket_key, count FROM owner_rate_limit_counters
     WHERE (bucket_key = :day_key AND window_start = :day_end)
        OR (bucket_key = :month_key AND window_start = :month_end)
    """
)


@dataclass(frozen=True)
class _Periods:
    day: date
    day_key: str
    day_end: datetime      # UTC instant the Jordan day ends (= next day's midnight in Amman)
    month_key: str
    month_end: datetime    # UTC instant the Jordan month ends


@dataclass(frozen=True)
class DiscoveryBudget:
    """What the Leads Finder screen shows."""

    day_used: int
    day_cap: int
    month_used: int
    month_cap: int

    @property
    def day_left(self) -> int:
        return max(0, self.day_cap - self.day_used)

    @property
    def month_left(self) -> int:
        return max(0, self.month_cap - self.month_used)

    @property
    def remaining(self) -> int:
        return min(self.day_left, self.month_left)


def _amman_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=OWNER_TIMEZONE).astimezone(timezone.utc)


def _periods(now: datetime) -> _Periods:
    today = jordan_date(now)
    first_of_next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return _Periods(
        day=today,
        day_key=f"{_DAY_PREFIX}{today.isoformat()}",
        day_end=_amman_midnight_utc(today + timedelta(days=1)),
        month_key=f"{_MONTH_PREFIX}{today:%Y-%m}",
        month_end=_amman_midnight_utc(first_of_next_month),
    )


def _caps_or_refuse(config) -> tuple[int, int]:
    caps = discovery_caps(config)
    if caps is None:
        raise LeadDiscoveryError("NOT_CONFIGURED", detail="lead discovery spending caps are not valid")
    return caps


def current_budget(config, *, now: datetime | None = None) -> DiscoveryBudget:
    daily_cap, monthly_cap = _caps_or_refuse(config)
    periods = _periods(now or utcnow())
    rows = db_session.execute(
        _USAGE_SQL,
        {"day_key": periods.day_key, "day_end": periods.day_end,
         "month_key": periods.month_key, "month_end": periods.month_end},
    ).all()
    counts = {bucket_key: count for bucket_key, count in rows}
    return DiscoveryBudget(
        day_used=counts.get(periods.day_key, 0), day_cap=daily_cap,
        month_used=counts.get(periods.month_key, 0), month_cap=monthly_cap,
    )


def _take_slot(bucket_key: str, window_end: datetime, cap: int) -> bool:
    row = db_session.execute(
        _RESERVE_SQL, {"id": uuid.uuid4(), "bucket_key": bucket_key, "window_start": window_end, "cap": cap}
    ).first()
    return row is not None


def reserve_search(config, *, actor_staff_user_id=None, now: datetime | None = None) -> None:
    """Take one slot from today's AND this month's budget, committed, or
    raise LeadDiscoveryError(DAILY_CAP_REACHED / MONTHLY_CAP_REACHED) having
    taken nothing. Called by the provider immediately before the billed
    request (discovery.build_provider's before_request hook)."""
    daily_cap, monthly_cap = _caps_or_refuse(config)
    periods = _periods(now or utcnow())

    refused = None
    if not _take_slot(periods.day_key, periods.day_end, daily_cap):
        refused = ("DAILY", daily_cap)
    elif not _take_slot(periods.month_key, periods.month_end, monthly_cap):
        refused = ("MONTHLY", monthly_cap)

    if refused is None:
        db_session.commit()
        return

    db_session.rollback()  # undo the day slot if it was the month that refused
    cap_name, limit = refused
    _audit_cap_reached_once(periods, cap_name, limit, actor_staff_user_id)
    raise LeadDiscoveryError(f"{cap_name}_CAP_REACHED", detail=f"{cap_name.lower()} lead discovery cap of {limit} reached")


def _audit_cap_reached_once(periods: _Periods, cap_name: str, limit: int, actor_staff_user_id) -> None:
    """One audit row per Jordan day, however many searches are refused.
    The marker insert and the audit row commit together (audit_record()
    commits the session), so a failed audit write leaves no marker and the
    next refusal tries again. An audit failure never turns the refusal into
    a 500 -- the cap has already done its job -- but it is logged."""
    from app.audit.services import record as audit_record

    try:
        marked = db_session.execute(
            _MARK_ONCE_SQL,
            {"id": uuid.uuid4(), "bucket_key": f"{_CAP_AUDIT_PREFIX}{periods.day.isoformat()}",
             "window_start": periods.day_end},
        ).first()
        if marked is None:
            db_session.commit()
            return
        audit_record(
            actor_staff_user_id=actor_staff_user_id,
            actor_role_snapshot=None,
            action_code="LEAD_DISCOVERY_CAP_REACHED",
            entity_type="lead_discovery_budget",
            entity_public_id=periods.day.isoformat(),
            result="REJECTED",
            reason=f"{cap_name.lower()} cap reached",
            after_state={"cap": cap_name, "limit": limit, "jordan_date": periods.day.isoformat()},
        )
    except Exception:  # noqa: BLE001 -- see docstring: logged, never raised over the refusal
        db_session.rollback()
        logger.exception("lead discovery: could not write the cap-reached audit row (%s cap, %s)", cap_name, periods.day)
