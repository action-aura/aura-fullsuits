"""Phase 9.5E -- canonical period boundaries for scheduled reports, all in
Asia/Amman (matching DailyActivitySnapshot's own existing timezone default,
Phase 9.5A). stdlib zoneinfo only -- no new dependency."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.base import utcnow

OWNER_TIMEZONE = ZoneInfo("Asia/Amman")


def jordan_date(moment: datetime) -> date:
    """The calendar date a moment falls on in Jordan. A timestamp read back
    from Postgres carries the SESSION's zone (Asia/Gaza on the dev and test
    servers, UTC+2 in winter), so `.date()` on it is neither Jordan's date
    nor UTC's -- always go through here."""
    return moment.astimezone(OWNER_TIMEZONE).date()


def jordan_today() -> date:
    """Today's date in Jordan -- THE business date (Step 0 fix round,
    2026-10-25). Every decision about "today" (a payment date that may not
    be in the future, a document number's year, a due date, a cancellation
    or employment end date, a quote's validity, a licence's term, what is
    overdue or expiring) reads it here. Before, each site read its own
    clock: utcnow().date() (UTC) or date.today() (the server's calendar).
    Jordan is UTC+3 all year, so between 21:00 and 24:00 UTC it is already
    tomorrow there -- measured: at 00:30 in Amman (21:30 UTC) the one-screen
    sale refused money that arrived "today" as "in the future", and a
    document numbered at 00:30 on 1 January took the old year."""
    return jordan_date(utcnow())


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
