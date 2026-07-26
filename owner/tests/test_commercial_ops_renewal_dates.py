from datetime import date

import pytest

from app.commercial_ops.renewal_dates import (
    RenewalDateError,
    RenewalDateRule,
    add_interval,
    calculate_early_renewal,
    calculate_late_renewal,
    calculate_pilot_conversion,
    is_early_renewal,
)


# -- add_interval: month/leap-year/day-clamping edge cases -----------------

def test_add_interval_simple_month():
    assert add_interval(date(2026, 1, 15), months=1) == date(2026, 2, 15)


def test_add_interval_month_end_clamped_to_shorter_month():
    # Jan 31 + 1 month must clamp to Feb 28 (2026 is not a leap year), never
    # overflow into March.
    assert add_interval(date(2026, 1, 31), months=1) == date(2026, 2, 28)


def test_add_interval_leap_year_february():
    # 2028 is a leap year -- Jan 31 + 1 month clamps to Feb 29.
    assert add_interval(date(2028, 1, 31), months=1) == date(2028, 2, 29)


def test_add_interval_annual_term_leap_day_start():
    # A Feb 29 start renewed annually onto a non-leap year clamps to Feb 28.
    assert add_interval(date(2028, 2, 29), months=12) == date(2029, 2, 28)


def test_add_interval_year_boundary():
    assert add_interval(date(2026, 12, 15), months=1) == date(2027, 1, 15)


def test_add_interval_multi_year():
    assert add_interval(date(2026, 3, 10), months=24) == date(2028, 3, 10)


def test_add_interval_days():
    assert add_interval(date(2026, 1, 1), days=14) == date(2026, 1, 15)


def test_add_interval_requires_exactly_one_of_months_or_days():
    with pytest.raises(RenewalDateError):
        add_interval(date(2026, 1, 1))
    with pytest.raises(RenewalDateError):
        add_interval(date(2026, 1, 1), months=1, days=1)


# -- is_early_renewal --------------------------------------------------------

def test_is_early_renewal_true_when_before_end():
    assert is_early_renewal(current_term_end=date(2026, 8, 1), as_of=date(2026, 7, 1)) is True


def test_is_early_renewal_true_on_same_day_as_end():
    # Same-day renewal counts as early (Part D's own test list).
    assert is_early_renewal(current_term_end=date(2026, 8, 1), as_of=date(2026, 8, 1)) is True


def test_is_early_renewal_false_after_end():
    assert is_early_renewal(current_term_end=date(2026, 8, 1), as_of=date(2026, 8, 2)) is False


def test_is_early_renewal_false_with_no_current_end():
    assert is_early_renewal(current_term_end=None, as_of=date(2026, 8, 1)) is False


# -- calculate_early_renewal -------------------------------------------------

def test_early_renewal_does_not_shorten_remaining_term():
    new_start, new_end, rule = calculate_early_renewal(current_term_end=date(2026, 8, 1), interval_months=1)
    assert new_start == date(2026, 8, 1)
    assert new_end == date(2026, 9, 1)
    assert rule == RenewalDateRule.EARLY_RENEWAL_FROM_CURRENT_END.value


def test_early_renewal_requires_current_term_end():
    with pytest.raises(RenewalDateError):
        calculate_early_renewal(current_term_end=None, interval_months=1)


def test_multiple_consecutive_early_renewals_never_lose_time():
    start, end, _ = calculate_early_renewal(current_term_end=date(2026, 1, 1), interval_months=1)
    assert (start, end) == (date(2026, 1, 1), date(2026, 2, 1))
    start2, end2, _ = calculate_early_renewal(current_term_end=end, interval_months=1)
    assert (start2, end2) == (date(2026, 2, 1), date(2026, 3, 1))
    start3, end3, _ = calculate_early_renewal(current_term_end=end2, interval_months=1)
    assert (start3, end3) == (date(2026, 3, 1), date(2026, 4, 1))


# -- calculate_late_renewal ---------------------------------------------------

def test_late_renewal_from_previous_end():
    new_start, new_end, rule = calculate_late_renewal(
        rule=RenewalDateRule.LATE_RENEWAL_FROM_PREVIOUS_END,
        previous_term_end=date(2026, 6, 1),
        interval_months=1,
    )
    assert new_start == date(2026, 6, 1)
    assert new_end == date(2026, 7, 1)
    assert rule == "LATE_RENEWAL_FROM_PREVIOUS_END"


def test_late_renewal_from_approval_date():
    new_start, new_end, rule = calculate_late_renewal(
        rule=RenewalDateRule.LATE_RENEWAL_FROM_APPROVAL_DATE,
        approval_date=date(2026, 7, 15),
        interval_months=1,
    )
    assert new_start == date(2026, 7, 15)
    assert rule == "LATE_RENEWAL_FROM_APPROVAL_DATE"


def test_late_renewal_from_payment_confirmed_date():
    new_start, new_end, rule = calculate_late_renewal(
        rule=RenewalDateRule.LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE,
        payment_confirmed_date=date(2026, 7, 20),
        interval_days=30,
    )
    assert new_start == date(2026, 7, 20)
    assert new_end == date(2026, 8, 19)
    assert rule == "LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE"


def test_late_renewal_never_infers_which_rule_silently():
    # Requesting a rule without its required anchor date is a hard error --
    # this module never falls back to a default anchor.
    with pytest.raises(RenewalDateError):
        calculate_late_renewal(rule=RenewalDateRule.LATE_RENEWAL_FROM_PREVIOUS_END, interval_months=1)
    with pytest.raises(RenewalDateError):
        calculate_late_renewal(rule=RenewalDateRule.LATE_RENEWAL_FROM_APPROVAL_DATE, interval_months=1)
    with pytest.raises(RenewalDateError):
        calculate_late_renewal(rule=RenewalDateRule.LATE_RENEWAL_FROM_PAYMENT_CONFIRMED_DATE, interval_months=1)


def test_late_renewal_rejects_early_renewal_rule():
    with pytest.raises(RenewalDateError):
        calculate_late_renewal(
            rule=RenewalDateRule.EARLY_RENEWAL_FROM_CURRENT_END,
            previous_term_end=date(2026, 6, 1),
            interval_months=1,
        )


# -- calculate_pilot_conversion -----------------------------------------------

def test_pilot_conversion_start_is_explicit_not_backdated():
    new_start, new_end, rule = calculate_pilot_conversion(paid_term_start=date(2026, 9, 1), interval_months=12)
    assert new_start == date(2026, 9, 1)
    assert new_end == date(2027, 9, 1)
    assert rule == RenewalDateRule.PILOT_CONVERSION_EXPLICIT_START.value


def test_pilot_conversion_credited_days_is_explicit_adjustment_not_backdated_start():
    new_start, new_end, _ = calculate_pilot_conversion(
        paid_term_start=date(2026, 9, 1), interval_months=1, credited_days=10
    )
    # Start date is untouched by the credit -- only the end date reflects it.
    assert new_start == date(2026, 9, 1)
    assert new_end == date(2026, 10, 11)  # Oct 1 (one month) + 10 credited days


def test_pilot_conversion_rejects_negative_credit():
    with pytest.raises(RenewalDateError):
        calculate_pilot_conversion(paid_term_start=date(2026, 9, 1), interval_months=1, credited_days=-1)


# -- duplicate/idempotent-retry semantics at the pure-function level --------

def test_calling_the_same_calculation_twice_is_deterministic():
    # This module has no state, so "duplicate retry" safety at this layer
    # just means: same inputs always produce the same outputs, which is what
    # the workflow layer (Milestone 2) relies on to make the actual
    # idempotency-key check meaningful.
    first = calculate_early_renewal(current_term_end=date(2026, 8, 1), interval_months=1)
    second = calculate_early_renewal(current_term_end=date(2026, 8, 1), interval_months=1)
    assert first == second
