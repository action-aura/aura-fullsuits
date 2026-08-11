"""Phase 9.5E -- canonical period boundaries for scheduled reports, all in
Asia/Amman (matching DailyActivitySnapshot's own existing timezone default,
Phase 9.5A). stdlib zoneinfo only -- no new dependency."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

OWNER_TIMEZONE = ZoneInfo("Asia/Amman")


def previous_completed_business_day(now_utc: datetime) -> date:
    local_now = now_utc.astimezone(OWNER_TIMEZONE)
    return (local_now - timedelta(days=1)).date()


def previous_completed_week(now_utc: datetime) -> tuple[date, date]:
    """Monday-Sunday, the most recently fully completed one."""
    local_now = now_utc.astimezone(OWNER_TIMEZONE)
    this_monday = local_now.date() - timedelta(days=local_now.weekday())
    prev_monday = this_monday - timedelta(days=7)
    prev_sunday = this_monday - timedelta(days=1)
    return prev_monday, prev_sunday


def previous_completed_month(now_utc: datetime) -> tuple[date, date]:
    local_now = now_utc.astimezone(OWNER_TIMEZONE)
    first_of_this_month = local_now.date().replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return first_of_prev_month, last_of_prev_month
