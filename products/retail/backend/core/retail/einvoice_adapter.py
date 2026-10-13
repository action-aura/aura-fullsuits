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
    EInvoiceDocument, EInvoiceLine, derive_payment_type, document_uuid,
    icv_from_einvoice_no, istd_type_name, select_buyer_id, tax_category_for_rate,
)
from commercial_runtime.einvoicing.istd_ubl import PROFILE_ID_ISTD


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
    if source_type == 'return':
        return _build_return_document(conn, outbox_row)
    if source_type != 'sale':
        raise ValueError(f"Retail e-invoice adapter only builds 'sale'/'return' documents, got {source_type!r}.")

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

    # Read the company's configured tax_calculation_mode the SAME way
    # retail_api.py::create_sale() did when it originally priced this sale
    # (SELECT against retail_settings -- see that module's _settings()) and
    # schema.py's _seed_retail demo seeder does for the identical lookup.
    # Without this, calculate_line() below silently falls back to its own
    # DEFAULT_MODE (TAX_AFTER_DISCOUNT), so a company configured for
    # TAX_BEFORE_DISCOUNT would get EInvoiceLine.discount_amount/tax_amount
    # that disagree with what the sale actually charged. core/retail must
    # not import api.retail_api's _settings() to get this (api/ already
    # imports THIS module -- see enqueue_sale's callers -- so the reverse
    # import would invert that layering), hence the direct query here
    # instead, wrapped the same defensive way schema.py's copy is (a
    # brand-new company whose retail_settings table row doesn't exist yet
    # must still build a valid, if default-mode, document).
    try:
        _mode_row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'",
            (company_id,),
        ).fetchone()
        tax_mode = tax_engine.normalize_mode(_mode_row[0] if _mode_row else None)
    except Exception:
        tax_mode = tax_engine.DEFAULT_MODE  # retail_settings not created yet (fresh install) -- use the default

    # Read once, ahead of the per-line loop, and pass into every calculate_
    # line() call below. Without this, calculate_line() silently defaulted
    # to a hardcoded 2dp quantum (currency=None) even for a JOD company,
    # so EInvoiceLine.discount_amount/.tax_amount would round to the wrong
    # precision for a 3-decimal-currency shop -- the same class of bug this
    # module's tax_mode lookup above already guards against, just on the
    # currency axis instead of the tax-mode axis. `ubl.py::_line_xml`
    # currently renders neither field (only line_total/unit_price), so this
    # was harmless to the actually-filed XML until the day something reads
    # them -- fixed now rather than left as a landmine.
    currency = settings.get_setting(conn, company_id, 'currency')

    lines = []
    for item in item_rows:
        calc = tax_engine.calculate_line(
            float(item['unit_price']), float(item['quantity']),
            float(item['discount_pct'] or 0), float(item['tax_rate'] or 0),
            mode=tax_mode, currency=currency,
        )
        lines.append(EInvoiceLine(
            description=item['product_name'], quantity=float(item['quantity']),
            unit_price=float(item['unit_price']), discount_amount=calc['discount_amount'],
            tax_amount=calc['tax'], line_total=float(item['line_total']),
            tax_rate=float(item['tax_rate'] or 0), tax_category=tax_category_for_rate(item['tax_rate']),
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
    # `currency` already read above, ahead of the per-line calculate_line()
    # loop -- reused here rather than queried a second time.
    seller_name = settings.get_setting(conn, company_id, 'seller_name') or 'Aura Retail Merchant'
    seller_tin = settings.get_setting(conn, company_id, 'seller_tin')
    payment_type = derive_payment_type(sale['payment_method'], float(sale['amount_paid']), float(sale['total']))

    istd = _istd_fields(conn, company_id, outbox_row, invoice_family, payment_type)
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
        **istd,
    )

def _istd_fields(conn, company_id, outbox_row, invoice_family, payment_type, **extra) -> dict:
    """The ISTD-profile fields shared by invoices and credit notes.

    The ISTD profile is selected when the shop's provider is `direct_istd`
    (and always for credit notes, which have no generic form). Every other
    install keeps rendering the generic profile exactly as before.

    UUID and ICV are DERIVED, not stored (no schema change): the UUID is a
    uuid5 of the outbox row's idempotency key, so it is identical on every
    retry; the ICV is the number minted by the dedicated einvoice_sequence --
    never the shop's local document number (invoice-numbering-audit.md)."""
    use_istd = (
        outbox_row['source_type'] == 'return'
        or settings.get_setting(conn, company_id, 'provider') == 'direct_istd'
    )
    if not use_istd:
        return {}
    fields = dict(
        profile=PROFILE_ID_ISTD,
        uuid=document_uuid(company_id, outbox_row['invoice_ref']),
        icv=icv_from_einvoice_no(outbox_row['einvoice_no']),
        type_name=istd_type_name(payment_type, invoice_family),
        activity_code=settings.get_setting(conn, company_id, 'seller_activity_code'),
    )
    fields.update(extra)
    return fields


def _sale_payment_type(sale) -> str:
    return derive_payment_type(sale['payment_method'], float(sale['amount_paid']), float(sale['total']))


def _tax_mode_and_currency(conn, company_id):
    try:
        row = conn.execute(
            "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='tax_calculation_mode'",
            (company_id,),
        ).fetchone()
        mode = tax_engine.normalize_mode(row[0] if row else None)
    except Exception:
        mode = tax_engine.DEFAULT_MODE
    return mode, settings.get_setting(conn, company_id, 'currency')


def _original_outbox_row(conn, company_id, sale_id):
    return conn.execute(
        "SELECT invoice_ref, einvoice_no, invoice_family FROM einvoice_outbox "
        "WHERE company_id=? AND source_type='sale' AND source_id=?",
        (company_id, sale_id),
    ).fetchone()


def _build_return_document(conn, outbox_row) -> EInvoiceDocument:
    """A credit note (381) for one return, referencing the ORIGINAL sale's
    e-invoice number and UUID.

    Lines are recomputed from the returned quantity against the original
    sale line with the sale's own tax mode -- the same recompute create_
    return() uses for the refund -- so tax and discount reverse exactly and
    the buyer / type name always match the original (a guide requirement)."""
    company_id = outbox_row['company_id']
    ret = conn.execute("SELECT * FROM returns WHERE id=? AND company_id=?",
                       (outbox_row['source_id'], company_id)).fetchone()
    if not ret:
        raise SaleNotFoundError(f"Return {outbox_row['source_id']} not found for company {company_id}.")
    sale = conn.execute("SELECT * FROM sales WHERE id=? AND company_id=?",
                        (ret['sale_id'], company_id)).fetchone()
    original = _original_outbox_row(conn, company_id, ret['sale_id'])
    if not sale or not original:
        raise SaleNotFoundError(f"Return {ret['id']}: its sale was never e-invoiced, so there is nothing to credit.")

    mode, currency = _tax_mode_and_currency(conn, company_id)
    lines, total = [], 0.0
    for ri in conn.execute(
        "SELECT product_id, quantity, sale_item_id FROM return_items WHERE return_id=? ORDER BY id", (ret['id'],)
    ).fetchall():
        if ri['sale_item_id']:
            si = conn.execute(
                "SELECT si.unit_price, si.discount_pct, si.tax_rate, p.name AS product_name FROM sale_items si "
                "JOIN products p ON p.id=si.product_id WHERE si.id=? AND si.sale_id=?",
                (ri['sale_item_id'], sale['id'])).fetchone()
        else:
            si = conn.execute(
                "SELECT si.unit_price, si.discount_pct, si.tax_rate, p.name AS product_name FROM sale_items si "
                "JOIN products p ON p.id=si.product_id WHERE si.sale_id=? AND si.product_id=? ORDER BY si.id LIMIT 1",
                (sale['id'], ri['product_id'])).fetchone()
        if not si:
            raise SaleNotFoundError(f"Return {ret['id']}: no matching sale line for product {ri['product_id']}.")
        calc = tax_engine.calculate_line(
            float(si['unit_price']), float(ri['quantity']), float(si['discount_pct'] or 0),
            float(si['tax_rate'] or 0), mode=mode, currency=currency)
        total += calc['total']
        lines.append(EInvoiceLine(
            description=si['product_name'], quantity=float(ri['quantity']), unit_price=float(si['unit_price']),
            discount_amount=calc['discount_amount'], tax_amount=calc['tax'], line_total=calc['taxable_amount'],
            tax_rate=float(si['tax_rate'] or 0), tax_category=tax_category_for_rate(si['tax_rate'])))

    buyer_name, buyer_id = _fetch_buyer(conn, company_id, sale['customer_id'])
    payment_type = _sale_payment_type(sale)
    family = original['invoice_family']
    subtotal = sum(l.quantity * l.unit_price for l in lines)
    discount_total = sum(l.discount_amount for l in lines)
    tax_total = sum(l.tax_amount for l in lines)
    istd = _istd_fields(
        conn, company_id, outbox_row, family, payment_type,
        document_kind='credit_note',
        original_einvoice_no=original['einvoice_no'],
        original_uuid=document_uuid(company_id, original['invoice_ref']),
        original_total=float(sale['total']),
        # The guide makes the reason mandatory; a till return with no typed
        # reason must still file, so the fallback is a plain statement of fact.
        return_reason=(ret['reason'] or '').strip() or 'Goods returned',
    )
    return EInvoiceDocument(
        company_id=company_id, einvoice_no=outbox_row['einvoice_no'],
        local_document_no=outbox_row['local_document_no'] or ret['return_number'],
        invoice_family=family, payment_type=payment_type, currency=currency,
        issue_datetime=str(ret['created_at']).replace(' ', 'T'),
        seller_name=settings.get_setting(conn, company_id, 'seller_name') or 'Aura Retail Merchant',
        seller_tin=settings.get_setting(conn, company_id, 'seller_tin'),
        buyer_name=buyer_name, buyer_id=buyer_id, lines=tuple(lines),
        subtotal=subtotal, discount_total=discount_total, tax_total=tax_total, grand_total=total,
        **istd,
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


def enqueue_return(conn_factory, *, company_id, return_id, return_number):
    """Queue a credit note (381) for a return of an e-invoiced sale. Same
    contract as enqueue_sale: opt-in (returns False without touching the
    sequence when e-invoicing is off), own connection, never raises into the
    caller's return. A return of a sale that was never e-invoiced (sold
    before enablement, or while off) gets NO credit note: there is no
    original number/UUID to reference, and minting one would credit a
    document the tax authority has never seen."""
    conn = conn_factory()
    try:
        if not settings.is_enabled(conn, _app_data_dir(), company_id):
            return False
        return _enqueue_return_on_conn(conn, company_id, return_id, return_number)
    finally:
        conn.close()


def _enqueue_return_on_conn(conn, company_id, return_id, return_number) -> bool:
    ret = conn.execute("SELECT sale_id FROM returns WHERE id=? AND company_id=?", (return_id, company_id)).fetchone()
    original = _original_outbox_row(conn, company_id, ret['sale_id']) if ret and ret['sale_id'] else None
    if not original:
        return False
    # A credit note takes its number from the SAME series as the invoice it
    # reverses, so ICV keeps climbing across invoices and credit notes alike.
    family = original['invoice_family']
    conn.execute("BEGIN IMMEDIATE")
    try:
        einvoice_no = sequence.allocate_einvoice_number(conn, company_id, family)
        row_id = outbox_module.OutboxRepository(conn).enqueue(
            company_id=company_id, invoice_ref=f'AURA_RETAIL:return:{return_id}', source_type='return',
            source_id=return_id, local_document_no=return_number, einvoice_no=einvoice_no,
            invoice_family=family, payment_type='cash',  # refined by build_document at submission time
            currency=settings.get_setting(conn, company_id, 'currency'),
            provider=settings.get_setting(conn, company_id, 'provider'),
        )
        conn.commit()
        return row_id is not None
    except Exception:
        conn.rollback()
        raise


def reconcile_missing_returns(conn, company_id, enabled_at_value):
    """Credit notes for completed returns that have none yet. This is what
    wires returns into the outbox WITHOUT a call inside create_return():
    the worker's reconcile pass picks up every return of an e-invoiced sale
    (only those -- see enqueue_return) made since e-invoicing was enabled."""
    if not enabled_at_value:
        return
    from datetime import datetime
    enabled_at_local = datetime.fromisoformat(enabled_at_value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
    rows = conn.execute(
        "SELECT r.id, r.return_number FROM returns r "
        "JOIN einvoice_outbox so ON so.source_type='sale' AND so.source_id=r.sale_id AND so.company_id=r.company_id "
        "LEFT JOIN einvoice_outbox o ON o.source_type='return' AND o.source_id=r.id AND o.company_id=r.company_id "
        "WHERE r.company_id=? AND r.status='completed' AND r.created_at >= ? AND o.id IS NULL "
        "ORDER BY r.id LIMIT 200",
        (company_id, enabled_at_local),
    ).fetchall()
    for row in rows:
        _enqueue_return_on_conn(conn, company_id, row['id'], row['return_number'])


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
    # Returns of e-invoiced sales become credit notes on the same pass.
    reconcile_missing_returns(conn, company_id, enabled_at_value)
