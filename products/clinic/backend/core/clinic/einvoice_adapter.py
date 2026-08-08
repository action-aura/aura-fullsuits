"""Aura Clinic -- JoFotara e-invoicing document adapter (docs/einvoicing/phase1/).

Mirrors products/retail/backend/core/retail/einvoice_adapter.py's shape --
see that file's module docstring for the full rationale (buyer-ID handling,
why enqueue opens its own connection, why reconciliation exists). This file
is the only place in Clinic that knows the shape of
clinic_invoices/clinic_invoice_items/clinic_patients on the e-invoicing
side.

Two real differences from Retail, both because Clinic's schema is
genuinely different, not because the design differs:

  1. clinic_invoice_items has no discount_pct/tax_rate per line (discount
     and tax are invoice-level only on clinic_invoices) -- line-level
     discount_amount/tax_amount are set to 0 here; the real, authoritative
     figures are the invoice-level subtotal/discount/tax/total already
     computed by clinic_api.py::create_invoice, carried at the document
     level. ubl.py's line serializer doesn't render per-line tax/discount
     anyway (see that module), so this has no effect on the generated
     document.
  2. clinic_invoices.created_at uses SQLite's CURRENT_TIMESTAMP default,
     which is UTC (see products/clinic/backend/database/schema.py) --
     unlike Retail's explicit LOCAL `now_local`. The reconciliation
     timestamp comparison below converts enabled_at to UTC-without-
     timezone-suffix accordingly, NOT to local time -- using Retail's
     conversion here would silently break the comparison by the local UTC
     offset. See products/retail/backend/core/retail/einvoice_adapter.py's
     reconcile_missing_sales docstring for the bug this class of mismatch
     caused there.
"""
from __future__ import annotations

from commercial_runtime.einvoicing import outbox as outbox_module
from commercial_runtime.einvoicing import sequence, settings
from commercial_runtime.einvoicing.document import (
    EInvoiceDocument, EInvoiceLine, derive_payment_type, select_buyer_id,
)


class InvoiceNotFoundError(ValueError):
    pass


def _fetch_buyer(conn, company_id, patient_id):
    if not patient_id:
        return 'Walk-in Patient', None
    patient = conn.execute(
        "SELECT name FROM clinic_patients WHERE id=? AND company_id=?", (patient_id, company_id)
    ).fetchone()
    buyer_name = patient['name'] if patient else 'Walk-in Patient'
    party_rows = conn.execute(
        "SELECT id_scheme, id_value FROM einvoice_party_ids "
        "WHERE company_id=? AND party_type='patient' AND party_id=?",
        (company_id, patient_id),
    ).fetchall()
    candidates = {r['id_scheme']: r['id_value'] for r in party_rows}
    return buyer_name, select_buyer_id(candidates)


def build_document(conn, outbox_row) -> EInvoiceDocument:
    """Matches OutboxWorker's `document_builder(conn, row)` callback
    signature -- see the Retail adapter's identical function for why."""
    company_id = outbox_row['company_id']
    source_type = outbox_row['source_type']
    source_id = outbox_row['source_id']
    if source_type != 'clinic_invoice':
        raise ValueError(f"Clinic e-invoice adapter only builds 'clinic_invoice' documents, got {source_type!r}.")

    invoice = conn.execute(
        "SELECT * FROM clinic_invoices WHERE id=? AND company_id=?", (source_id, company_id)
    ).fetchone()
    if not invoice:
        raise InvoiceNotFoundError(f"Clinic invoice {source_id} not found for company {company_id}.")

    item_rows = conn.execute(
        "SELECT description, qty, unit_price, line_total FROM clinic_invoice_items WHERE invoice_id=?",
        (source_id,),
    ).fetchall()

    lines = [
        EInvoiceLine(
            description=item['description'] or 'Clinic service', quantity=float(item['qty']),
            unit_price=float(item['unit_price']), discount_amount=0.0, tax_amount=0.0,
            line_total=float(item['line_total']),
        )
        for item in item_rows
    ]

    buyer_name, buyer_id = _fetch_buyer(conn, company_id, invoice['patient_id'])
    invoice_family = settings.get_setting(conn, company_id, 'invoice_family')
    currency = settings.get_setting(conn, company_id, 'currency')
    seller_name = settings.get_setting(conn, company_id, 'seller_name') or 'Aura Clinic Merchant'
    seller_tin = settings.get_setting(conn, company_id, 'seller_tin')
    payment_type = derive_payment_type('cash', float(invoice['amount_paid'] or 0), float(invoice['total']))

    return EInvoiceDocument(
        company_id=company_id,
        einvoice_no=outbox_row['einvoice_no'],
        local_document_no=outbox_row['local_document_no'] or invoice['invoice_number'],
        invoice_family=invoice_family,
        payment_type=payment_type,
        currency=currency,
        issue_datetime=str(invoice['created_at']).replace(' ', 'T'),
        seller_name=seller_name,
        seller_tin=seller_tin,
        buyer_name=buyer_name,
        buyer_id=buyer_id,
        lines=tuple(lines),
        subtotal=float(invoice['subtotal']),
        discount_total=float(invoice['discount'] or 0),
        tax_total=float(invoice['tax']),
        grand_total=float(invoice['total']),
    )


def _app_data_dir():
    import os
    return os.environ.get('AURA_APP_DATA', '.')


def enqueue_invoice(conn_factory, *, company_id, invoice_id, invoice_number):
    """Called AFTER the invoice's own request has already committed (see
    clinic_api.py::create_invoice) -- opens its OWN independent connection,
    exactly like Retail's enqueue_sale, so this can never affect the
    invoice transaction that already succeeded."""
    conn = conn_factory()
    try:
        if not settings.is_enabled(conn, _app_data_dir(), company_id):
            return False
        return _enqueue_on_conn(conn, company_id, invoice_id, invoice_number)
    finally:
        conn.close()


def _enqueue_on_conn(conn, company_id, invoice_id, invoice_number) -> bool:
    invoice_family = settings.get_setting(conn, company_id, 'invoice_family')
    conn.execute("BEGIN IMMEDIATE")
    try:
        einvoice_no = sequence.allocate_einvoice_number(conn, company_id, invoice_family)
        row_id = outbox_module.OutboxRepository(conn).enqueue(
            company_id=company_id, invoice_ref=f'AURA_CLINIC:clinic_invoice:{invoice_id}', source_type='clinic_invoice',
            source_id=invoice_id, local_document_no=invoice_number, einvoice_no=einvoice_no,
            invoice_family=invoice_family, payment_type='cash',  # refined by build_document at submission time
            currency=settings.get_setting(conn, company_id, 'currency'),
            provider=settings.get_setting(conn, company_id, 'provider'),
        )
        conn.commit()
        return row_id is not None
    except Exception:
        conn.rollback()
        raise


def reconcile_missing_invoices(conn, company_id, enabled_at_value):
    """Finds clinic invoices with no outbox row at all and enqueues them.
    See this module's docstring point 2 for why the timestamp conversion
    below is UTC, not local -- clinic_invoices.created_at is SQLite's
    CURRENT_TIMESTAMP default (UTC), unlike Retail's explicit local time."""
    if not enabled_at_value:
        return

    from datetime import datetime
    enabled_at_utc = datetime.fromisoformat(enabled_at_value).strftime('%Y-%m-%d %H:%M:%S')

    rows = conn.execute(
        "SELECT i.id, i.invoice_number FROM clinic_invoices i "
        "LEFT JOIN einvoice_outbox o ON o.source_type='clinic_invoice' AND o.source_id = i.id AND o.company_id = i.company_id "
        "WHERE i.company_id=? AND i.created_at >= ? AND o.id IS NULL "
        "ORDER BY i.id LIMIT 200",
        (company_id, enabled_at_utc),
    ).fetchall()
    for row in rows:
        _enqueue_on_conn(conn, company_id, row['id'], row['invoice_number'])
