"""Aura Retail -- shared money-formatting for outbound notifications.

WHY THIS MODULE EXISTS

Two notification channels render the same Z-report figures independently:
`email_hook.py::queue_shift_close_email` and
`whatsapp_hook.py::queue_shift_close_report` both build a human-readable
rendering of the same cash-session numbers, from the same `report` dict, for
the same close event. A shop can have BOTH channels enabled at once (an
owner subscribed to WhatsApp, an accountant reading email), so if the two
channels round or format money differently, the shop's own two records of
the same closed drawer disagree with each other. That is a strictly WORSE
failure than either channel alone being wrong: a wrong number in one place
is a bug to find and fix, but two different "correct" answers to "how much
cash was in the drawer" -- from the same install, about the same event --
erodes trust in both of them at once, with no way to tell which to believe
without opening the till software directly.

This is exactly what had already shipped: email read `base_currency` and
formatted to the currency's real minor units, while WhatsApp's shift-close
report hardcoded `:.2f` regardless of currency. For a JOD shop (three
decimal places, 1000 fils) that meant a drawer holding 12.350 was reported
as `JD 12.350` by email and `12.35` by WhatsApp for the identical close.

This module is the ONE place that decides the two questions every
money-reporting notification channel needs answered identically:

  * `company_currency(conn, company_id)` -- which currency this shop's
    figures are in, falling back to `pricing.DEFAULT_BASE_CURRENCY` ('JOD')
    for a shop that has never opened settings, NOT to
    `pricing.CURRENCY_DECIMALS`. Those answer different questions:
    CURRENCY_DECIMALS (2) is what an UNKNOWN currency CODE degrades to;
    DEFAULT_BASE_CURRENCY is what NO code at all means, which is a
    Jordanian shop selling in dinars. Getting that backwards is exactly the
    bug that shipped once already -- see `pricing.DEFAULT_BASE_CURRENCY`'s
    own docstring, and `api/retail_api.py::_company_currency`, which makes
    the identical choice on the API side for the identical reason.

  * `format_money(value, currency)` -- render `value` at that currency's
    real minor units (`pricing.CURRENCY_MINOR_UNITS`), e.g. `JD 12.350` for
    a 3-decimal dinar, never a hardcoded `:.2f`.

Both functions keep the never-raise, degrade-to-a-default posture every
similar helper in this codebase uses (see `pricing.currency_quantum`'s own
docstring for the same rule stated once, at the source): a malformed or
missing setting must produce a sane default, never a 500 -- these are called
from best-effort notification paths that must never block the transaction
they report on.
"""
from __future__ import annotations

import sqlite3

from core.retail import pricing as _pricing

#: Same key `api/retail_api.py::_DEFAULT_SETTINGS` uses, read here directly
#: because core/retail must not import api/ (api/ already imports core/).
_CURRENCY_SETTING = 'base_currency'


def company_currency(conn, company_id):
    """This shop's currency code, falling back to the product default.

    Degrades rather than raising, the same way `metrics.business_day`
    degrades on a database whose settings table does not exist yet -- a
    report is not worth a 500, and this is called from a best-effort path.

    It degrades to `pricing.DEFAULT_BASE_CURRENCY`, NOT to None. None would
    route `format_money` below to `pricing.CURRENCY_DECIMALS`, which is 2 --
    the fallback for an UNKNOWN currency code, not for an ABSENT one. A shop
    that has never opened settings is a Jordanian shop selling in dinars,
    and every report about its drawer has to print fils.
    """
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
            (company_id, _CURRENCY_SETTING)).fetchone()
    except sqlite3.Error:
        return _pricing.DEFAULT_BASE_CURRENCY
    return (row[0] if row and row[0] else None) or _pricing.DEFAULT_BASE_CURRENCY


def format_money(value, currency) -> str:
    """`JD 12.350`, at the currency's real minor units -- not a hardcoded 2dp."""
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    digits = _pricing.CURRENCY_MINOR_UNITS.get(
        (currency or '').strip().upper(), _pricing.CURRENCY_DECIMALS)
    symbol = _pricing.currency_symbol(currency)
    body = f"{amount:.{digits}f}"
    # A single-character mark hugs its digits, a multi-letter one takes a
    # space -- the same rule the till and the phone already use, so a figure
    # in a notification reads identically to the one on screen.
    return (symbol + body) if len(symbol) == 1 else f"{symbol} {body}"
