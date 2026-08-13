"""Phase 9.5D Milestone 8 -- Sales Order domain service.

Built directly on the existing Phase 9.5A SalesOrder/SalesOrderLine
schema (app/models/commercial_sales.py) -- no new model. Orders are
created only from an accepted, eligible Quote (Non-Negotiable Rule 2:
Order is not an Invoice; Rule 13: Order requires a confirmed Customer).
See docs/owner/phase9_5d/sales-order-domain-contract.md,
order-fulfillment-intent-contract.md.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.errors import SALES_ORDER_TRANSITIONS, CommercialSalesError
from app.commercial_sales.lead_quote_boundary import resolve_customer_for_accepted_quote
from app.commercial_sales.numbering import allocate_document_number
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import (
    CommercialOperationsIdempotencyKey,
    Quote,
    QuoteLine,
    SalesOrder,
    SalesOrderLine,
)

OPERATION_CODE = "SALES_ORDER_CREATE_FROM_QUOTE"


def create_order_from_quote(
    quote: Quote,
    *,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    idempotency_key: str,
) -> SalesOrder:
    existing_key = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == OPERATION_CODE,
        )
    ).scalars().first()
    if existing_key is not None:
        replayed_order = db_session.get(SalesOrder, existing_key.result_reference_id)
        if replayed_order is None or replayed_order.quote_id != quote.id:
            raise CommercialSalesError("IDEMPOTENCY_CONFLICT")
        return replayed_order

    if quote.status != "ACCEPTED":
        raise CommercialSalesError("QUOTE_NOT_ACCEPTED")

    # One active Order per accepted Quote (Milestone 8's own rule) --
    # checked before the idempotency-key path above even applies (a
    # second *different* idempotency key against the same Quote must
    # still be rejected, not silently create a second Order).
    existing_order = db_session.execute(
        select(SalesOrder).where(SalesOrder.quote_id == quote.id, SalesOrder.status != "CANCELLED")
    ).scalars().first()
    if existing_order is not None:
        raise CommercialSalesError("IDEMPOTENCY_CONFLICT")

    # Non-Negotiable Rule 13: a confirmed Customer is required before an
    # Order exists -- resolves (converts, if Lead-based) via the
    # Milestone 7 boundary, never re-implemented here.
    customer = resolve_customer_for_accepted_quote(
        quote, actor_staff_user_id=actor_staff_user_id, idempotency_key=f"{idempotency_key}-boundary"
    )

    quote_lines = db_session.execute(
        select(QuoteLine).where(QuoteLine.quote_id == quote.id).order_by(QuoteLine.sort_order)
    ).scalars().all()

    order = SalesOrder(
        quote_id=quote.id,
        customer_id=customer.id,
        created_by_employee_profile_id=actor_employee_profile_id,
        status="DRAFT",
        order_number=allocate_document_number("SALES_ORDER"),
        currency=quote.currency,
        total=quote.total,
        version=1,
    )
    db_session.add(order)
    db_session.flush()

    for ql in quote_lines:
        db_session.add(
            SalesOrderLine(
                sales_order_id=order.id,
                plan_id=ql.plan_id,
                addon_id=ql.addon_id,
                price_version_id=ql.price_version_id,
                description=ql.description,
                quantity=ql.quantity,
                unit_price=ql.unit_price,
                overridden_unit_price=ql.overridden_unit_price,
                override_reason=ql.override_reason,
                discount_amount=ql.discount_amount,
                line_total=ql.line_total,
                sort_order=ql.sort_order,
            )
        )

    db_session.add(
        CommercialOperationsIdempotencyKey(
            idempotency_key=idempotency_key,
            operation_code=OPERATION_CODE,
            result_reference_id=order.id,
        )
    )
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="ORDER_CREATED",
        entity_type="sales_order",
        entity_public_id=str(order.id),
        after_state={"order_number": order.order_number, "quote_id": str(quote.id), "customer_id": str(customer.id)},
    )
    return order


def _check_transition(status: str, target: str) -> None:
    if target not in SALES_ORDER_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_ORDER_TRANSITION", from_status=status, to_status=target)


def _check_version(order: SalesOrder, expected_version: int | None) -> None:
    if expected_version is not None and order.version != expected_version:
        raise CommercialSalesError("STALE_VERSION")


def confirm_order(order: SalesOrder, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None) -> SalesOrder:
    _check_version(order, expected_version)
    _check_transition(order.status, "CONFIRMED")

    order.status = "CONFIRMED"
    order.confirmed_at = utcnow()
    order.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="ORDER_CONFIRMED",
        entity_type="sales_order",
        entity_public_id=str(order.id),
        before_state={"status": "DRAFT"},
        after_state={"status": "CONFIRMED"},
    )
    return order


def cancel_order(
    order: SalesOrder, *, reason: str, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> SalesOrder:
    _check_version(order, expected_version)
    _check_transition(order.status, "CANCELLED")
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")

    before_status = order.status
    order.status = "CANCELLED"
    order.cancelled_at = utcnow()
    order.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="ORDER_CANCELLED",
        entity_type="sales_order",
        entity_public_id=str(order.id),
        reason=reason,
        before_state={"status": before_status},
        after_state={"status": "CANCELLED"},
    )
    return order
