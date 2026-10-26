"""Step 1 (2026-10-25) -- the sales leaderboard.

The owner's board (Aura Growth Plan, Step 1): this Jordan calendar month and
last month; per seller the sales counted on the ladder, the ladder total,
held vs payable, and renewals; ranked by counted sales, then total.

EVERY number comes from the ladder's own summary
(ladder.py::ladder_month_summary -- the same function the seller's own
"Monthly commission ladder" card and the managers' "This month, every
seller" table read). Nothing here adds up a ledger row itself, so the board
can never disagree with the commission screen: a refund inside the hold, a
Package 2 that counts at its third payment, a take-back -- all of it is
already in that summary.

WHO SEES WHAT (decided 2026-10-25):
  - commissions.view_all (Sales Manager, Finance, Super Admin): every
    seller's money, exactly as the commission screen already shows them;
  - commissions.view_own (a seller): every seller's NAME and COUNTS (sales
    counted, renewals) -- the competition is the point of a board -- but
    money only in their OWN row, which /commissions already shows them.
    Another seller's earnings are that person's pay; a seller has never been
    able to see them (own-only commission scoping) and a board must not
    become the way around that. The money of other rows is REMOVED here, in
    the service, not hidden in the template: what is never sent to the page
    cannot leak through view-source, a data attribute or a future template.
  - nobody sees a customer: the summary carries none and neither does this.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.commissions.ladder import is_ladder_rule, ladder_month_of, ladder_month_summary
from app.commissions.services import resolve_active_rule
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commissions import CommissionLadderSale, CommissionLedgerEntry
from app.models.employees import EmployeeProfile
from app.operational_reports.periods import jordan_today

# The summary's money fields: present on a row only for a viewer who may see them.
MONEY_KEYS = ("total", "held", "payable", "renewals_total", "renewals_held")
_COUNT_KEYS = ("count", "yearly_count", "monthly_count", "renewals", "refunded_after_hold")


def board_months(now: datetime | None = None) -> tuple[date, date]:
    """(this month, last month) -- first days, Jordan calendar months. 00:30
    on 1 November in Amman is November's board even though it is still 31
    October in UTC (ladder_month_of: the ladder's own month rule)."""
    this_month = ladder_month_of(now or utcnow())
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    return this_month, last_month


def _month_end(month: date) -> date:
    return ((month.replace(day=28) + timedelta(days=4)).replace(day=1)) - timedelta(days=1)


def _sellers_on_the_board(month: date) -> set[uuid.UUID]:
    """Everyone with ladder activity that month -- the same two facts
    ladder_month_overview reads (a ladder sale of that month, counted or not;
    a ladder or renewal row of that month) -- plus every ACTIVE employee on
    the ladder rule that month, so a seller with no sale yet is on the board
    at 0 rather than missing from it."""
    ids = set(db_session.execute(
        select(CommissionLadderSale.employee_profile_id).where(CommissionLadderSale.ladder_month == month)
    ).scalars().all())
    ids |= set(db_session.execute(
        select(CommissionLedgerEntry.employee_profile_id).where(CommissionLedgerEntry.ladder_month == month)
    ).scalars().all())
    as_of = min(jordan_today(), _month_end(month))
    active = db_session.execute(
        select(EmployeeProfile.id).where(EmployeeProfile.employment_status == "ACTIVE",
                                         EmployeeProfile.archived_at.is_(None))
    ).scalars().all()
    ids |= {profile_id for profile_id in active if is_ladder_rule(resolve_active_rule(profile_id, as_of))}
    return ids


def rank(rows: list[dict]) -> list[dict]:
    """Sorted by counted sales, then ladder total (both high first), then
    name; equal (count, total) share a rank and the next rank skips
    (1, 2, 3, 3, 5) -- the usual sports-table rule, so two sellers with the
    same month are shown as level, not as one ahead of the other."""
    ordered = sorted(rows, key=lambda r: (-r["count"], -r["total"], r["employee_name"], str(r["employee_profile_id"])))
    out, previous, previous_rank = [], None, 0
    for position, row in enumerate(ordered, start=1):
        key = (row["count"], row["total"])
        current_rank = previous_rank if key == previous else position
        out.append({**row, "rank": current_rank})
        previous, previous_rank = key, current_rank
    return out


def leaderboard(month: date, *, viewer_profile_id: uuid.UUID | None, show_all_money: bool,
                now: datetime | None = None) -> list[dict]:
    """The board for one month, ranked. Each row: rank, employee_profile_id,
    employee_name, is_viewer, the counts, and -- only when show_all_money or
    it is the viewer's own row -- the money (MONEY_KEYS) and currency.
    Ranking uses the full numbers before any money is removed, so a seller
    sees the same order a manager sees."""
    now = now or utcnow()
    rows = []
    for profile_id in _sellers_on_the_board(month):
        profile = db_session.get(EmployeeProfile, profile_id)
        summary = ladder_month_summary(profile_id, month, now=now)
        rows.append({**summary, "employee_profile_id": profile_id,
                     "employee_name": profile.full_name if profile is not None else ""})
    out = []
    for row in rank(rows):
        is_viewer = viewer_profile_id is not None and row["employee_profile_id"] == viewer_profile_id
        visible = {"rank": row["rank"], "employee_profile_id": row["employee_profile_id"],
                   "employee_name": row["employee_name"], "is_viewer": is_viewer, "month": row["month"]}
        visible.update({key: row[key] for key in _COUNT_KEYS})
        if show_all_money or is_viewer:
            visible.update({key: row[key] for key in MONEY_KEYS})
            visible["currency"] = row["currency"]
        out.append(visible)
    return out

