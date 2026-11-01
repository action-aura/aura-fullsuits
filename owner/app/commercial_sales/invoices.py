"""Phase 9.5D Milestone 9 -- Commercial Invoice domain service.

Built directly on the existing Phase 9.5A CommercialInvoice/
CommercialInvoiceItem schema -- no new model. Explicitly operational
commercial documents, never described as government-certified tax
invoices / legally compliant fiscal invoices / Jordanian e-invoices /
accounting journal entries (Non-Negotiable Rule 15). See
docs/owner/phase9_5d/commercial-invoice-domain-contract.md,
invoice-status-and-balance-contract.md.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.calculator import GENERAL_SALES_TAX_RATE, split_tax_inclusive
from app.commercial_sales.errors import INVOICE_TRANSITIONS, CommercialSalesError
from app.commercial_sales.numbering import allocate_document_number
from app.extensions import db_session
from app.models.base import utcnow
from app.operational_reports.periods import jordan_today
from app.models.commercial_sales import (
    CommercialInvoice,
    CommercialInvoiceItem,
    CommercialOperationsIdempotencyKey,
    PaymentAllocation,
    SalesOrder,
    SalesOrderLine,
)

OPERATION_CODE = "INVOICE_CREATE_FROM_ORDER"
DEFAULT_DUE_DAYS = 30


def create_invoice_from_order(
    order: SalesOrder,
    *,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    idempotency_key: str,
    due_date: date | None = None,
) -> CommercialInvoice:
    existing_key = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == OPERATION_CODE,
        )
    ).scalars().first()
    if existing_key is not None:
        replayed = db_session.get(CommercialInvoice, existing_key.result_reference_id)
        if replayed is None or replayed.sales_order_id != order.id:
            raise CommercialSalesError("IDEMPOTENCY_CONFLICT")
        return replayed

    if order.status != "CONFIRMED":
        raise CommercialSalesError("INVALID_ORDER_TRANSITION", from_status=order.status, to_status="CONFIRMED")

    existing_invoice = db_session.execute(
        select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID")
    ).scalars().first()
    if existing_invoice is not None:
        raise CommercialSalesError("IDEMPOTENCY_CONFLICT")

    order_lines = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id).order_by(SalesOrderLine.sort_order)
    ).scalars().all()

    # Tax-inclusive prices (accountant decision 2026-11-01): every Aura price
    # already contains the 16% general sales tax, so the invoice SHOWS the tax
    # inside the total and never adds it. WHY tax_total is a memo and not an
    # addend: subtotal stays the gross (the sum of the tax-inclusive lines, ==
    # total), so total == subtotal - discount_total as it always was and
    # commissions/ledger.py::_tax_excluded_base -- which reads
    # (subtotal - discount_total) / total and the extra-device share of
    # subtotal -- computes exactly what it did before. Storing net as subtotal
    # would have cut every seller's commission by 13.8% (the owner's rule:
    # commission follows the price paid, unchanged). The net is total -
    # tax_total, derived where it is shown (invoice_tax_breakdown).
    _net, included_tax = split_tax_inclusive(order.total)
    invoice = CommercialInvoice(
        sales_order_id=order.id,
        customer_id=order.customer_id,
        created_by_employee_profile_id=actor_employee_profile_id,
        status="DRAFT",
        invoice_number=allocate_document_number("COMMERCIAL_INVOICE"),
        currency=order.currency,
        subtotal=order.total,
        discount_total=Decimal("0.00"),
        tax_total=included_tax,
        total=order.total,
        due_date=due_date,
        version=1,
    )
    db_session.add(invoice)
    db_session.flush()

    for ol in order_lines:
        db_session.add(
            CommercialInvoiceItem(
                commercial_invoice_id=invoice.id,
                plan_id=ol.plan_id,
                addon_id=ol.addon_id,
                price_version_id=ol.price_version_id,
                description=ol.description,
                quantity=ol.quantity,
                unit_price=ol.unit_price,
                overridden_unit_price=ol.overridden_unit_price,
                override_reason=ol.override_reason,
                discount_amount=ol.discount_amount,
                line_total=ol.line_total,
                sort_order=ol.sort_order,
            )
        )

    db_session.add(
        CommercialOperationsIdempotencyKey(
            idempotency_key=idempotency_key, operation_code=OPERATION_CODE, result_reference_id=invoice.id
        )
    )
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="INVOICE_CREATED",
        entity_type="commercial_invoice",
        entity_public_id=str(invoice.id),
        after_state={"invoice_number": invoice.invoice_number, "sales_order_id": str(order.id), "total": str(invoice.total)},
    )
    return invoice


def invoice_tax_breakdown(invoice: CommercialInvoice) -> dict:
    """What an invoice shows about its tax: {net, tax, total, rate_label}, the
    tax INSIDE the total (see create_invoice_from_order). An invoice made
    before tax was recorded has tax_total 0 but was priced tax-inclusive just
    the same, so its breakdown is computed here on the fly from the total with
    the same helper; the stored row is never touched. net + tax == total
    always, because tax is the remainder."""
    total = Decimal(invoice.total)
    stored_tax = Decimal(invoice.tax_total)
    tax = stored_tax if stored_tax > 0 else split_tax_inclusive(total)[1]
    return {
        "net": total - tax,
        "tax": tax,
        "total": total,
        "rate_label": f"{(GENERAL_SALES_TAX_RATE * 100).normalize():f}%",
    }


def _check_transition(status: str, target: str) -> None:
    if target not in INVOICE_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_INVOICE_TRANSITION", from_status=status, to_status=target)


def _check_version(invoice: CommercialInvoice, expected_version: int | None) -> None:
    if expected_version is not None and invoice.version != expected_version:
        raise CommercialSalesError("STALE_VERSION")


def issue_invoice(
    invoice: CommercialInvoice, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> CommercialInvoice:
    _check_version(invoice, expected_version)
    _check_transition(invoice.status, "ISSUED")

    invoice.status = "ISSUED"
    invoice.issued_at = utcnow()
    if invoice.due_date is None:
        invoice.due_date = jordan_today() + timedelta(days=DEFAULT_DUE_DAYS)  # Jordan days
    invoice.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="INVOICE_ISSUED",
        entity_type="commercial_invoice",
        entity_public_id=str(invoice.id),
        before_state={"status": "DRAFT"},
        after_state={"status": "ISSUED", "due_date": str(invoice.due_date)},
    )
    return invoice


def confirmed_allocated_amount(invoice: CommercialInvoice) -> Decimal:
    """Sum of non-reversed PaymentAllocation rows against this invoice --
    Milestone 11's authoritative source for 'has this invoice received
    any money.' Returns 0 before Milestone 10/11 exist in practice (no
    allocation can be created without a confirmed Payment), which is
    exactly correct, not a placeholder."""
    total = db_session.execute(
        select(PaymentAllocation.allocated_amount).where(
            PaymentAllocation.commercial_invoice_id == invoice.id, PaymentAllocation.reversed_at.is_(None)
        )
    ).scalars().all()
    return sum(total, Decimal("0.00"))


def void_invoice(
    invoice: CommercialInvoice, *, reason: str, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> CommercialInvoice:
    _check_version(invoice, expected_version)
    _check_transition(invoice.status, "VOID")
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    if confirmed_allocated_amount(invoice) > 0:
        raise CommercialSalesError("INVALID_INVOICE_TRANSITION", from_status=invoice.status, to_status="VOID")

    before_status = invoice.status
    invoice.status = "VOID"
    invoice.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="INVOICE_VOIDED",
        entity_type="commercial_invoice",
        entity_public_id=str(invoice.id),
        reason=reason,
        before_state={"status": before_status},
        after_state={"status": "VOID"},
    )
    return invoice
