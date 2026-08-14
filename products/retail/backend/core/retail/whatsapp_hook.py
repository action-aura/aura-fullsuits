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
"""
from __future__ import annotations

import json

from commercial_runtime.notifications import whatsapp_settings
from commercial_runtime.notifications import whatsapp_recipients as _recipients
from commercial_runtime.notifications.whatsapp_outbox import WhatsAppOutboxRepository as _WhatsAppOutboxRepository


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
        count = _enqueue_for_recipients(
            conn, company_id=company_id, report_type='shift_close_report',
            template_name=template_name, branch_id=branch_id,
            params_fn=lambda: [
                branch_name or 'Main', str(report['window_end'])[:16],
                f"{report['expected_cash']:.2f}", f"{report['closing_float_counted']:.2f}",
                f"{report['variance']:+.2f}",
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
    return _enqueue_for_recipients(
        conn, company_id=company_id, report_type='daily_sales_summary',
        template_name=template_name, branch_id=None,
        params_fn=lambda: [
            business_name or 'Aura Retail', today, f"{summary['revenue']:.2f}",
            str(summary['transactions']), f"{summary['avg_ticket']:.2f}", f"{summary['gross_profit']:.2f}",
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
    return _enqueue_for_recipients(
        conn, company_id=company_id, report_type='ar_overdue_alert',
        template_name=template_name, branch_id=None,
        params_fn=lambda: [f"{overdue_total:.2f}", str(customer_count), f"{bucket_90_plus:.2f}"],
    )
