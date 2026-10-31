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

import hashlib
import uuid
from contextlib import contextmanager

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.audit.services import record as audit_record
from app.commercial_sales.catalog_for_sales import DEVICE_SLOTS_BY_ADDON_CODE, describe_plan_for_sale
from app.commercial_sales.packages import contract_terms
from app.commercial_sales.errors import CommercialSalesError
from app.extensions import db_session, get_engine
from app.licensing.issuance import resolve_license_term
from app.licensing.services import create_license, issue_license_key
from app.models.base import utcnow
from app.models.catalog import Addon, Plan
from app.models.employees import EmployeeProfile
from app.models.commercial_sales import (
    CommercialInvoice,
    CommercialOperationsIdempotencyKey,
    CommercialRefund,
    SalesOrder,
    SalesOrderLine,
)
from app.models.customers import Customer
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


def _extra_device_slots(order: SalesOrder) -> tuple[int, int, int]:
    """Devices this order bought beyond the plan's included count, as
    (extra desktops, extra phones, untyped any-kind devices) (Step 0,
    2026-10-24; typed 2026-10-31). Before, fulfillment gave every licence
    plan.included_device_count and silently dropped any extra devices the
    customer had paid for.

    Each device add-on line counts quantity x its slots per unit
    (catalog_for_sales.DEVICE_SLOTS_BY_ADDON_CODE): EXTRA_DESKTOP is one
    desktop, EXTRA_PHONE one phone, a retired EXTRA_DEVICE_PAIR (orders made
    before the typed extras) one of each, a single EXTRA_DEVICE one untyped.
    The licence total is included + all three; the first two are also kept
    typed on the licence so activation admits only the bought kind."""
    rows = db_session.execute(
        select(Addon.addon_code, SalesOrderLine.quantity)
        .join(Addon, Addon.id == SalesOrderLine.addon_id)
        .where(SalesOrderLine.sales_order_id == order.id, Addon.addon_code.in_(tuple(DEVICE_SLOTS_BY_ADDON_CODE)))
    ).all()
    desktops = sum(DEVICE_SLOTS_BY_ADDON_CODE[code][0] * quantity for code, quantity in rows)
    phones = sum(DEVICE_SLOTS_BY_ADDON_CODE[code][1] * quantity for code, quantity in rows)
    untyped = sum(DEVICE_SLOTS_BY_ADDON_CODE[code][2] * quantity for code, quantity in rows)
    return desktops, phones, untyped


def _sales_owner_staff_user_id(order: SalesOrder) -> uuid.UUID | None:
    """The seller's staff account -- the order's creator, the same employee
    the commission ledger credits through the invoice. Step 0 fix: this used
    to be the fulfilling actor, i.e. the manager who confirmed the order,
    so every fulfilled subscription named the checker as its sales owner."""
    profile = db_session.get(EmployeeProfile, order.created_by_employee_profile_id)
    return profile.staff_user_id if profile is not None else None


_FULFILLMENT_LOCK_NAMESPACE = "commercial_sales.fulfill_order"


def _fulfillment_lock_key(order_id: uuid.UUID) -> int:
    # Same derivation shape as operational_reports/scheduler.py::_advisory_lock_key.
    raw = f"{_FULFILLMENT_LOCK_NAMESPACE}|{order_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)  # fits in a Postgres bigint


@contextmanager
def _hold_fulfillment_lock(order_id: uuid.UUID):
    """Per-order mutual exclusion that SURVIVES the commits inside fulfill_order.

    The row lock taken via `db_session.refresh(order, with_for_update=True)`
    is released by the very next statement, `audit_record(...
    "FULFILLMENT_STARTED" ...)`, which commits (app/audit/services.py:
    `record()` always ends with `db_session.commit()`). Every service called
    afterwards -- `create_subscription`, `transition_subscription`,
    `create_license`, `issue_license_key` -- commits again for the same
    reason (item #1/#2's recovery guard needs each step durable on its own).
    So anything held ON db_session's transaction is not a lock across the
    sequence at all: it is released the instant the first nested commit
    fires, which is before the second concurrent caller's read of
    `order.status` even happens. Measured on CI 2026-09-07 (run
    34138397829): `test_item3_concurrent_fulfillment_requests_only_one_
    creates_subscription` got 4 Subscriptions from 5 concurrent callers, not
    1 -- see docs/corrections/owner/fulfillment-lock-released-by-nested-
    commits-root-cause-analysis.md for the full trace.

    This lock lives on a DEDICATED connection with its own transaction, so
    db_session may commit as often as it likes without touching it -- the
    lock ends only when this context exits (the `finally` below rolls back
    that connection's otherwise-empty transaction, which is what releases a
    transaction-scoped advisory lock). A transaction-scoped advisory lock
    (`pg_advisory_xact_lock`, not the session-scoped variant) is used for the
    same reason `operational_reports/scheduler.py` uses one: if this process
    dies mid-fulfillment, the lock is released when Postgres notices the
    connection is gone -- nothing can leak it forever.

    `lock_timeout` (10s by default, `app/extensions.py::init_db`) applies to
    the wait inside `pg_advisory_xact_lock`: a caller that cannot acquire the
    lock in time is told `FULFILLMENT_IN_PROGRESS` and can retry once the
    winner finishes, rather than crashing with a raw `OperationalError`.

    Costs one extra pooled connection for the duration of a fulfillment
    call, which is rare (an operator confirming one order at a time) and
    bounded by `lock_timeout`.
    """
    conn = get_engine().connect()
    try:
        txn = conn.begin()
        try:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _fulfillment_lock_key(order_id)})
        except OperationalError as exc:
            txn.rollback()
            # 55P03 = lock_not_available (lock_timeout expired). Anything else is not ours to translate.
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":
                raise CommercialSalesError(
                    "FULFILLMENT_IN_PROGRESS",
                    reason="another fulfillment of this order is still running; retry once it finishes",
                ) from exc
            raise
        try:
            yield
        finally:
            # Nothing to commit on this connection; ending the transaction releases the lock.
            txn.rollback()
    finally:
        conn.close()


def _create_subscription_and_license(
    order, plan, allowed_platforms, device_limit, start, end, actor_staff_user_id, license_pepper, idempotency_key,
    extra_desktops=0, extra_phones=0,
):
    """The ordinary sale: a NEW subscription and a NEW licence with a new key.
    (Moved here unchanged from fulfill_order's body so the conversion path can
    sit beside it; the comments are the original ones.)"""
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
                "billing_cycle": plan.billing_model,
                "start_date": start,
                "end_date": end,
                "device_allowance": device_limit,
                "sales_owner_staff_user_id": _sales_owner_staff_user_id(order),
                # Step 0 packages: the contract the package is sold
                # on (Package 2: 12 months, Package 3: 24), fixed
                # at the sale. The licence term above is the PAID
                # period (one month for a monthly package); the
                # contract is what the later months and the
                # commission ladder read (packages.contract_terms).
                **contract_terms(plan, start),
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
                "device_limit": device_limit,
                "extra_desktop_slots": extra_desktops,
                "extra_phone_slots": extra_phones,
                "valid_from": start,
                "valid_until": end,
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
    return subscription, license_row, license_key


_PRE_SALE_LIFECYCLE = ("LEAD", "PROSPECT", "PILOT")


def _mark_customer_active(customer_id) -> None:
    """The customer has paid: a lead / prospect / pilot customer becomes ACTIVE. Rehearsal
    2026-10-31: after a trial was paid for and unlocked, the customer page still said
    "prospect". Only the pre-sale stages move; a suspended, closed or archived customer is
    never reopened by a payment. Committed by the caller's fulfillment commit."""
    customer = db_session.get(Customer, customer_id)
    if customer is not None and customer.lifecycle_status in _PRE_SALE_LIFECYCLE:
        customer.lifecycle_status = "ACTIVE"


def _convert_trial_in_place(
    order, plan, allowed_platforms, device_limit, start, end, actor_staff_user_id, extra_desktops=0, extra_phones=0
):
    """New customer flow (NC2): the money for a trial is confirmed. Under the
    order's fulfillment lock (held by the caller) the trial's OWN subscription
    goes PILOT -> ACTIVE with the paid term and package, its licence keeps its
    key and gets the new package / device limit / dates, and the trial record
    becomes CONVERTED. Each step is a get-or-do so a retry after a crash part-way
    finishes the conversion instead of failing on the half-converted rows.

    Why in place and not a new subscription: the shop's devices are registered
    against the trial's licence; a new licence would strand them and need a new
    key typed on every till. The lease the shop's next check-in receives now says
    ACTIVE with the paid term_end, which is what unlocks the till.

    LicenseEntitlement needs no sync step: entitlements are resolved from the
    licence's plan every time a lease is signed (licensing_service/entitlements.py;
    LicenseEntitlement rows are only staff overrides, and none are written for a
    trial), so pointing the licence at the new plan IS the re-sync."""
    from app.customer_onboarding import documents as customer_documents
    from app.customer_onboarding.trial import convert_trial_pilot, trial_pilot_for_subscription

    subscription = db_session.get(Subscription, order.converts_subscription_id)
    if subscription is None or subscription.customer_id != order.customer_id:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="the trial this order converts does not belong to this customer")
    already_done_by_this_order = subscription.sales_order_id == order.id
    if not already_done_by_this_order and subscription.status != "PILOT":
        raise CommercialSalesError(
            "FULFILLMENT_NOT_ELIGIBLE", reason=f"the trial to convert is in status {subscription.status}, not PILOT"
        )
    pilot = trial_pilot_for_subscription(subscription.id)
    if pilot is None:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="the subscription to convert is not a trial")
    if subscription.product_id != plan.product_id:
        raise CommercialSalesError("FULFILLMENT_NOT_ELIGIBLE", reason="a trial can only be converted to a package of its own product")

    license_row = db_session.execute(select(License).where(License.subscription_id == subscription.id)).scalars().first()
    if license_row is None or license_row.status not in ("ISSUED", "ACTIVE"):
        raise CommercialSalesError(
            "FULFILLMENT_NOT_ELIGIBLE",
            reason=f"the trial's licence is in incompatible status {license_row.status if license_row else 'MISSING'}",
        )

    if not already_done_by_this_order:
        subscription.plan_id = plan.id
        subscription.billing_cycle = plan.billing_model
        subscription.start_date = start
        subscription.end_date = end
        subscription.device_allowance = device_limit
        subscription.sales_order_id = order.id
        subscription.sales_owner_staff_user_id = _sales_owner_staff_user_id(order)
        for column, value in contract_terms(plan, start).items():
            setattr(subscription, column, value)
        db_session.commit()
    if subscription.status == "PILOT":
        transition_subscription(
            subscription, "ACTIVE", actor_staff_user_id, reason="Trial paid: converted from the confirmed, paid Sales Order"
        )
    license_row.plan_id = plan.id
    license_row.allowed_platforms = allowed_platforms
    license_row.device_limit = device_limit
    # Absolute, not added: a retry after a crash must land on the same numbers,
    # and the trial licence carried no typed extras of its own.
    license_row.extra_desktop_slots = extra_desktops
    license_row.extra_phone_slots = extra_phones
    license_row.valid_from = start
    license_row.valid_until = end
    db_session.commit()
    if pilot.status != "CONVERTED":
        convert_trial_pilot(pilot, order_id=order.id, actor_staff_user_id=actor_staff_user_id)
    customer_documents.keep_for_customer_lifetime(order.customer_id)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="TRIAL_CONVERTED",
        entity_type="subscription", entity_public_id=str(subscription.id),
        after_state={"sales_order_id": str(order.id), "plan_id": str(plan.id), "device_limit": device_limit,
                     "extra_desktop_slots": extra_desktops, "extra_phone_slots": extra_phones,
                     "term_start": start.isoformat(), "term_end": end.isoformat() if end else None},
    )
    return subscription, license_row


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

    # Real concurrency guard (item #3): the ADVISORY LOCK held for the rest
    # of this function (_hold_fulfillment_lock, above) is what serializes
    # two concurrent fulfillment attempts for the SAME order. The
    # SELECT ... FOR UPDATE re-read directly below is NOT that guard --
    # it used to be described as one, and measurement proved that claim
    # wrong: the row lock it takes is released by the very next statement,
    # audit_record(... "FULFILLMENT_STARTED" ...), which commits (every
    # audit_record call ends with db_session.commit()), and every service
    # called afterwards in this function -- create_subscription,
    # transition_subscription, create_license, issue_license_key -- commits
    # again for the same reason. So a second concurrent caller can acquire
    # the row lock and read order.status == "CONFIRMED" moments after the
    # first caller's audit-start commit, long before the first caller has
    # created anything. Measured on CI 2026-09-07 (run 34138397829):
    # test_item3_concurrent_fulfillment_requests_only_one_creates_
    # subscription got 4 Subscriptions from 5 concurrent callers, not 1.
    # See docs/corrections/owner/fulfillment-lock-released-by-nested-
    # commits-root-cause-analysis.md for the full trace. The re-read itself
    # is still worth keeping: under the advisory lock, it gives this call a
    # fresh view of whatever the previous lock-holder (if any) just
    # committed.
    with _hold_fulfillment_lock(order.id):
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

        extra_desktops, extra_phones, extra_untyped = _extra_device_slots(order)
        device_limit = plan.included_device_count + extra_desktops + extra_phones + extra_untyped
        if plan.max_device_count is not None and device_limit > plan.max_device_count:
            raise CommercialSalesError(
                "FULFILLMENT_NOT_ELIGIBLE",
                reason=f"{device_limit} devices exceed the package maximum of {plan.max_device_count}",
            )
        # Same term rule as the direct issuance screen (issuance.py::
        # resolve_license_term -- read its docstring: ONE_TIME is perpetual
        # on purpose). Step 0: this path used to set no term at all, so a
        # MONTHLY/ANNUAL licence sold through the pipeline never expired.
        start, end = resolve_license_term(plan)

        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="FULFILLMENT_STARTED",
            entity_type="sales_order", entity_public_id=str(order.id), after_state={"invoice_id": str(invoice.id)},
        )

        try:
            if order.converts_subscription_id is not None:
                # New customer flow (NC2): this order PAYS FOR A TRIAL. The trial's
                # own subscription and licence are converted in place -- no second
                # subscription, no new licence key (the shop already has the key).
                subscription, license_row = _convert_trial_in_place(
                    order, plan, allowed_platforms, device_limit, start, end, actor_staff_user_id,
                    extra_desktops, extra_phones,
                )
                license_key = None
            else:
                subscription, license_row, license_key = _create_subscription_and_license(
                    order, plan, allowed_platforms, device_limit, start, end, actor_staff_user_id, license_pepper, idempotency_key,
                    extra_desktops, extra_phones,
                )

            _mark_customer_active(order.customer_id)
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
