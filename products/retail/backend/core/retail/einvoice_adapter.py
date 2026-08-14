"""Aura Retail -- JoFotara e-invoicing document adapter (docs/einvoicing/phase1/).

Turns a completed `sales` row into the product-agnostic
commercial_runtime.einvoicing.document.EInvoiceDocument, and provides the
enqueue/reconcile hooks worker.py and the sale-completion route call. This
is the ONLY file in Retail that knows the shape of `sales`/`sale_items` on
the e-invoicing side -- commercial_runtime/einvoicing never imports
anything from here.

Buyer identification: a walk-in sale (no customer_id) has no TIN/NIN/PN on
file at all. This adapter deliberately uses document.select_buyer_id()
(returns None gracefully) rather than document.require_buyer_id() (raises)
-- a missing buyer ID must never block a routine cash sale's e-invoice
pipeline. Whether JoFotara's real ISTD profile actually requires a buyer ID
for a given transaction is a Phase 2 question, resolved once real ISTD
integration docs exist (docs/einvoicing/phase2/istd-field-mapping.md); this
is a deliberate, documented Phase 1 judgment call, not an oversight.
"""
from __future__ import annotations

from core.retail import pricing as tax_engine
from commercial_runtime.einvoicing import outbox as outbox_module
from commercial_runtime.einvoicing import sequence, settings
from commercial_runtime.einvoicing.document import (
    EInvoiceDocument, EInvoiceLine, derive_payment_type, select_buyer_id,
)


class SaleNotFoundError(ValueError):
    pass


def _fetch_buyer(conn, company_id, customer_id):
    if not customer_id:
        return 'Walk-in Customer', None
    customer = conn.execute(
        "SELECT name FROM customers WHERE id=? AND company_id=?", (customer_id, company_id)
    ).fetchone()
    buyer_name = customer['name'] if customer else 'Walk-in Customer'
    party_rows = conn.execute(
        "SELECT id_scheme, id_value FROM einvoice_party_ids "
        "WHERE company_id=? AND party_type='customer' AND party_id=?",
        (company_id, customer_id),
    ).fetchall()
    candidates = {r['id_scheme']: r['id_value'] for r in party_rows}
    return buyer_name, select_buyer_id(candidates)


def build_document(conn, outbox_row) -> EInvoiceDocument:
    """Matches commercial_runtime.einvoicing.worker.OutboxWorker's
    `document_builder(conn, row)` callback signature exactly -- `outbox_row`
    is the einvoice_outbox row the worker just claimed, which already
    carries company_id/source_type/source_id/einvoice_no/local_document_no,
    so this never needs a second lookup into einvoice_outbox itself."""
    company_id = outbox_row['company_id']
    source_type = outbox_row['source_type']
    source_id = outbox_row['source_id']
    if source_type != 'sale':
        raise ValueError(f"Retail e-invoice adapter only builds 'sale' documents, got {source_type!r}.")

    sale = conn.execute(
        "SELECT * FROM sales WHERE id=? AND company_id=?", (source_id, company_id)
    ).fetchone()
    if not sale:
        raise SaleNotFoundError(f"Sale {source_id} not found for company {company_id}.")

    item_rows = conn.execute(
        "SELECT si.quantity, si.unit_price, si.discount_pct, si.tax_rate, si.line_total, p.name AS product_name "
        "FROM sale_items si JOIN products p ON p.id = si.product_id WHERE si.sale_id=?",
        (source_id,),
    ).fetchall()

    lines = []
    for item in item_rows:
        calc = tax_engine.calculate_line(
            float(item['unit_price']), float(item['quantity']),
            float(item['discount_pct'] or 0), float(item['tax_rate'] or 0),
        )
        lines.append(EInvoiceLine(
            description=item['product_name'], quantity=float(item['quantity']),
            unit_price=float(item['unit_price']), discount_amount=calc['discount_amount'],
            tax_amount=calc['tax'], line_total=float(item['line_total']),
        ))

    buyer_name, buyer_id = _fetch_buyer(conn, company_id, sale['customer_id'])
    # Use the outbox row's OWN invoice_family, not a fresh settings read.
    # _enqueue_on_conn() reads invoice_family once and uses that single value
    # both to allocate einvoice_no (sequence.allocate_einvoice_number --
    # different families are different number series, e.g. 'INC-000042' vs
    # a 'general_sales' series) and to stamp outbox_row['invoice_family'],
    # committed together in the same BEGIN IMMEDIATE transaction. If this
    # function re-read the live setting instead, a company changing
    # invoice_family between enqueue and worker submission would submit a
    # document whose declared invoice_family disagrees with the series
    # already baked into its own einvoice_no -- see this file's module
    # docstring pattern and docs/einvoicing/phase1/invoice-numbering-audit.md
    # for why einvoice_no/invoice_family must never drift apart post-enqueue.
    invoice_family = outbox_row['invoice_family']
    currency = settings.get_setting(conn, company_id, 'currency')
    seller_name = settings.get_setting(conn, company_id, 'seller_name') or 'Aura Retail Merchant'
    seller_tin = settings.get_setting(conn, company_id, 'seller_tin')
    payment_type = derive_payment_type(sale['payment_method'], float(sale['amount_paid']), float(sale['total']))

    return EInvoiceDocument(
        company_id=company_id,
        einvoice_no=outbox_row['einvoice_no'],
        local_document_no=outbox_row['local_document_no'] or sale['sale_number'],
        invoice_family=invoice_family,
        payment_type=payment_type,
        currency=currency,
        issue_datetime=str(sale['created_at']).replace(' ', 'T'),
        seller_name=seller_name,
        seller_tin=seller_tin,
        buyer_name=buyer_name,
        buyer_id=buyer_id,
        lines=tuple(lines),
        subtotal=float(sale['subtotal']),
        discount_total=float(sale['discount_amount']),
        tax_total=float(sale['tax_amount']),
        grand_total=float(sale['total']),
    )


def _app_data_dir():
    """settings.is_enabled() needs an app_data_dir only to resolve the
    killswitch flag file -- retrieved via AURA_APP_DATA, matching every
    other module in this codebase's convention (see
    commercial_runtime/security/app_secret.py)."""
    import os
    return os.environ.get('AURA_APP_DATA', '.')


def enqueue_sale(conn_factory, *, company_id, sale_id, sale_number):
    """Called AFTER the sale's own request has already committed and
    returned its response shape (see retail_api.py::create_sale) -- opens
    its OWN independent connection via `conn_factory` rather than reusing
    the sale's connection, so this can never roll back, block, or in any
    way affect the sale transaction that already succeeded. The caller
    wraps this in a broad try/except regardless (belt and suspenders:
    checkout must be unconditionally unaffected by this feature)."""
    conn = conn_factory()
    try:
        if not settings.is_enabled(conn, _app_data_dir(), company_id):
            return False
        return _enqueue_on_conn(conn, company_id, sale_id, sale_number)
    finally:
        conn.close()


def _enqueue_on_conn(conn, company_id, sale_id, sale_number) -> bool:
    """Shared by enqueue_sale() (its own connection, one sale) and
    reconcile_missing_sales() (the worker's connection, many sales in a
    loop) -- one implementation of 'allocate a number and enqueue', never
    two copies to keep in sync."""
    invoice_family = settings.get_setting(conn, company_id, 'invoice_family')
    conn.execute("BEGIN IMMEDIATE")
    try:
        einvoice_no = sequence.allocate_einvoice_number(conn, company_id, invoice_family)
        row_id = outbox_module.OutboxRepository(conn).enqueue(
            company_id=company_id, invoice_ref=f'AURA_RETAIL:sale:{sale_id}', source_type='sale',
            source_id=sale_id, local_document_no=sale_number, einvoice_no=einvoice_no,
            invoice_family=invoice_family, payment_type='cash',  # refined by build_document at submission time
            currency=settings.get_setting(conn, company_id, 'currency'),
            provider=settings.get_setting(conn, company_id, 'provider'),
        )
        conn.commit()
        return row_id is not None
    except Exception:
        conn.rollback()
        raise


def reconcile_missing_sales(conn, company_id, enabled_at_value):
    """Finds completed sales with no outbox row at all (e.g. the process
    crashed between the sale's own commit and enqueue_sale() running) and
    enqueues them. `enabled_at_value` guarantees pre-enablement sales are
    never retroactively submitted. Runs on the worker's own connection --
    unlike enqueue_sale, this IS the worker's normal per-tick unit of work,
    so it participates in the worker's own commit cycle."""
    if not enabled_at_value:
        return

    # settings.py stores enabled_at as a UTC ISO-8601 string (e.g.
    # "2026-08-04T12:34:56.789012+00:00"), but sales.created_at is written
    # as NAIVE LOCAL time in "YYYY-MM-DD HH:MM:SS" format (see
    # retail_api.py::create_sale's `now_local` -- "Write LOCAL time, not the
    # UTC CURRENT_TIMESTAMP default"). Comparing those two strings directly
    # with SQL >= is meaningless (different format AND different timezone --
    # the literal 'T' character alone makes every real comparison false).
    # Convert to the SAME format/timezone sales.created_at actually uses
    # before it ever reaches SQL.
    from datetime import datetime
    enabled_at_local = datetime.fromisoformat(enabled_at_value).astimezone().strftime('%Y-%m-%d %H:%M:%S')

    rows = conn.execute(
        "SELECT s.id, s.sale_number FROM sales s "
        "LEFT JOIN einvoice_outbox o ON o.source_type='sale' AND o.source_id = s.id AND o.company_id = s.company_id "
        "WHERE s.company_id=? AND s.status='completed' AND s.created_at >= ? AND o.id IS NULL "
        "ORDER BY s.id LIMIT 200",
        (company_id, enabled_at_local),
    ).fetchall()
    for row in rows:
        _enqueue_on_conn(conn, company_id, row['id'], row['sale_number'])
