"""AI spending caps (hard rule R4): a request over a cap is refused BEFORE any
AI call is made, so a refused request costs nothing.

Three caps, all counted in Postgres across every Gunicorn worker and restart,
all on Jordan's calendar (UTC+3, same as the Places cap and the business day):

    shop analysis  per LICENCE per Jordan month   OWNER_AI_SHOP_MONTHLY_CAP         (default 120)
    every purpose  per PURPOSE per Jordan day     OWNER_AI_DAILY_CAP                (default 2000)
    staff helper   per STAFF MEMBER per Jordan day OWNER_AI_HELP_DAILY_CAP_PER_STAFF (default 100)

It deliberately REUSES app/leads/discovery_budget.py's machinery rather than
copying it: `_RESERVE_SQL` is the single conditional upsert

    INSERT ... ON CONFLICT (bucket_key, window_start)
      DO UPDATE SET count = count + 1 WHERE count < :cap  RETURNING count

so two simultaneous requests cannot both take the last slot (a read-then-write
lets both read 119 and both write 120), and `_periods` is the one definition
of "Jordan day/month" (read its docstring for why window_start holds the
period END -- the nightly housekeeping purge deletes rows whose window_start is
more than a day old, so a month counter stamped with the month's START would be
wiped every night and the cap would silently start over). A second copy of
either would drift from the first. No new table, no migration: the existing
owner_rate_limit_counters table is the store.

A cap of 0, a negative or a non-number switches THAT FEATURE OFF -- it never
means "unlimited" -- and an unset/blank cap means the default (same rule as the
discovery caps). Reserving across two counters is all-or-nothing: if the second
refuses, the first slot is rolled back, so a refused request never eats budget.

A reserved slot is not handed back if the AI call then fails: whether a
timed-out request was billed is unknowable from here, so the budget counts
attempts (the conservative reading, same as discovery_budget).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from app.ai.providers import AI_BUDGET_EXHAUSTED, AI_NOT_CONFIGURED, AIError
from app.extensions import db_session
from app.leads.discovery import _cap_value as cap_value
from app.leads.discovery_budget import _RESERVE_SQL, _periods
from app.models.base import utcnow

DEFAULT_SHOP_MONTHLY_CAP = 120
DEFAULT_DAILY_CAP = 2000
DEFAULT_HELP_DAILY_CAP_PER_STAFF = 100


def _cap(config, key: str, default: int, what: str) -> int:
    value = cap_value(config.get(key), default)
    if value is None:
        # An invalid cap means "this feature is off", not "no limit".
        raise AIError(AI_NOT_CONFIGURED, detail=f"the {what} cap is not a valid number")
    return value


def _reserve(counters: list[tuple[str, datetime, int, str]]) -> list[int]:
    """Take one slot from each (bucket_key, window_end, cap, name) counter, in
    the order given, all or nothing. Returns each counter's new count."""
    counts: list[int] = []
    for bucket_key, window_end, cap, name in counters:
        row = db_session.execute(
            _RESERVE_SQL,
            {"id": uuid.uuid4(), "bucket_key": bucket_key, "window_start": window_end, "cap": cap},
        ).first()
        if row is None:
            db_session.rollback()  # undo any slot already taken in this attempt
            raise AIError(AI_BUDGET_EXHAUSTED, detail=f"{name} cap of {cap} reached")
        counts.append(int(row[0]))
    db_session.commit()
    return counts


def reserve_shop_analysis(config, license_id, *, now: datetime | None = None) -> dict:
    """One slot from this licence's monthly allowance AND the server-wide daily
    shop-analysis allowance. Returns {"used": n, "cap": m} for the licence's
    month (what the till shows the shop owner)."""
    monthly_cap = _cap(config, "AI_SHOP_MONTHLY_CAP", DEFAULT_SHOP_MONTHLY_CAP, "monthly shop")
    daily_cap = _cap(config, "AI_DAILY_CAP", DEFAULT_DAILY_CAP, "daily")
    periods = _periods(now or utcnow())
    month_count, _day_count = _reserve([
        (f"ai:shop:{license_id}:{periods.month_key[-7:]}", periods.month_end, monthly_cap, "monthly shop"),
        (f"ai:day:shop_analysis:{periods.day.isoformat()}", periods.day_end, daily_cap, "daily"),
    ])
    return {"used": month_count, "cap": monthly_cap}


def reserve_purpose_day(config, purpose: str, *, now: datetime | None = None) -> None:
    """One slot from the server-wide daily allowance for `purpose` (leads rank)."""
    daily_cap = _cap(config, "AI_DAILY_CAP", DEFAULT_DAILY_CAP, "daily")
    periods = _periods(now or utcnow())
    _reserve([(f"ai:day:{purpose}:{periods.day.isoformat()}", periods.day_end, daily_cap, "daily")])


def reserve_staff_help(config, staff_id, *, now: datetime | None = None) -> None:
    """One slot from this staff member's daily allowance AND the server-wide
    daily staff-help allowance."""
    staff_cap = _cap(config, "AI_HELP_DAILY_CAP_PER_STAFF", DEFAULT_HELP_DAILY_CAP_PER_STAFF, "per-staff help")
    daily_cap = _cap(config, "AI_DAILY_CAP", DEFAULT_DAILY_CAP, "daily")
    periods = _periods(now or utcnow())
    _reserve([
        (f"ai:help:{staff_id}:{periods.day.isoformat()}", periods.day_end, staff_cap, "per-staff help"),
        (f"ai:day:staff_help:{periods.day.isoformat()}", periods.day_end, daily_cap, "daily"),
    ])
