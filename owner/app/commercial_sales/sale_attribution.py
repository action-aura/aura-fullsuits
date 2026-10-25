"""Who sold a payment's sale -- read-only, models-only helpers.

Step 0 (2026-10-24, owner rule "nobody confirms their own sale"). Kept in
its own module that imports nothing but models, so the payment STORAGE
layer (app/subscriptions/services.py::correct_payment, the one writer of
PaymentRecord.status) can ask "who is this payment's seller?" without an
import cycle (app.commercial_sales.payments already imports that module).

A payment belongs to a sale in one of two ways:
  - a one-screen sale (app/commercial_sales/direct_sale.py) links its
    payment to its SalesOrder through the shared ledger
    owner_commercial_operations_idempotency_keys: idempotency_key =
    str(payment.id), operation_code = DIRECT_SALE_PAYMENT_LINK,
    result_reference_id = order.id. The (key, operation) unique constraint
    makes it one order per payment. A ledger row instead of a new column:
    no schema change was needed for it;
  - a pipeline payment that names its commercial invoice
    (PaymentRecord.commercial_invoice_id).
Either way the seller is the EmployeeProfile the commission ledger credits
(commissions/ledger.py credits invoice.created_by_employee_profile_id, and
a one-screen sale's invoice is created for its order's creator).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.extensions import db_session
from app.models.commercial_sales import CommercialInvoice, CommercialOperationsIdempotencyKey, SalesOrder
from app.models.employees import EmployeeProfile

DIRECT_SALE_PAYMENT_LINK = "DIRECT_SALE_PAYMENT"


def direct_sale_order_id_for_payment(payment_id: uuid.UUID) -> uuid.UUID | None:
    row = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == str(payment_id),
            CommercialOperationsIdempotencyKey.operation_code == DIRECT_SALE_PAYMENT_LINK,
        )
    ).scalars().first()
    return row.result_reference_id if row is not None else None


def direct_sale_payment_id_for_order(order_id: uuid.UUID) -> uuid.UUID | None:
    row = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.operation_code == DIRECT_SALE_PAYMENT_LINK,
            CommercialOperationsIdempotencyKey.result_reference_id == order_id,
        )
    ).scalars().first()
    return uuid.UUID(row.idempotency_key) if row is not None else None


def sale_owner_profile_id_for_payment(payment) -> uuid.UUID | None:
    order_id = direct_sale_order_id_for_payment(payment.id)
    if order_id is not None:
        order = db_session.get(SalesOrder, order_id)
        return order.created_by_employee_profile_id if order is not None else None
    if payment.commercial_invoice_id is not None:
        invoice = db_session.get(CommercialInvoice, payment.commercial_invoice_id)
        return invoice.created_by_employee_profile_id if invoice is not None else None
    return None


def sale_owner_staff_user_id_for_payment(payment) -> uuid.UUID | None:
    """The staff account of the payment's seller, or None when the payment
    belongs to no sale and to no subscription with a sales owner.

    Step 0 ladder (2026-10-24): a later payment of a subscription -- a
    monthly package's 2nd, 3rd... payment, or a renewal -- belongs to the
    subscription's sales owner. It is what counts their monthly sale on the
    ladder (the 3rd payment of Package 2) or earns their 10 JOD renewal, so
    "nobody confirms their own sale" covers it too. Measured on 1025d626:
    such a payment had no seller, and the seller could confirm it."""
    profile_id = sale_owner_profile_id_for_payment(payment)
    if profile_id is None:
        subscription_id = getattr(payment, "subscription_id", None)
        if subscription_id is None:
            return None
        from app.models.subscriptions import Subscription

        subscription = db_session.get(Subscription, subscription_id)
        return subscription.sales_owner_staff_user_id if subscription is not None else None
    profile = db_session.get(EmployeeProfile, profile_id)
    return profile.staff_user_id if profile is not None else None
