"""Phase 9.5D Milestone 12 -- Commercial Refund domain service.

Built on the existing CommercialRefund schema (Phase 9.5A), extended
this milestone with version/approved_at/paid_at/voided_at (migration
f2b7d4e91a63 -- a real, proven gap, see app/models/commercial_sales.py).
A Refund is a real, separate, immutable-history record -- never a
negative PaymentRecord row, never an edit to the original payment
(docs/owner/phase9_5a/payment-and-fulfillment-contract.md, unchanged).
See docs/owner/phase9_5d/refund-domain-contract.md,
refund-entitlement-consequence-policy.md.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.commercial_sales import refund_policy
from app.commercial_sales.calculator import calculate_refundable_balance, validate_refund_amount
from app.commercial_sales.errors import REFUND_TRANSITIONS, CommercialSalesError
from app.commercial_sales.invoices import confirmed_allocated_amount
from app.extensions import db_session
from app.models.base import utcnow
from app.models.commercial_sales import CommercialInvoice, CommercialRefund


def total_confirmed_refunds(invoice: CommercialInvoice) -> Decimal:
    amounts = db_session.execute(
        select(CommercialRefund.amount).where(CommercialRefund.commercial_invoice_id == invoice.id, CommercialRefund.status == "PAID")
    ).scalars().all()
    return sum(amounts, Decimal("0.00"))


def refundable_balance(invoice: CommercialInvoice) -> Decimal:
    """Refundable base is money actually collected (confirmed+allocated),
    not the invoice's face total -- you cannot refund money that was
    never received. Non-Negotiable: 'refund cannot exceed refundable
    amount', 'prior refunds included'."""
    return calculate_refundable_balance(confirmed_allocated_amount(invoice), total_confirmed_refunds(invoice))


def create_refund(
    invoice: CommercialInvoice,
    *,
    amount: Decimal,
    reason: str,
    payment_record_id: uuid.UUID | None,
    actor_employee_profile_id: uuid.UUID,
    actor_staff_user_id: uuid.UUID,
    window_override_reason: str | None = None,
) -> CommercialRefund:
    """window_override_reason: a Super Admin's written reason to allow this
    refund after the 30 days (refund_policy.py). None = no override asked;
    ignored while the window is still open (nothing is recorded)."""
    if not reason or not reason.strip():
        raise CommercialSalesError("REASON_REQUIRED")
    now = utcnow()
    override_reason = None
    if _refund_window_closed(invoice, now):
        override_reason = refund_policy.check_window_override(actor_staff_user_id, window_override_reason)

    amount = validate_refund_amount(amount, refundable_balance(invoice))

    refund = CommercialRefund(
        commercial_invoice_id=invoice.id,
        payment_record_id=payment_record_id,
        amount=amount,
        currency=invoice.currency,
        reason=reason,
        status="DRAFT",
        created_by_employee_profile_id=actor_employee_profile_id,
        version=1,
    )
    if override_reason is not None:
        refund_policy.record_window_override(refund, actor_staff_user_id=actor_staff_user_id, reason=override_reason,
                                             now=now)
    db_session.add(refund)
    db_session.flush()
    if override_reason is not None:
        # Commits the refund AND its override audit row together.
        refund_policy.audit_window_override(refund, invoice, actor_staff_user_id=actor_staff_user_id)
    else:
        db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_REQUESTED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"commercial_invoice_id": str(invoice.id), "amount": str(amount)},
    )
    return refund


def _refund_window_closed(invoice: CommercialInvoice, now) -> bool:
    """Owner decision (2026-10-25): NO refunds after 30 days since the
    payment was confirmed. The window ends at ladder.refund_window_ends_at
    -- the same moment, function and constant (HOLD_PERIOD) as the
    commission hold, so a refund is possible exactly while the commission is
    still held. Closed AT that moment (the hold is over at it). Only a Super
    Admin's written override lets a refund through after it
    (refund_policy.check_window_override)."""
    from app.commissions import ladder

    ends_at = ladder.refund_window_ends_at(invoice)
    return ends_at is not None and now >= ends_at


def refund_window_closed(invoice: CommercialInvoice) -> bool:
    """For the screens: show the Super Admin's override box only when needed."""
    return _refund_window_closed(invoice, utcnow())


def _check_transition(status: str, target: str) -> None:
    if target not in REFUND_TRANSITIONS.get(status, set()):
        raise CommercialSalesError("INVALID_REFUND_TRANSITION", from_status=status, to_status=target)


def _check_version(refund: CommercialRefund, expected_version: int | None) -> None:
    if expected_version is not None and refund.version != expected_version:
        raise CommercialSalesError("STALE_VERSION")


def approve_refund(
    refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None
) -> CommercialRefund:
    """Non-Negotiable Rule 12: sales staff cannot self-approve a refund --
    enforced here regardless of permission grant, matching the exact
    self-approval-block pattern already used for CommercialApproval and
    payment confirmation."""
    _check_version(refund, expected_version)
    _check_transition(refund.status, "APPROVED")

    creator_staff_user_id = _resolve_creator_staff_user_id(refund)
    if creator_staff_user_id == actor_staff_user_id:
        raise CommercialSalesError("SELF_APPROVAL_FORBIDDEN")

    refund.status = "APPROVED"
    refund.approved_by_staff_user_id = actor_staff_user_id
    refund.approved_at = utcnow()
    refund.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_APPROVED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "APPROVED"},
    )
    return refund


def _resolve_creator_staff_user_id(refund: CommercialRefund) -> uuid.UUID | None:
    from app.models.employees import EmployeeProfile

    profile = db_session.get(EmployeeProfile, refund.created_by_employee_profile_id)
    return profile.staff_user_id if profile else None


def confirm_refund(
    refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None,
    window_override_reason: str | None = None,
) -> CommercialRefund:
    _check_version(refund, expected_version)
    _check_transition(refund.status, "PAID")

    invoice = db_session.get(CommercialInvoice, refund.commercial_invoice_id)
    # Milestone 24 (financial-property pass) -- real gap found: nothing
    # previously re-validated the refund total at confirm time, nor
    # locked the invoice. Two DRAFT refunds can each independently pass
    # create_refund()'s own validate_refund_amount() check (each within
    # the refundable balance individually) while their SUM exceeds it --
    # without a lock + re-check here, both could be confirmed
    # concurrently and over-refund the customer past what was actually
    # collected. Row-locked, matching allocate_payment()'s own pattern.
    db_session.execute(select(CommercialInvoice).where(CommercialInvoice.id == invoice.id).with_for_update())
    # Step 0 fix round (2026-10-25) -- the seller's ladder lock NEXT, before
    # anything below touches a commission row. The percentage reversal is an
    # INSERT whose FK to the reversed row takes FOR KEY SHARE on it, and the
    # approval of that row holds the seller lock while it asks FOR UPDATE on
    # it: with the seller lock taken after the reversal (inside
    # ladder.evaluate_sale, as on d15d11f0) the two waited for each other --
    # measured: "deadlock detected" (verifier probe v12). Lock order, every
    # path: payment row -> invoice row -> seller lock -> commission rows
    # (ladder.py module docstring). Re-taking it later in this transaction
    # (evaluate_sale) is a no-op.
    from app.commissions import ladder

    ladder.lock_seller(invoice.created_by_employee_profile_id)
    # Owner decision (2026-10-25): no refunds after 30 days -- checked again
    # here, at the moment the money would go out: a refund requested on day
    # 29 and paid out on day 31 is refused, nothing written -- unless the
    # refund already carries a Super Admin's override (given when it was
    # requested), or a Super Admin gives one now. Decided under the invoice
    # row lock, so the override is recorded once.
    now = utcnow()
    override_now = False
    try:
        if _refund_window_closed(invoice, now) and refund.window_override_at is None:
            reason_given = refund_policy.check_window_override(actor_staff_user_id, window_override_reason)
            refund_policy.record_window_override(refund, actor_staff_user_id=actor_staff_user_id,
                                                 reason=reason_given, now=now)
            override_now = True
    except CommercialSalesError:
        db_session.rollback()
        raise

    collected = confirmed_allocated_amount(invoice)
    already_refunded = total_confirmed_refunds(invoice)  # excludes this refund -- still pre-PAID
    if already_refunded + refund.amount > collected:
        # Step 0 (2026-10-25): rolled back -- an override recorded above on
        # this refund must not be left pending in the session for some later
        # commit to write without its audit row.
        db_session.rollback()
        raise CommercialSalesError("REFUND_EXCEEDS_REFUNDABLE", amount=refund.amount, refundable=collected - already_refunded)

    refund.status = "PAID"
    refund.paid_at = now
    refund.version += 1
    db_session.flush()

    refunded = already_refunded + refund.amount
    before_status = invoice.status
    is_full_refund = refunded >= collected and collected > 0
    if is_full_refund:
        invoice.status = "REFUNDED"
    elif refunded > 0:
        invoice.status = "PARTIALLY_REFUNDED"
    invoice.version += 1
    db_session.flush()
    # Step 0 (2026-10-25): the owner's threshold, judged now -- this refund
    # included (flushed PAID above) -- and stored on the refund, before the
    # licence and the ladder read it (refund_policy.py).
    refund_policy.decide_refund(refund, invoice)

    # Step 0 ladder (2026-10-24): the refund, the commission it takes back and
    # the licence's stop are ONE transaction. Before (1025d626) the refund
    # committed first, then the subscription was suspended (its own commit),
    # then each commission reversal committed on its own -- a crash between
    # them left a refunded customer whose seller kept the commission.
    from app.commissions.ledger import audit_reversal, reverse_commissions_for_refund

    reason = f"Refund {refund.id} confirmed against invoice {invoice.id}"
    try:
        stops = _schedule_entitlement_consequence(refund, invoice, is_full_refund=is_full_refund,
                                                  actor_staff_user_id=actor_staff_user_id)
        reversals = reverse_commissions_for_refund(
            invoice, refund_amount=refund.amount, collected_amount=collected, reason=reason,
            actor_staff_user_id=actor_staff_user_id, commit=False,
        )
        # The ladder: a refund of package money inside the sale's 30-day hold
        # removes the sale and re-derives its month; after the hold the sale
        # stays counted and the refund is recorded on it (ladder.py).
        if invoice.sales_order_id is not None:
            ladder.evaluate_sale(invoice.sales_order_id)
        if override_now:
            # Commits everything above together with the override's audit row.
            refund_policy.audit_window_override(refund, invoice, actor_staff_user_id=actor_staff_user_id)
        else:
            db_session.commit()
    except BaseException:
        db_session.rollback()
        raise

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_CONFIRMED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "PAID", "invoice_status": {"before": before_status, "after": invoice.status},
                     "threshold_percent_applied": refund.threshold_percent_applied,
                     "package_refund_over_threshold": refund.package_refund_over_threshold},
    )
    for stop in stops:
        audit_record(
            actor_staff_user_id=actor_staff_user_id,
            actor_role_snapshot=None,
            action_code="ENTITLEMENT_CONSEQUENCE_SCHEDULED",
            entity_type="license",
            entity_public_id=str(stop.license_id),
            after_state={"to_status": stop.to_status, "devices_removed": stop.devices_removed,
                         "effective_at": stop.effective_at.isoformat(),
                         "subscription_id": str(stop.subscription_id) if stop.subscription_id else None},
        )
    for reversal in reversals:
        audit_reversal(reversal, reason=reason, actor_staff_user_id=actor_staff_user_id)
    ladder.write_pending_audit(actor_staff_user_id)
    return refund


def _schedule_entitlement_consequence(refund: CommercialRefund, invoice: CommercialInvoice, *, is_full_refund: bool,
                                      actor_staff_user_id: uuid.UUID) -> list:
    """Item #7: refund after fulfillment must invoke the configured
    entitlement consequence -- Non-Negotiable Rule 7, never a silent choice.

    Step 0 (2026-10-24), the owner's rule: the licence keeps working 2 days
    after the refund, then stops. The consequence is SCHEDULED, flush only
    (app/licensing/scheduled_stops.py): a stop for each live licence of the
    fulfilled subscription, at the refund's paid_at + 2 days, applied by the
    licence checks themselves. It used to suspend the subscription on the
    spot (and never the licence). Only a FULL refund after fulfilment has
    this consequence -- a partial one (e.g. only the extra pair given back)
    keeps the licence, as entitlement_consequence.py has always decided.

    Step 0 fix round (2026-10-25), verifier probe v9: for a PACKAGE sale (an
    order with a plan line) "full" is decided on the PACKAGE money. Refunds
    are counted against the extra-device part first, so once the refunds
    exceed it the package is no longer fully paid -- the ladder stops
    counting the sale at the same line (ladder.py::_package_refund) -- and
    the licence stops 2 days later. Measured on d15d11f0: 250 of a 300
    Package 1 + pair sale refunded, the sale uncounted, no stop: the shop kept
    the package for 50 JOD. A refund of ONLY the extra-device money keeps the
    licence and removes the devices it paid for, 2 days later
    (scheduled_stops.py). An order with no plan line keeps the old rule
    (full refund -> stop)."""
    from app.commercial_sales.entitlement_consequence import SUSPEND_ENTITLEMENTS, determine_entitlement_consequence
    from app.commissions import ladder
    from app.licensing.scheduled_stops import (
        devices_already_scheduled_off,
        schedule_device_removal_after_refund,
        schedule_stops_after_refund,
    )
    from app.models.commercial_sales import SalesOrder
    from app.models.subscriptions import Subscription

    if invoice.sales_order_id is None:
        return []
    order = db_session.get(SalesOrder, invoice.sales_order_id)
    order_was_fulfilled = order is not None and order.status == "FULFILLED"
    subscription = db_session.execute(
        select(Subscription).where(Subscription.sales_order_id == order.id)
    ).scalars().first() if order is not None else None

    split = ladder.package_split(invoice)
    refunded = total_confirmed_refunds(invoice)  # includes this refund: flushed PAID above
    # Step 0 (2026-10-25), the owner's threshold: for a package sale the
    # licence stops only when the package money given back so far is ABOVE
    # the threshold (decided and stored on this refund by
    # refund_policy.decide_refund) -- a full refund of a package sale is
    # judged the same way (100% of a Package 1 is above any threshold below
    # 100%). Before, any package money back stopped it (a 10 JOD goodwill
    # refund cut the shop off). Not a package sale: the old rule (full refund).
    stops_licence = refund_policy.refund_stops_licence(refund) if split is not None else is_full_refund
    consequence = determine_entitlement_consequence(
        order_was_fulfilled=order_was_fulfilled, is_full_refund=stops_licence,
    )
    if subscription is None:
        return []
    if consequence == SUSPEND_ENTITLEMENTS:
        return schedule_stops_after_refund(refund, subscription, actor_staff_user_id=actor_staff_user_id)
    if not order_was_fulfilled or split is None or refunded <= 0:
        return []
    refund_ids = db_session.execute(
        select(CommercialRefund.id).where(CommercialRefund.commercial_invoice_id == invoice.id,
                                          CommercialRefund.status == "PAID")
    ).scalars().all()
    devices = _refunded_devices(invoice, refunded) - devices_already_scheduled_off(subscription.id, refund_ids)
    return schedule_device_removal_after_refund(refund, subscription, devices=devices,
                                                actor_staff_user_id=actor_staff_user_id)


def _refunded_devices(invoice: CommercialInvoice, refunded: Decimal) -> int:
    """How many extra devices the refunded extra-device money paid for: the
    invoice's device lines in order, a unit counted only once its whole price
    is given back (25 of a 50 JOD pair removes nothing)."""
    from app.commercial_sales.catalog_for_sales import DEVICES_PER_UNIT_BY_ADDON_CODE
    from app.models.catalog import Addon
    from app.models.commercial_sales import CommercialInvoiceItem

    lines = db_session.execute(
        select(CommercialInvoiceItem.line_total, CommercialInvoiceItem.quantity, Addon.addon_code)
        .join(Addon, Addon.id == CommercialInvoiceItem.addon_id)
        .where(CommercialInvoiceItem.commercial_invoice_id == invoice.id,
               Addon.addon_code.in_(tuple(DEVICES_PER_UNIT_BY_ADDON_CODE)))
        .order_by(CommercialInvoiceItem.sort_order, CommercialInvoiceItem.id)
    ).all()
    remaining, devices = Decimal(refunded), 0
    for line_total, quantity, code in lines:
        if quantity <= 0 or Decimal(line_total) <= 0:
            continue
        unit = Decimal(line_total) / quantity
        units = min(quantity, int(remaining // unit))
        devices += units * DEVICES_PER_UNIT_BY_ADDON_CODE[code]
        remaining -= unit * units
        if units < quantity:
            break
    return devices


def void_refund(refund: CommercialRefund, *, actor_staff_user_id: uuid.UUID, expected_version: int | None = None) -> CommercialRefund:
    _check_version(refund, expected_version)
    _check_transition(refund.status, "VOID")

    refund.status = "VOID"
    refund.voided_at = utcnow()
    refund.version += 1
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="REFUND_VOIDED",
        entity_type="commercial_refund",
        entity_public_id=str(refund.id),
        after_state={"status": "VOID"},
    )
    return refund
