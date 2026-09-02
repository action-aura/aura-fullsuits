"""Aura Retail -- WhatsApp report triggers.

Never let a side-feature touch the primary operation -- same contract
core/retail/reorder_hook.py's own module docstring documents for itself,
applied here to four distinct trigger points instead of one:

  - queue_low_stock_alert() takes the CALLER's already-open connection and
    runs inside the SAME transaction as reorder_hook.py's own
    _maybe_queue_low_stock_email() call (same idempotency reasoning: an
    alert is only ever queued when a brand-new reorder_requests row was
    actually created, so idx_reorder_requests_open's uniqueness already
    caps this at "at most one open low-stock WhatsApp alert per product",
    with no extra table needed).
  - queue_shift_close_report() opens its OWN connection, called AFTER
    close_cash_session()'s own commit -- mirrors maybe_trigger_reorder()'s
    post-sale pattern exactly. The route wraps this call in a broad
    try/except so a failure here can never change the close response.
  - queue_daily_sales_summary() / queue_ar_overdue_alert() are on-demand
    (POST /reports/whatsapp), so they run on the SAME connection the route
    already opened and will commit/close itself -- no separate connection
    needed for a request that has no prior side effect to protect.

Every function is a gated no-op -- zero behavior change -- when: WhatsApp
transport is unconfigured, this company hasn't opted in, no template name is
configured for that report type, or recipients_for() returns nobody
subscribed. An install that never touches any of this sees zero rows ever
appear in whatsapp_outbox, same "invisible unless opted in" guarantee every
other optional channel in this codebase gives (e-invoicing, email).

Scope trim, stated explicitly rather than silently gapped: daily_sales_
summary and ar_overdue_alert send COMPANY-WIDE figures to every subscribed
recipient, regardless of that recipient's own branch_id -- unlike shift_
close_report/low_stock_alert, which are genuinely per-branch events.
Building real per-branch daily-sales/AR aggregation for a recipient scoped
to one branch is deferred; for a small shop, an owner subscribed to the
daily summary almost always wants the whole company's number anyway. A
recipient's branch_id still fully applies to shift_close_report/low_stock_
alert routing, where it is the actually-correct behavior.

All component_params values are forced to single-line strings before being
handed to enqueue() -- Meta rejects newlines/tabs inside a template body
parameter, and several of the source strings here (product names, branch
names) are free-form user data that could theoretically contain either.

MONEY FIGURES go through `core/retail/money_format.py`, the same module
`email_hook.py` uses -- not a private `:.2f` here. This file used to
hardcode `:.2f` at every money placeholder (shift-close cash figures, the
daily-sales revenue/avg-ticket/profit trio, the AR-overdue totals), which
silently disagreed with email's currency-aware rendering for any 3-decimal
currency (the Jordanian dinar, this product's home market, among others):
the same drawer would read `12.350` in an email and `12.35` over WhatsApp.
See `money_format.py`'s own docstring for why two channels disagreeing
about the same figure is worse than either channel alone being wrong.
Quantities (on_hand/reorder_level in the low-stock alert) are NOT money and
keep their own `:g` formatting -- currency awareness does not apply to a
unit count.
"""
from __future__ import annotations

import json

from commercial_runtime.notifications import whatsapp_settings
from commercial_runtime.notifications import whatsapp_recipients as _recipients
from commercial_runtime.notifications.whatsapp_outbox import WhatsAppOutboxRepository as _WhatsAppOutboxRepository
from core.retail import money_format as _money_format


def _oneline(value) -> str:
    return ' '.join(str(value).split())


def _ready_template(conn, company_id, report_type):
    """Returns the configured template name, or None when this report type
    is not ready to send (transport unconfigured, company not opted in, or
    no template name set for this report type) -- every queue_* function
    below checks this FIRST, before touching whatsapp_recipients at all."""
    if not whatsapp_settings.is_enabled(conn, company_id):
        return None
    return whatsapp_settings.template_name_for(conn, company_id, report_type)


def _enqueue_for_recipients(conn, *, company_id, report_type, template_name, branch_id, params_fn):
    """Shared fan-out: look up matching recipients, enqueue one row per
    recipient with THAT recipient's own language_code, params built by
    calling params_fn() once (all four report types send the same figures
    to every recipient of that type -- only language/phone vary per row).
    Returns the count queued."""
    matched = _recipients.recipients_for(conn, company_id, report_type, branch_id=branch_id)
    if not matched:
        return 0
    # AUDIT-fix: whatsapp_recipients has no (company_id, phone_e164)
    # uniqueness constraint -- two distinct recipient rows (e.g. "Owner" and
    # "Accountant") can legitimately share one real phone number. Without
    # this dedup, both rows matching the same report_type would enqueue two
    # separate WhatsApp sends to that one number for the same event. Keep
    # the first match per phone number; a recipient's own row order (from
    # recipients_for()'s SQL) is otherwise not meaningful here.
    seen_phones = set()
    deduped = []
    for recipient in matched:
        if recipient['phone_e164'] in seen_phones:
            continue
        seen_phones.add(recipient['phone_e164'])
        deduped.append(recipient)
    matched = deduped
    params = [_oneline(p) for p in params_fn()]
    repo = _WhatsAppOutboxRepository(conn)
    for recipient in matched:
        repo.enqueue(
            company_id=company_id, message_type=report_type,
            recipient_phone_e164=recipient['phone_e164'], template_name=template_name,
            language_code=recipient['language_code'] or 'en_US',
            component_params_json=json.dumps(params),
        )
    return len(matched)


def queue_shift_close_report(conn_factory, *, company_id, branch_id, branch_name, report) -> int:
    """`report` is the dict close_cash_session() already built (the SAME
    object _cash_session_report() returned, with closing_float_counted/
    variance already merged in by that route) -- captured in full here, at
    enqueue time, for the same reason every outbox row captures its content
    up front rather than re-querying later (schema.py's own docstring)."""
    conn = conn_factory()
    try:
        template_name = _ready_template(conn, company_id, 'shift_close_report')
        if template_name is None:
            return 0
        currency = _money_format.company_currency(conn, company_id)
        variance = float(report['variance'] or 0)
        count = _enqueue_for_recipients(
            conn, company_id=company_id, report_type='shift_close_report',
            template_name=template_name, branch_id=branch_id,
            params_fn=lambda: [
                branch_name or 'Main', str(report['window_end'])[:16],
                _money_format.format_money(report['expected_cash'], currency),
                _money_format.format_money(report['closing_float_counted'], currency),
                ('+' if variance > 0 else '') + _money_format.format_money(variance, currency),
            ],
        )
        conn.commit()
        return count
    finally:
        conn.close()


def queue_low_stock_alert(conn, *, company_id, branch_id, branch_name, product_name, on_hand, reorder_level) -> int:
    """Takes the CALLER's own connection -- see this module's docstring for
    why (same-transaction as the reorder_requests row, mirrored from
    reorder_hook.py's own low-stock email call). Caller commits; this
    function never does."""
    template_name = _ready_template(conn, company_id, 'low_stock_alert')
    if template_name is None:
        return 0
    return _enqueue_for_recipients(
        conn, company_id=company_id, report_type='low_stock_alert',
        template_name=template_name, branch_id=branch_id,
        params_fn=lambda: [product_name, f"{on_hand:g}", f"{reorder_level:g}", branch_name or 'Main'],
    )


def queue_daily_sales_summary(conn, *, company_id, business_name, summary) -> int:
    """`summary` is a dict already computed by the caller (retail_api.py's
    _compute_report_summary(conn, cid, 1)) -- see this module's docstring
    for why this sends the same company-wide figures to every subscriber
    regardless of their own branch_id. Runs on the caller's own connection;
    caller commits."""
    template_name = _ready_template(conn, company_id, 'daily_sales_summary')
    if template_name is None:
        return 0
    from datetime import datetime
    today = datetime.now().strftime('%Y-%m-%d')
    currency = _money_format.company_currency(conn, company_id)
    return _enqueue_for_recipients(
        conn, company_id=company_id, report_type='daily_sales_summary',
        template_name=template_name, branch_id=None,
        params_fn=lambda: [
            business_name or 'Aura Retail', today,
            _money_format.format_money(summary['revenue'], currency),
            str(summary['transactions']),
            _money_format.format_money(summary['avg_ticket'], currency),
            _money_format.format_money(summary['gross_profit'], currency),
        ],
    )


def queue_ar_overdue_alert(conn, *, company_id, overdue_total, customer_count, bucket_90_plus) -> int:
    """overdue_total/customer_count/bucket_90_plus are precomputed by the
    caller (retail_api.py's aging_report() bucketing logic) -- same
    company-wide-only scope note as queue_daily_sales_summary above; AR has
    no branch dimension in this schema at all (customers.credit_balance is
    company-scoped, not branch-scoped), so this is not even a trim, just a
    fact about the data."""
    template_name = _ready_template(conn, company_id, 'ar_overdue_alert')
    if template_name is None:
        return 0
    currency = _money_format.company_currency(conn, company_id)
    return _enqueue_for_recipients(
        conn, company_id=company_id, report_type='ar_overdue_alert',
        template_name=template_name, branch_id=None,
        params_fn=lambda: [
            _money_format.format_money(overdue_total, currency),
            str(customer_count),
            _money_format.format_money(bucket_90_plus, currency),
        ],
    )
