"""Aura Retail -- post-sale reorder-automation trigger
(feat/reorder-automation-foundation).

Turns a just-completed sale into zero or more `reorder_requests` rows: for
every DISTINCT product sold, if the product opted in
(`products.reorder_method != 'none'`) and its post-sale stock balance has
dropped to or below `products.reorder_level`, and no request is already
open for that product, this creates one pending row and queues it for sync.

Mirrors core/retail/einvoice_adapter.py's `enqueue_sale` structural
precedent closely on purpose (same codebase, same "never let a side-feature
touch the sale" contract) -- see that file's own module docstring:

  - Called AFTER the sale's own request has already committed and returned
    its response shape (retail_api.py::create_sale). Opens its OWN
    independent connection via `conn_factory`, never reuses the sale's
    connection, so this can never roll back, block, or in any way affect
    the sale transaction that already succeeded.
  - The caller (create_sale) wraps this in a broad try/except regardless --
    belt and suspenders, exactly like the einvoicing call site. Nothing in
    this module is allowed to propagate an exception that would change the
    sale's HTTP response; see
    products/retail/tests/retail_reorder_hook_regression_test.py.
  - Unlike einvoicing, this NEVER adds a key to the sale's response --
    reorder automation is invisible from the checkout API's own contract,
    not merely best-effort. There is no equivalent of einvoicing's
    'einvoice' response key here, by design.

Quantity note (Phase 1 foundation, deliberately simple): the local
purchase_order an Accept action drafts (see retail_api.py's accept route)
orders exactly `reorder_level` units of the product -- restocking back up
to the reorder threshold itself, not a demand-based forecast. Refining that
heuristic is explicitly out of scope for this foundation wave; the
WhatsApp-facing quantity/urgency messaging this eventually feeds is
deferred entirely (see the migration's own docstring in database/schema.py
and ROADMAP.md).

feat/email-outbox-foundation (2026-08-12): the SAME event that opens a new
`reorder_requests` row now ALSO queues a low-stock alert email, on the SAME
connection/transaction as that row (see `_maybe_queue_low_stock_email`
below) -- reusing idx_reorder_requests_open's existing idempotency
guarantee for free instead of inventing a second one: an email is only ever
queued when a brand-new pending request is actually created, so "at most
one open request per product" already implies "at most one low-stock email
per open low-stock event" with no extra table or check needed. Queuing is
itself gated on commercial_runtime.notifications.settings.is_enabled() (SMTP
configured AND this company opted in) and a configured recipient -- an
install that has done neither sees ZERO behavior change from this addition:
same reorder_requests row, same sync_outbox event, nothing more.
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone

from commercial_runtime.notifications import settings as _notification_settings
from commercial_runtime.notifications.outbox import EmailOutboxRepository as _EmailOutboxRepository
from core.retail import whatsapp_hook as _whatsapp_hook


def maybe_trigger_reorder(conn_factory, *, company_id, branch_id, product_ids):
    """Called once per completed sale with the DISTINCT product ids that sale
    just sold (a multi-line sale can drop several products below their own
    reorder_level at once -- each is evaluated independently). Returns the
    list of product_ids a new pending reorder_requests row was actually
    created for (empty list is the overwhelmingly common case: most
    products never opt in, and most sales don't cross the threshold).

    Opens exactly one connection for the whole batch (not one per product)
    -- cheaper, and the idempotency guard below is safe per-product anyway
    since each product's check-then-insert is its own short transaction.
    """
    if not product_ids:
        return []
    conn = conn_factory()
    try:
        created = []
        for product_id in product_ids:
            if _maybe_create_request_for_product(conn, company_id, branch_id, product_id):
                created.append(product_id)
        return created
    finally:
        conn.close()


def _maybe_create_request_for_product(conn, company_id, branch_id, product_id) -> bool:
    """One product, one short BEGIN IMMEDIATE transaction -- mirrors
    create_sale's own use of BEGIN IMMEDIATE (retail_api.py) to avoid a
    check-then-insert race between two near-simultaneous sales of the same
    product both observing "no open request yet" before either commits.
    idx_reorder_requests_open (database/schema.py) is the real backstop if
    that race is ever hit anyway -- this transaction just avoids relying on
    a raised IntegrityError as the normal-case control flow.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        # launch-readiness Phase 6 stage 6b-iii-b: `AND deleted_at_utc IS
        # NULL` added -- this is the WRITE gate for a NEW reorder request, so
        # a tombstoned product must raise none, exactly like create_purchase_
        # order's own product read (retail_api.py). This is deliberately NOT
        # the same rule as an EXISTING pending request for a product that is
        # deleted AFTER the request was raised -- see phase6b-decisions.md /
        # this stage's own docs for why that one stays visible and declinable
        # (list_reorder_requests's INNER JOIN is untouched) while only the
        # CREATE path here is gated. A missing/tombstoned product simply
        # reads as `not product` below, exactly like an id that never
        # existed.
        #
        # `status='active'` joins the tombstone check for the same
        # two-population reason retail_api.py's `create_supplier_contact`
        # comment spells out in full -- a product deleted before tombstones
        # existed never got a `deleted_at_utc` stamp, so the tombstone
        # filter alone would miss it and let a new reorder request raise
        # against it.
        product = conn.execute(
            "SELECT name, reorder_method, reorder_level FROM products "
            "WHERE id=? AND company_id=? AND status='active' AND deleted_at_utc IS NULL",
            (product_id, company_id),
        ).fetchone()
        if not product or (product['reorder_method'] or 'none') == 'none':
            conn.rollback()
            return False

        reorder_level = float(product['reorder_level'] or 0)

        balance = conn.execute(
            "SELECT quantity_on_hand FROM inventory_balances WHERE company_id=? AND product_id=? AND branch_id=?",
            (company_id, product_id, branch_id),
        ).fetchone()
        on_hand = float(balance['quantity_on_hand']) if balance else 0.0
        if on_hand > reorder_level:
            conn.rollback()
            return False

        existing = conn.execute(
            "SELECT id FROM reorder_requests WHERE company_id=? AND product_id=? AND status IN ('pending','accepted')",
            (company_id, product_id),
        ).fetchone()
        if existing:
            conn.rollback()
            return False

        request_id = str(_uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        draft_message = (
            f"Stock for this product is at {on_hand:g} (reorder level {reorder_level:g}). "
            f"Suggested reorder quantity: {max(reorder_level, 1):g}."
        )
        # launch-readiness Phase 6 stage 6a-i: row_version/updated_at_utc
        # stamped at creation, same reasoning as retail_api.py's
        # create_category. `now` is this row's own created_at instant --
        # reused for updated_at_utc too, one instant, not two.
        conn.execute(
            "INSERT INTO reorder_requests "
            "(id, company_id, branch_id, product_id, status, draft_message, created_at, "
            "row_version, updated_at_utc) "
            "VALUES (?,?,?,?,'pending',?,?,?,?)",
            (request_id, company_id, branch_id, product_id, draft_message, now, 1, now),
        )
        _queue_reorder_sync_event(conn, request_id, company_id, branch_id, product_id,
                                   'pending', draft_message, now, 'create',
                                   row_version=1, updated_at_utc=now)
        _maybe_queue_low_stock_email(conn, company_id, product_name=product['name'] or 'Product',
                                      on_hand=on_hand, reorder_level=reorder_level,
                                      draft_message=draft_message)
        # WhatsApp -- same same-transaction/same-idempotency reasoning as the
        # email call immediately above (see this module's docstring, 2026-08-14
        # addition); a gated no-op when unconfigured, same as email.
        branch_row = conn.execute(
            "SELECT name FROM branches WHERE id=? AND company_id=?", (branch_id, company_id)
        ).fetchone()
        _whatsapp_hook.queue_low_stock_alert(
            conn, company_id=company_id, branch_id=branch_id,
            branch_name=(branch_row['name'] if branch_row else None),
            product_name=product['name'] or 'Product', on_hand=on_hand, reorder_level=reorder_level,
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def _maybe_queue_low_stock_email(conn, company_id, *, product_name, on_hand, reorder_level, draft_message):
    """Queues (never sends inline) a low-stock alert email on the SAME
    connection/transaction as the reorder_requests row just created --
    see this module's own docstring for why that gives this call its
    idempotency for free, and why this is safe to run inside the same
    try/except-rollback the caller already wraps this whole function in:
    a failure here rolls back the reorder_requests row too, which is
    correct (an email that can never be queued for a request that was
    never actually created is not a partial success worth keeping), and
    that rollback+raise is itself caught by retail_api.py::create_sale's
    own broad try/except around the whole hook (see reorder_hook.py's
    module docstring) -- so a bug here still can never touch the sale's
    response, exactly like every other failure mode in this file.

    A no-op, not an error, when notifications aren't configured/enabled or
    this company has no low_stock_recipient set -- most installs will hit
    this branch every single time, by design."""
    if not _notification_settings.is_enabled(conn, company_id):
        return
    recipient = _notification_settings.recipient_for(conn, company_id, 'low_stock_recipient')
    if not recipient:
        return
    subject = f"Low stock alert: {product_name}"
    body_text = (
        f"{product_name} has dropped to {on_hand:g} units on hand "
        f"(reorder level {reorder_level:g}).\n\n{draft_message}\n\n"
        "Review and accept/decline this reorder request in Aura Retail's Admin Center."
    )
    _EmailOutboxRepository(conn).enqueue(
        company_id=company_id, email_type='low_stock_alert', recipient=recipient,
        subject=subject, body_text=body_text,
    )


def _queue_reorder_sync_event(conn, request_id, company_id, branch_id, product_id,
                               status, draft_message, timestamp, event_type,
                               *, row_version=None, updated_at_utc=None):
    """Same INSERT shape as retail_api.py's `_queue_sync_event` -- this
    module runs outside any Flask request context (its own connection, no
    `cur` handed in from a route), so it cannot import that helper without
    creating a core/retail -> api import (backwards from every other
    dependency in this codebase); this is a deliberate, minimal duplicate
    of that one INSERT statement, not a divergent implementation.

    `row_version`/`updated_at_utc` (launch-readiness Phase 6 stage 6a-i,
    docs/launch-readiness/phase6-catalogue-correctness.md): keyword-only and
    optional so this function's one caller states them explicitly rather
    than this function guessing a value on the caller's behalf -- this is
    the CREATE site for `reorder_requests`, so the caller always has the
    freshly-inserted row's own `row_version` (1) and `updated_at_utc`
    (`now`) in hand already."""
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) VALUES (?,?,?,?,?,?)",
        (str(_uuid.uuid4()), 'reorder_request', request_id, event_type,
         json.dumps({
             'id': request_id, 'branch_id': branch_id, 'product_id': product_id,
             'status': status, 'draft_message': draft_message, 'resolved_at': None,
             'row_version': row_version, 'updated_at_utc': updated_at_utc,
         }),
         timestamp),
    )
