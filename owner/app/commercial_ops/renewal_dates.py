"""Renewal date-calculation rules (Phase 8 Part D).

Pure functions only -- no database session, no I/O, no side effects. Every
function takes explicit input dates and returns an explicit
``(new_start, new_end, date_rule)`` tuple; nothing about *which* rule
governs is ever inferred silently for a late renewal, per Part D's own
rule ("do not infer silently... record the selected rule").

Uses plain `datetime.date` throughout, not `datetime.datetime` -- these are
business/billing dates (calendar days), not instants, so there is no
timezone ambiguity to resolve: "renew starting 2026-08-01" means the same
calendar day everywhere, unlike a timestamp. Anywhere a caller has a
`datetime` (e.g. an approval timestamp), it must convert to the customer's
or Owner's authoritative business date before calling into this module --
that conversion is a policy decision this module deliberately does not
make.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from enum import Enum


class RenewalDateRule(str, Enum):
    EARLY_RENEWAL_FROM_CURRENT_END = "EARLY_RENEWAL_FROM_CURRENT_END"
    LATE_RENEWAL_FROM_PREVIOUS_END = "LATE_RENEWAL_FROM_PREVIOUS_END"
    LATE_RENEWAL_FROM_APPROVAL_DATE = "LATE_RENEWAL_FROM_APPROVAL_DATE"
    LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE = "LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE"
    PILOT_CONVERSION_EXPLICIT_START = "PILOT_CONVERSION_EXPLICIT_START"


_LATE_RENEWAL_RULES = frozenset(
    {
        RenewalDateRule.LATE_RENEWAL_FROM_PREVIOUS_END,
        RenewalDateRule.LATE_RENEWAL_FROM_APPROVAL_DATE,
        RenewalDateRule.LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE,
    }
)


class RenewalDateError(ValueError):
    pass


def add_interval(base: date, *, months: int | None = None, days: int | None = None) -> date:
    """Add a billing interval to a date. Month arithmetic preserves the
    day-of-month where possible and clamps to the target month's last valid
    day otherwise (Jan 31 + 1 month -> Feb 28, or Feb 29 on a leap year --
    never silently overflowing into March). Exactly one of months/days must
    be given; a renewal interval is either "N months" (MONTHLY/ANNUAL plans)
    or "N days" (custom/promotional terms), never a fuzzy mix of both."""
    if (months is None) == (days is None):
        raise RenewalDateError("Specify exactly one of months or days.")
    if days is not None:
        return base + timedelta(days=days)

    total_month_index = base.month - 1 + months  # type: ignore[operator]
    year = base.year + total_month_index // 12
    month = total_month_index % 12 + 1
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    day = min(base.day, last_day_of_target_month)
    return date(year, month, day)


def is_early_renewal(*, current_term_end: date | None, as_of: date) -> bool:
    """The one classification this module performs automatically: whether
    `as_of` falls on-or-before the current term's own end date. This does
    NOT select which late-renewal anchor to use when it returns False --
    that always requires an explicit caller choice (see
    `calculate_late_renewal`)."""
    return current_term_end is not None and as_of <= current_term_end


def calculate_early_renewal(
    *, current_term_end: date, interval_months: int | None = None, interval_days: int | None = None
) -> tuple[date, date, str]:
    """Early renewal: the new term starts from the existing valid-until
    date. Remaining paid time is never discarded (Part D)."""
    if current_term_end is None:
        raise RenewalDateError("Early renewal requires an existing current_term_end.")
    new_start = current_term_end
    new_end = add_interval(new_start, months=interval_months, days=interval_days)
    return new_start, new_end, RenewalDateRule.EARLY_RENEWAL_FROM_CURRENT_END.value


def calculate_late_renewal(
    *,
    rule: RenewalDateRule,
    previous_term_end: date | None = None,
    approval_date: date | None = None,
    payment_confirmed_date: date | None = None,
    interval_months: int | None = None,
    interval_days: int | None = None,
) -> tuple[date, date, str]:
    """Renewal after expiry. `rule` must be one of the three documented
    late-renewal anchors -- the caller (a service/route, never this pure
    module) is responsible for deciding which one applies as a matter of
    commercial policy; this function only refuses to guess."""
    if rule not in _LATE_RENEWAL_RULES:
        raise RenewalDateError(f"Not a valid late-renewal rule: {rule!r}")
    if rule == RenewalDateRule.LATE_RENEWAL_FROM_PREVIOUS_END:
        if previous_term_end is None:
            raise RenewalDateError("LATE_RENEWAL_FROM_PREVIOUS_END requires previous_term_end.")
        new_start = previous_term_end
    elif rule == RenewalDateRule.LATE_RENEWAL_FROM_APPROVAL_DATE:
        if approval_date is None:
            raise RenewalDateError("LATE_RENEWAL_FROM_APPROVAL_DATE requires approval_date.")
        new_start = approval_date
    else:
        if payment_confirmed_date is None:
            raise RenewalDateError("LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE requires payment_confirmed_date.")
        new_start = payment_confirmed_date
    new_end = add_interval(new_start, months=interval_months, days=interval_days)
    return new_start, new_end, rule.value


def calculate_pilot_conversion(
    *,
    paid_term_start: date,
    interval_months: int | None = None,
    interval_days: int | None = None,
    credited_days: int = 0,
) -> tuple[date, date, str]:
    """Pilot conversion: the paid term's start date is always an explicit
    input, never inherited from the pilot's own dates (Part D/M: "pilot time
    must not silently become paid time"). Any credit for pilot time is an
    explicit, separately-visible adjustment -- extra days appended to the
    computed end date -- never a backdated start."""
    if credited_days < 0:
        raise RenewalDateError("credited_days must not be negative.")
    new_end = add_interval(paid_term_start, months=interval_months, days=interval_days)
    if credited_days:
        new_end = new_end + timedelta(days=credited_days)
    return paid_term_start, new_end, RenewalDateRule.PILOT_CONVERSION_EXPLICIT_START.value
