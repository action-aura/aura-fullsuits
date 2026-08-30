"""Phase 9.5D Milestone 13 -- commercial fulfillment orchestration.

The single most severe risk in this phase (Non-Negotiable Principle 5):
this module NEVER inserts Subscription/License/Installation rows
directly. Every mutation goes through the existing canonical services
(app/subscriptions/services.py::create_subscription/transition_subscription,
app/licensing/services.py::issue_license_key) -- confirmed by grep
(this file imports those functions, never the raw model constructors
for a write). See docs/owner/phase9_5d/commercial-fulfillment-contract.md,
subscription-license-integration-report.md,
fulfillment-idempotency-and-recovery.md.

Default policy: FULL_CONFIRMED_PAYMENT_REQUIRED (invoice.status == PAID).
Only NEW_SUBSCRIPTION fulfillment is implemented this milestone --
RENEWAL/ADD_ON/DEVICE_POLICY_CHANGE/COMMERCIAL_EXTENSION are named,
real gaps, not silently claimed supported (spec: "do not invent
unsupported fulfillment types").
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
from app.commercial_sales.errors import CommercialSalesError
from app.extensions import db_session
from app.licensing.services import create_license, issue_license_key
from app.models.base import utcnow
from app.models.catalog import Plan
from app.models.commercial_sales import (
    CommercialInvoice,
    CommercialOperationsIdempotencyKey,
    CommercialRefund,
    SalesOrder,
    SalesOrderLine,
)
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.subscriptions.services import create_subscription, transition_subscription

OPERATION_CODE = "ORDER_FULFILL"
FULFILLMENT_INTENT_NEW_SUBSCRIPTION = "NEW_SUBSCRIPTION"

# Subscription states a fulfillment retry may safely resume from and
# reuse. Any other real state (CANCELLED/EXPIRED/PAST_DUE/SUSPENDED/
# COMPLETED) means something else already happened to this Subscription
# outside this fulfillment attempt -- reusing it blindly would be unsafe
# (item #4: "existing incompatible Subscription or License state").
_RESUMABLE_SUBSCRIPTION_STATUSES = ("DRAFT", "ACTIVE")


def _check_eligibility(order: SalesOrder) -> tuple[CommercialInvoice, SalesOrderLine]:
    if order.status != "CONFIRMED":
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason=f"order status is {order.status}, not CONFIRMED")

    invoice = db_session.execute(
        select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID")
    ).scalars().first()
    if invoice is None:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="no issued invoice exists for this order")
    if invoice.status != "PAID":
        # Default policy: FULL_CONFIRMED_PAYMENT_REQUIRED. Any alternative
        # (partial-payment fulfillment) requires explicit configured
        # policy + management permission + reason + audit -- not
        # implemented this milestone (no silent override).
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason=f"invoice status is {invoice.status}, full payment required")

    blocking_refund = db_session.execute(
        select(CommercialRefund).where(
            CommercialRefund.commercial_invoice_id == invoice.id, CommercialRefund.status.in_(("DRAFT", "APPROVED", "PAID"))
        )
    ).scalars().first()
    if blocking_refund is not None:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="a refund exists against this invoice")

    subscription_line = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id, SalesOrderLine.plan_id.is_not(None)).order_by(SalesOrderLine.sort_order)
    ).scalars().first()
    if subscription_line is None:
        # Real, named gap: an addon-only order has nothing this milestone
        # knows how to fulfill (ADD_ON fulfillment intent not implemented).
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="no plan line found; add-on-only fulfillment not implemented this phase")

    return invoice, subscription_line


def fulfill_order(
    order: SalesOrder,
    *,
    actor_staff_user_id: uuid.UUID,
    license_pepper: str,
    idempotency_key: str,
) -> dict:
    existing_key = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == OPERATION_CODE,
        )
    ).scalars().first()
    if existing_key is not None:
        replayed = db_session.get(Subscription, existing_key.result_reference_id)
        if replayed is None or replayed.sales_order_id != order.id:
            # Duplicate idempotency key, conflicting payload (item #6):
            # the same key was already used for a DIFFERENT order --
            # rejected outright, never silently resolved to either order.
            raise CommercialSalesError("IDEMPOTENCY_CONFLICT")
        # Duplicate idempotency key, identical payload (item #5): same
        # key, same order -- returns the original result, no re-execution.
        # AUDIT-NNN: the license key was already revealed once, on the
        # original (non-replayed) call -- a replay legitimately has no key
        # to show. This is reported as "already issued" by the caller
        # (never re-derivable, ADR-9), not treated as an error and not
        # left to render as a blank/empty key.
        return {"subscription_id": replayed.id, "order_id": order.id, "replayed": True, "license_key": None}

    # Real concurrency guard (item #3): SELECT ... FOR UPDATE on the order
    # row serializes two concurrent fulfillment attempts for the SAME
    # order -- the second call blocks until the first commits (and then
    # sees order.status == "FULFILLED" and is rejected) or rolls back
    # (and then proceeds cleanly). Without this lock, two concurrent
    # calls could both read order.status == "CONFIRMED" before either
    # writes, and both create a Subscription.
    db_session.refresh(order, with_for_update=True)

    if order.status == "FULFILLED":
        raise CommercialSalesError("FULFILLMENT_ALREADY_COMPLETE")

    invoice, line = _check_eligibility(order)
    plan = db_session.get(Plan, line.plan_id)
    # Real bug caught before this shipped: a literal "ALL" string would
    # never match activation.py's exact membership check
    # (request_platform in license_row.allowed_platforms.split(",")) --
    # every real activation attempt would be silently rejected. Derived
    # from the plan's actual supported platforms instead (Milestone 4's
    # catalog read service, already computes this).
    catalog_item = describe_plan_for_sale(plan.id)
    allowed_platforms = ",".join(catalog_item["supported_platform_codes"]) if catalog_item else ""
    if not allowed_platforms:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="plan has no supported platforms configured")

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="FULFILLMENT_STARTED",
        entity_type="sales_order", entity_public_id=str(order.id), after_state={"invoice_id": str(invoice.id)},
    )

    try:
        # Recovery guard (items #1/#2): create_subscription()/
        # create_license() each commit their own transaction internally
        # (their own established behavior, unchanged) -- fulfill_order()
        # cannot wrap the whole sequence in one atomic outer transaction
        # without changing code outside app/commercial_sales/. A retry
        # after a partial failure at ANY point in the sequence (License
        # creation crashes after Subscription exists; the final
        # order/idempotency-key write crashes after both exist) must not
        # create a SECOND Subscription/License for the same order -- an
        # already-existing row for this sales_order_id/subscription_id is
        # reused, never duplicated, before falling back to creating a new
        # one.
        subscription = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order.id)).scalars().first()
        if subscription is None:
            # Canonical service call -- never a direct model-row insert.
            subscription = create_subscription(
                {
                    "customer_id": order.customer_id,
                    "product_id": plan.product_id,
                    "plan_id": plan.id,
                    "sales_order_id": order.id,
                    "device_allowance": plan.included_device_count,
                    "sales_owner_staff_user_id": actor_staff_user_id,
                },
                actor_staff_user_id,
            )
        elif subscription.status not in _RESUMABLE_SUBSCRIPTION_STATUSES:
            # Item #4: an incompatible existing state (e.g. CANCELLED,
            # EXPIRED, SUSPENDED) means something else already happened
            # to this Subscription outside this fulfillment attempt --
            # blindly reusing/transitioning it would silently override
            # that. Rejected, not silently proceeded.
            raise CommercialSalesError(
                "FULFILLMENT_NOT_ELIGIBLE", reason=f"existing subscription is in incompatible status {subscription.status}"
            )

        if subscription.status == "DRAFT":
            transition_subscription(subscription, "ACTIVE", actor_staff_user_id, reason="Fulfilled from confirmed, paid Sales Order")

        license_row = db_session.execute(select(License).where(License.subscription_id == subscription.id)).scalars().first()
        if license_row is not None and license_row.status not in ("DRAFT", "ISSUED"):
            raise CommercialSalesError(
                "FULFILLMENT_NOT_ELIGIBLE", reason=f"existing license is in incompatible status {license_row.status}"
            )
        if license_row is None:
            # Canonical service call -- never a direct model-row insert.
            license_row = create_license(
                {
                    "customer_id": order.customer_id,
                    "subscription_id": subscription.id,
                    "product_id": plan.product_id,
                    "plan_id": plan.id,
                    "allowed_platforms": allowed_platforms,
                    "device_limit": plan.included_device_count,
                },
                actor_staff_user_id,
            )
        # The one real key-issuance authority -- never constructs the key
        # or writes key fields directly (app/licensing/services.py). Its
        # own idempotency ledger (LicenseKeyIssuanceEvent) already makes
        # this call itself safely retryable.
        #
        # AUDIT-NNN: the plaintext key `issue_license_key` returns here used
        # to be discarded -- a licence fulfilled through this pipeline was
        # issued to a customer who could never read the key to activate it.
        # The full key exists only in this return value (ADR-9: never
        # persisted, never logged, never re-derivable), so it is captured
        # and threaded through fulfill_order's own return value, to be
        # revealed once by the caller exactly the way
        # licensing/routes.py::issue renders `revealed_key` for the direct
        # path. `license_key` stays None when this call resumes a retry
        # whose license was already ISSUED by an earlier attempt (the key
        # was shown once, then, and is equally unrecoverable now).
        license_key = None
        if license_row.status == "DRAFT":
            license_row, license_key = issue_license_key(license_row, license_pepper, f"{idempotency_key}-license", actor_staff_user_id)

        order.status = "FULFILLED"
        order.fulfilled_at = utcnow()
        order.version += 1

        db_session.add(
            CommercialOperationsIdempotencyKey(
                idempotency_key=idempotency_key, operation_code=OPERATION_CODE, result_reference_id=subscription.id
            )
        )
        db_session.commit()
    except Exception:
        db_session.rollback()
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="FULFILLMENT_FAILED",
            entity_type="sales_order", entity_public_id=str(order.id),
        )
        raise

    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="FULFILLMENT_COMPLETED",
        entity_type="sales_order", entity_public_id=str(order.id),
        after_state={"subscription_id": str(subscription.id), "license_id": str(license_row.id)},
    )
    return {
        "subscription_id": subscription.id, "license_id": license_row.id, "order_id": order.id,
        "replayed": False, "license_key": license_key,
    }
