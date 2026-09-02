"""Automatic emails from retail trigger points.

WHY THIS MODULE EXISTS

The WhatsApp channel has four report types wired
(`whatsapp_settings.REPORT_TYPES`) and two of them fire AUTOMATICALLY: a
low-stock alert after a sale, and a shift-close (Z) report when a cash
session closes. Email had only ONE automatic trigger -- the low-stock alert
in `reorder_hook._maybe_queue_low_stock_email` -- so a shop that chose email
instead of WhatsApp got nothing at all when a till was closed and counted.
That is the gap this module closes.

It follows the outbox pattern the e-invoicing work established and this
repo's CLAUDE.md nominates for every async, external-facing feature: QUEUE,
never send inline. `EmailOutboxWorker` drains it, and
`app.py::_resume_notifications_workers` restarts that worker after a reboot,
so a queued report survives the PC being switched off.

INVISIBLE UNLESS OPTED IN, like every notification in this product. Both
guards below have to pass -- `is_enabled()` (which folds in "is SMTP
configured at all") and a configured recipient -- so an install that has done
neither sees exactly zero behaviour change: no row, no error, no log.

MONEY IS FORMATTED AT THE SHOP'S OWN PRECISION, deliberately. The WhatsApp
shift-close report renders its figures with `:.2f`, which is wrong for the
Jordanian dinar -- 1000 fils, three decimal places -- and would print
JD 12.35 for a drawer holding 12.350. This module reads `base_currency` from
`retail_settings` and formats to that currency's real minor units, the same
source `core/retail/pricing.py` uses. See ROADMAP for the WhatsApp side,
which is recorded rather than changed here: touching a second channel's
formatting inside a change that adds a third trigger would bury it.
"""
from __future__ import annotations

import sqlite3

from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository as _EmailOutboxRepository
from core.retail import pricing as _pricing

#: Same key `api/retail_api.py::_DEFAULT_SETTINGS` uses, read here directly
#: because core/retail must not import api/ (api/ already imports core/).
_CURRENCY_SETTING = 'base_currency'


def _currency(conn, company_id):
    """This shop's currency code, falling back to the product default.

    Degrades rather than raising, the same way `metrics.business_day` degrades
    on a database whose settings table does not exist yet -- a report is not
    worth a 500, and this is called from a best-effort path.

    It degrades to `pricing.DEFAULT_BASE_CURRENCY`, NOT to None. None would
    route `_money` below to `pricing.CURRENCY_DECIMALS`, which is 2 -- the
    fallback for an UNKNOWN currency code, not for an ABSENT one. A shop that
    has never opened settings is a Jordanian shop selling in dinars, and its
    Z-report has to print fils. `api/retail_api.py::_company_currency` makes
    the identical choice for the identical reason; both used to answer None
    and both were wrong for the most common install there is.
    """
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
            (company_id, _CURRENCY_SETTING)).fetchone()
    except sqlite3.Error:
        return _pricing.DEFAULT_BASE_CURRENCY
    return (row[0] if row and row[0] else None) or _pricing.DEFAULT_BASE_CURRENCY


def _money(value, currency):
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
    # in an email reads identically to the one on screen.
    return (symbol + body) if len(symbol) == 1 else f"{symbol} {body}"


def queue_shift_close_email(conn_factory, *, company_id, branch_name, report) -> int:
    """Queue the Z-report email for a cash session that just closed.

    Opens its OWN connection and commits it, exactly like
    `whatsapp_hook.queue_shift_close_report`, and for the same reason the
    e-invoicing call site in `create_sale()` does: the close has ALREADY
    committed by the time this runs, so nothing here may share, roll back or
    otherwise touch that transaction.

    `report` is the dict `close_cash_session()` already built, captured in
    full at enqueue time rather than re-queried later -- the outbox rule.

    Returns 1 if a row was queued, 0 if this install has not opted in.
    """
    conn = conn_factory()
    try:
        if not _notification_settings.is_enabled(conn, company_id):
            return 0
        recipient = _notification_settings.recipient_for(
            conn, company_id, 'reports_recipient')
        if not recipient:
            return 0

        currency = _currency(conn, company_id)
        where = branch_name or 'Main'
        closed_at = str(report.get('window_end') or '')[:16]
        variance = float(report.get('variance') or 0)

        subject = f"Shift closed -- {where} -- {closed_at}"
        lines = [
            f"Cash session closed at {where}.",
            "",
            f"Closed at:       {closed_at}",
            f"Expected cash:   {_money(report.get('expected_cash'), currency)}",
            f"Counted:         {_money(report.get('closing_float_counted'), currency)}",
            f"Variance:        {'+' if variance > 0 else ''}{_money(variance, currency)}",
        ]
        # A drawer that balanced is the boring case and should read as such;
        # a variance is the whole reason someone opens this email.
        if abs(variance) > 0.0005:
            status = report.get('variance_status')
            lines += ["",
                      "This drawer did NOT balance.",
                      f"Status: {status}" if status else ""]
        else:
            lines += ["", "This drawer balanced."]

        _EmailOutboxRepository(conn).enqueue(
            company_id=company_id, email_type='shift_close_report',
            recipient=recipient, subject=subject,
            body_text="\n".join(line for line in lines if line is not None),
        )
        conn.commit()
        return 1
    finally:
        conn.close()
