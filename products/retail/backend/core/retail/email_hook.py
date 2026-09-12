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

MONEY IS FORMATTED AT THE SHOP'S OWN PRECISION, deliberately -- and now via
ONE shared module, `core/retail/money_format.py`, rather than a formatter
private to this file. The WhatsApp shift-close report used to render its
figures with a hardcoded `:.2f`, which is wrong for the Jordanian dinar --
1000 fils, three decimal places -- and printed JD 12.35 for a drawer holding
12.350, while this channel printed the correct 12.350 for the identical
close. That was not "WhatsApp is wrong and email is right" so much as "two
channels reporting the same event in two different precisions", which is
worse than either being wrong alone: a shop with both channels enabled saw
its own records disagree with themselves. `money_format.py` now owns both
`company_currency()` and `format_money()` so email and WhatsApp can no
longer drift apart the way they did before -- see that module's docstring
for the full reasoning.
"""
from __future__ import annotations

from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository as _EmailOutboxRepository
from core.retail import money_format as _money_format


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

        currency = _money_format.company_currency(conn, company_id)
        where = branch_name or 'Main'
        closed_at = str(report.get('window_end') or '')[:16]
        variance = float(report.get('variance') or 0)

        subject = f"Shift closed -- {where} -- {closed_at}"
        lines = [
            f"Cash session closed at {where}.",
            "",
            f"Closed at:       {closed_at}",
            f"Expected cash:   {_money_format.format_money(report.get('expected_cash'), currency)}",
            f"Counted:         {_money_format.format_money(report.get('closing_float_counted'), currency)}",
            f"Variance:        {'+' if variance > 0 else ''}{_money_format.format_money(variance, currency)}",
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
