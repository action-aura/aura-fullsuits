"""Step 0 (2026-10-24) -- the owner's monthly commission ladder.

The owner's rules (Aura Growth Plan, "Your answers" + the 2026-10-24
additions):
  - per seller, per calendar month in Jordan time, the count restarts each
    month and the ladder numbers are TOTALS: 1 sale = 20, 2 = 50, 3 = 100,
    4 = 200, 5 = 350 JOD, and each sale after the 5th adds 30;
  - a sale is a new package for a branch: one sales order with a plan line
    (a new branch is its own package, so its own order). Extra devices -- the
    pair -- never count, sold with the sale or later (an order with no plan
    line is never a sale);
  - Package 1 (no contract) counts when its money is in: the invoice fully
    allocated, i.e. the 250 confirmed;
  - a monthly package (one with a contract) counts once about 100 JOD of
    package money is paid: monthly price x ceil(100 / monthly price) -- 105
    for Package 2 (its 3rd payment), 100 for Package 3 (its 5th) -- in the
    month the payment that crosses that line is confirmed; never before;
  - 10 JOD to the subscription's sales owner for each renewal;
  - commission is payable 30 days after the confirming date;
  - a refund inside those 30 days: the sale does not count -- since
    2026-10-25 only a refund ABOVE the owner's threshold (a percentage of the
    package money paid, commercial_sales/refund_policy.py); at or below it
    the sale still counts. After the 30 days no refund is possible, except
    one a Super Admin allows in writing: then the sale stops counting too
    (REFUND_LATE_OVERRIDE).

HOW IT IS KEPT (auditable, crash-safe, exactly once):
  - CommissionLadderSale is the FACT "this order counted, at this moment, in
    this month" (or stopped counting, and why). One COUNTED row per order is
    the database's own guarantee (partial unique index).
  - The money is in the ordinary commission ledger (entry_kind LADDER), so
    approval, payout and the seller's own-only view keep working. Each counted
    sale's live rows add up to its SHARE of the month: sales ordered by the
    moment they qualified, the k-th sale's share is total(k) - total(k-1).
    Whenever a month changes (a sale counts, or stops counting), the whole
    month is re-derived (_recompute_months) and only the differences are
    posted: a sale whose share changed while still held has its open row
    CANCELLED (never edited) and a new row posted; one whose share changed
    after it was approved/paid gets a REVERSED (take-back) row. So the month
    always ends EXACTLY as if the removed sale had never happened, and the
    history shows every step.
  - Payable vs held: a row is approvable only after payable_after (the
    sale's hold end). Because sales are ordered by the moment they qualified
    and every hold is the same 30 days, the sales whose hold is over are
    always the FIRST k of the month -- so a refund inside a hold can only
    move sales that are themselves still held. Approved or paid money is
    never re-sliced by a refund inside the hold.
    That ordering argument needs every NEW sale to qualify no earlier than
    the moment it is counted. Fix round (2026-10-25), verifier probe v10: a
    sale qualified at its payment's CONFIRMATION, so a payment confirmed on
    1 September whose sale was finished (allocated) on 25 October went to
    position 1 of September -- a closed month whose five sales were approved
    -- and took back 120 JOD of approved money; its own share was payable at
    once. A sale now qualifies at the LATER of its payment's confirmation and
    the moment it actually became countable (the allocation that completed
    its invoice; for a monthly package, also the payment that crossed the
    line): in practice the moment it is counted. Its month and hold run from
    there, so it lands after every sale already past its hold.
    (A CORRECTION -- reverse_allocation -- of a sale already past its hold
    still re-derives its month and may take back from the later sales of
    that month: the month stays exactly ladder(count). That is the one path
    that re-slices approved money, by design, and only for money that turned
    out never to have been the sale's.)
  - Everything here runs INSIDE the caller's transaction (allocation,
    payment confirmation, refund, allocation reversal): flush only, never
    commit. The caller's commit writes the cause and its commission
    together; a crash leaves neither. Audit rows are queued and written by
    write_pending_audit() after that commit (audit_record commits on its own).
  - One lock: a transaction-scoped advisory lock per SELLER. Lock order on
    every path: the payment row, then the invoice row (the callers already
    hold them; subscriptions/services.py::lock_payment_for_update), then the
    seller lock, then commission ledger rows. Nothing holding the seller lock
    asks for a payment or invoice lock, and nothing touches a commission row
    before it -- fix round (2026-10-25), verifier probe v12: confirm_refund
    and reverse_allocation inserted the percentage reversal (its FK takes
    FOR KEY SHARE on the reversed row) BEFORE asking for the seller lock,
    while approve_commission_entry holds the seller lock and asks FOR UPDATE
    on that row: "deadlock detected". Both now take the seller lock right
    after the invoice row. Where one transaction needs TWO sellers (a renewal
    payment: the sale's seller and the subscription's sales owner) both are
    taken up front in lock-key order (on_payment_status_written). Two
    managers confirming two payments of one seller at once are serialized
    here; the second sees the first's committed result.

Coexistence with the percentage plans: the rule in force on the Jordan date
the sale's FIRST package payment was confirmed governs the WHOLE sale, on
both sides (rule_governing_payment). Fix round (2026-10-25), verifier probe
v8: each side used the rule at its own payment, so a ladder seller moved to a
percentage plan before a Package 2's 3rd payment earned NOTHING for the sale
-- the ladder read the percentage rule at the 3rd payment and the percentage
side never sees a later monthly payment. A MONTHLY_LADDER seller gets ladder rows
and no percentage row; a percentage seller gets the percentage row (pair
part excluded, ledger.py) and no ladder row. Never both for one sale: a sale
with a live percentage row never counts on the ladder, and the percentage
path posts nothing for an order already counted on a ladder.
"""
from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.commissions.services import resolve_active_rule
from app.extensions import db_session
from app.models.base import utcnow
from app.models.catalog import Plan
from app.models.commercial_ops import PaymentCorrectionHistory
from app.models.commercial_sales import (
    CommercialInvoice,
    CommercialInvoiceItem,
    CommercialRefund,
    PaymentAllocation,
    SalesOrder,
    SalesOrderLine,
)
from app.models.commissions import CommissionLadderSale, CommissionLedgerEntry
from app.models.employees import EmployeeProfile
from app.models.subscriptions import PaymentRecord, Subscription
from app.operational_reports.periods import OWNER_TIMEZONE, jordan_date

LADDER_RULE_TYPE = "MONTHLY_LADDER"
LADDER_CURRENCY = "JOD"
# Cumulative month totals for 0..5 sales; each sale after the 5th adds 30.
LADDER_TOTALS = (Decimal("0.00"), Decimal("20.00"), Decimal("50.00"), Decimal("100.00"), Decimal("200.00"),
                 Decimal("350.00"))
STEP_AFTER_FIFTH = Decimal("30.00")
RENEWAL_COMMISSION = Decimal("10.00")
HOLD_PERIOD = timedelta(days=30)
MONTHLY_QUALIFYING_AMOUNT = Decimal("100.00")

_ZERO = Decimal("0.00")
_SELLER_LOCK_NAMESPACE = "commissions.ladder.seller"
_PENDING_AUDIT_KEY = "commission_ladder_pending_audit"


# ------------------------------------------------------------ the numbers --

def ladder_total(sales: int) -> Decimal:
    """The month's ladder TOTAL for this many counted sales."""
    if sales <= 0:
        return _ZERO
    if sales < len(LADDER_TOTALS):
        return LADDER_TOTALS[sales]
    return LADDER_TOTALS[-1] + STEP_AFTER_FIFTH * (sales - (len(LADDER_TOTALS) - 1))


def ladder_share(position: int) -> Decimal:
    """What the position-th sale of a month adds: 20, 30, 50, 100, 150, 30, 30..."""
    return ladder_total(position) - ladder_total(position - 1)


def ladder_month_of(moment: datetime) -> date:
    """The calendar month (its first day) a moment falls in, in Jordan time.
    23:30 UTC on 30 September is 02:30 on 1 October in Amman -- October."""
    return moment.astimezone(OWNER_TIMEZONE).date().replace(day=1)


def _utc(moment: datetime) -> datetime:
    """Every moment this module adds days to is UTC first. A timestamp read
    back from Postgres carries the SESSION's zone (Asia/Gaza on the dev and
    test servers), and adding timedelta(days=30) to a zone-aware value is
    wall-clock arithmetic: across a DST change the hold would end an hour
    early or late (measured: 23:30 UTC + 30 days came back as 00:30 UTC)."""
    return moment.astimezone(timezone.utc)


# ------------------------------------------------------------- the facts --

def payment_confirmed_at(payment_id: uuid.UUID) -> datetime | None:
    """When a payment was confirmed: its latest move INTO CONFIRMED in the
    correction history (correct_payment, the one writer of payment status,
    writes one row per move). Reads the caller's own uncommitted move too."""
    db_session.flush()
    moment = db_session.execute(
        select(PaymentCorrectionHistory.created_at)
        .where(
            PaymentCorrectionHistory.payment_record_id == payment_id,
            PaymentCorrectionHistory.new_status == "CONFIRMED",
            PaymentCorrectionHistory.previous_status != "CONFIRMED",
        )
        .order_by(PaymentCorrectionHistory.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _utc(moment) if moment is not None else None


def ladder_rule_for(employee_profile_id: uuid.UUID, moment: datetime):
    """The seller's rule in force on that moment's Jordan date, or None."""
    return resolve_active_rule(employee_profile_id, jordan_date(moment))


def is_ladder_rule(rule) -> bool:
    return rule is not None and rule.rule_type == LADDER_RULE_TYPE


@dataclass
class _Sale:
    order: SalesOrder
    invoice: CommercialInvoice
    plan: Plan
    seller_profile_id: uuid.UUID
    package_amount: Decimal       # the package's money on the invoice (setup fee included)
    other_amount: Decimal         # everything else on it (the pair)
    subscription: Subscription | None
    is_monthly: bool


@dataclass
class _Qualifying:
    payment_id: uuid.UUID
    allocation_id: uuid.UUID | None
    # The moment the sale became countable: the LATER of this payment's
    # confirmation and the allocation that completed the sale's invoice
    # (see the module docstring, fix round probe v10).
    countable_at: datetime


def _load_sale(order_id: uuid.UUID) -> _Sale | None:
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return None
    plan_line = db_session.execute(
        select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id, SalesOrderLine.plan_id.is_not(None))
        .order_by(SalesOrderLine.sort_order)
    ).scalars().first()
    if plan_line is None:
        return None  # extra devices alone, sold later: never a sale
    invoice = db_session.execute(
        select(CommercialInvoice)
        .where(CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID")
        .order_by(CommercialInvoice.created_at, CommercialInvoice.id)
    ).scalars().first()
    if invoice is None:
        return None
    plan = db_session.get(Plan, plan_line.plan_id)
    package_amount = sum(
        db_session.execute(
            select(CommercialInvoiceItem.line_total).where(
                CommercialInvoiceItem.commercial_invoice_id == invoice.id, CommercialInvoiceItem.plan_id.is_not(None)
            )
        ).scalars().all(),
        _ZERO,
    )
    subscription = db_session.execute(
        select(Subscription).where(Subscription.sales_order_id == order.id)
    ).scalars().first()
    contract = (subscription.contract_term_months if subscription is not None else None) or plan.contract_term_months
    return _Sale(
        order=order, invoice=invoice, plan=plan, seller_profile_id=invoice.created_by_employee_profile_id,
        package_amount=Decimal(package_amount), other_amount=Decimal(invoice.total) - Decimal(package_amount),
        subscription=subscription, is_monthly=bool(contract),
    )


def _live_allocations(invoice: CommercialInvoice) -> list[PaymentAllocation]:
    return db_session.execute(
        select(PaymentAllocation)
        .join(PaymentRecord, PaymentRecord.id == PaymentAllocation.payment_record_id)
        .where(
            PaymentAllocation.commercial_invoice_id == invoice.id,
            PaymentAllocation.reversed_at.is_(None),
            PaymentRecord.status == "CONFIRMED",
        )
        .order_by(PaymentAllocation.allocated_at, PaymentAllocation.id)
    ).scalars().all()


def required_monthly_amount(monthly_price: Decimal) -> Decimal:
    """'About 100 JOD': whole months until 100 is reached -- 3 x 35 = 105
    (Package 2's 3rd payment), 5 x 20 = 100 (Package 3's 5th)."""
    return monthly_price * math.ceil(MONTHLY_QUALIFYING_AMOUNT / monthly_price)


def _sale_payment_ids(sale: _Sale) -> set[uuid.UUID]:
    return {a.payment_record_id for a in db_session.execute(
        select(PaymentAllocation).where(PaymentAllocation.commercial_invoice_id == sale.invoice.id)
    ).scalars().all()}


def _qualifying(sale: _Sale) -> _Qualifying | None:
    """The payment that made the sale count, or None while it does not."""
    allocations = _live_allocations(sale.invoice)
    running, completing = _ZERO, None
    for allocation in allocations:
        running += Decimal(allocation.allocated_amount)
        if running >= Decimal(sale.invoice.total):
            completing = allocation
            break
    if completing is None:
        return None  # the sale's own invoice is not fully paid (yet, or any more)
    first = _Qualifying(
        payment_id=completing.payment_record_id, allocation_id=completing.id,
        countable_at=_countable_at(completing),
    )
    if not sale.is_monthly:
        return first
    monthly_price = sale.package_amount
    if monthly_price <= 0:
        return None
    required = required_monthly_amount(monthly_price)
    events: list[tuple[datetime, str, Decimal, _Qualifying]] = [
        (first.countable_at, str(first.payment_id), sale.package_amount, first)
    ]
    for at, payment in later_package_payments(sale):
        events.append((at, str(payment.id), Decimal(payment.amount),
                       _Qualifying(payment_id=payment.id, allocation_id=None, countable_at=at)))
    paid = _ZERO
    for _at, _id, amount, qualifying in sorted(events, key=lambda e: (e[0], e[1])):
        paid += amount
        if paid >= required:
            # A later payment exists only once the sale was finished (its
            # subscription is made at fulfilment), so it is never earlier
            # than the sale's own countable moment.
            return qualifying
    return None


def later_package_payments(sale: _Sale) -> list[tuple[datetime, PaymentRecord]]:
    """The subscription's confirmed payments after the sale's own (a monthly
    package's later months, a Package 1 renewal), in the invoice's currency,
    with the moment each was confirmed: package money paid on the sale beyond
    its invoice. Used by _qualifying ("about 100 JOD") and by the refund
    threshold (refund_policy.package_money_paid), so the two count the same
    money."""
    if sale.subscription is None:
        return []
    sale_payments = _sale_payment_ids(sale)
    later = db_session.execute(
        select(PaymentRecord).where(
            PaymentRecord.subscription_id == sale.subscription.id, PaymentRecord.status == "CONFIRMED"
        )
    ).scalars().all()
    return [(payment_confirmed_at(payment.id) or _utc(payment.created_at), payment) for payment in later
            if payment.id not in sale_payments and payment.currency == sale.invoice.currency]


def _countable_at(allocation: PaymentAllocation) -> datetime:
    """The moment an allocation's money became countable: the LATER of its
    payment's confirmation and the allocation itself."""
    allocated_at = _utc(allocation.allocated_at)
    confirmed_at = payment_confirmed_at(allocation.payment_record_id) or allocated_at
    return max(confirmed_at, allocated_at)


def refund_window_ends_at(invoice: CommercialInvoice) -> datetime | None:
    """Owner decision (2026-10-25): NO refunds after 30 days. The last moment
    this invoice's money may be refunded: the moment its money became
    countable (the allocation that completed it -- or, while it is not fully
    paid, the latest one) + HOLD_PERIOD. The SAME function and constant as a
    package sale's commission hold (_qualifying -> hold_ends_at), so "still
    refundable" and "inside the hold" can never disagree. None when nothing
    is allocated (nothing to refund)."""
    allocations = _live_allocations(invoice)
    if not allocations:
        return None
    running, completing = _ZERO, allocations[-1]
    for allocation in allocations:
        running += Decimal(allocation.allocated_amount)
        if running >= Decimal(invoice.total):
            completing = allocation
            break
    return _countable_at(completing) + HOLD_PERIOD


def _first_package_payment_at(sale: _Sale) -> datetime | None:
    """When the sale's FIRST package payment was confirmed: the earliest
    confirmation among the payments live-allocated to its invoice."""
    moments = [payment_confirmed_at(a.payment_record_id) or _utc(a.allocated_at) for a in _live_allocations(sale.invoice)]
    return min(moments) if moments else None


def _governing_rule(sale: _Sale):
    """The rule that governs the whole sale: the seller's rule in force on
    the Jordan date its first package payment was confirmed (module
    docstring, fix round probe v8). None while nothing is paid."""
    first = _first_package_payment_at(sale)
    return ladder_rule_for(sale.seller_profile_id, first) if first is not None else None


def rule_governing_payment(invoice: CommercialInvoice, payment_id: uuid.UUID, fallback_moment: datetime):
    """The commission rule a payment allocated to this invoice is paid
    under -- the percentage side's question (ledger.py). For a package sale
    the rule that governs the whole sale, so the two sides can never
    disagree; for any other invoice (no plan line: extra devices sold later,
    a quote-pipeline invoice without an order) the rule on the Jordan date
    this payment was confirmed."""
    if invoice.sales_order_id is not None:
        sale = _load_sale(invoice.sales_order_id)
        if sale is not None and sale.invoice.id == invoice.id:
            first = _first_package_payment_at(sale)
            if first is not None:
                # None here is a real answer (no plan in force then): the
                # sale earns nothing, it does not fall back to a later rule.
                return ladder_rule_for(sale.seller_profile_id, first)
    moment = payment_confirmed_at(payment_id) or _utc(fallback_moment)
    return ladder_rule_for(invoice.created_by_employee_profile_id, moment)


def load_package_sale(invoice: CommercialInvoice) -> _Sale | None:
    """The package sale this invoice is the sale's own invoice of, or None."""
    if invoice.sales_order_id is None:
        return None
    sale = _load_sale(invoice.sales_order_id)
    if sale is None or sale.invoice.id != invoice.id:
        return None
    return sale


def package_split(invoice: CommercialInvoice) -> tuple[Decimal, Decimal] | None:
    """(package money, everything else -- the extra devices) on a package
    sale's invoice, or None when the invoice is not a package sale's. A
    refund is counted against the second part first (_package_refund)."""
    sale = load_package_sale(invoice)
    return None if sale is None else (sale.package_amount, sale.other_amount)


def _package_refund(sale: _Sale) -> CommercialRefund | None:
    """The confirmed refund that took the sale off the ladder, or None.
    Step 0 (2026-10-25), the owner's threshold: the first refund judged ABOVE
    it when it was paid out (refund_policy.refund_uncounts_sale -- the one
    decision point). A refund at or below it leaves the sale a sale; so does
    a refund of only the pair. A refund paid out before the threshold existed
    keeps the old rule: the first one that gave back PACKAGE money (refunds
    past the pair's part of the invoice)."""
    from app.commercial_sales import refund_policy

    refunds = db_session.execute(
        select(CommercialRefund).where(
            CommercialRefund.commercial_invoice_id == sale.invoice.id, CommercialRefund.status == "PAID"
        ).order_by(CommercialRefund.paid_at, CommercialRefund.id)
    ).scalars().all()
    running = _ZERO
    for refund in refunds:
        running += Decimal(refund.amount)
        if refund_policy.refund_uncounts_sale(refund, legacy_package_money_back=running > sale.other_amount):
            return refund
    return None


def _has_live_payment_rule_entry(invoice_id: uuid.UUID) -> bool:
    """Any not-fully-reversed percentage-rule row on this invoice."""
    rows = db_session.execute(
        select(CommissionLedgerEntry.commission_amount).where(
            CommissionLedgerEntry.source_commercial_invoice_id == invoice_id,
            CommissionLedgerEntry.entry_kind == "PAYMENT_RULE",
            CommissionLedgerEntry.status != "CANCELLED",
        )
    ).scalars().all()
    return bool(rows) and sum(rows, _ZERO) != _ZERO


def counted_sale_for_order(order_id: uuid.UUID) -> CommissionLadderSale | None:
    return db_session.execute(
        select(CommissionLadderSale)
        .where(CommissionLadderSale.sales_order_id == order_id, CommissionLadderSale.status == "COUNTED")
        .execution_options(populate_existing=True)
    ).scalars().first()


# --------------------------------------------------------------- locking --

def lock_seller(employee_profile_id: uuid.UUID) -> None:
    """Transaction-scoped: released by the caller's commit or rollback.
    Taken again in the same transaction it is a no-op (advisory locks stack)."""
    db_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _seller_lock_key(employee_profile_id)})


# ----------------------------------------------------------------- audit --

def _queue(action_code: str, entity_type: str, entity_id, after_state: dict, reason: str | None = None) -> None:
    db_session.info.setdefault(_PENDING_AUDIT_KEY, []).append(
        {"action_code": action_code, "entity_type": entity_type, "entity_public_id": str(entity_id),
         "after_state": after_state, "reason": reason}
    )


@event.listens_for(Session, "after_rollback")
def _drop_queued_audit(session) -> None:
    # What a rolled-back transaction did never happened: never audit it.
    session.info.pop(_PENDING_AUDIT_KEY, None)


def write_pending_audit(actor_staff_user_id) -> None:
    """Called by every trigger site right AFTER its commit."""
    from app.audit.services import record as audit_record

    pending = db_session.info.pop(_PENDING_AUDIT_KEY, None) or []
    for item in pending:
        audit_record(actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, **item)


# -------------------------------------------------------- counting a sale --

def evaluate_sale(order_id: uuid.UUID, *, now: datetime | None = None) -> None:
    """Bring one order's place on its seller's ladder in line with the facts:
    count it if it now qualifies, uncount it if it no longer does (a refund
    inside the hold, or a correction), and re-derive every month touched.
    Idempotent: called again with nothing changed, it writes nothing."""
    now = now or utcnow()
    # The session does not autoflush (extensions.py): the caller's own
    # writes -- the payment just confirmed, the refund just paid, the
    # allocation just reversed -- must be visible to the reads below.
    db_session.flush()
    sale = _load_sale(order_id)
    if sale is None:
        return
    lock_seller(sale.seller_profile_id)
    counted = counted_sale_for_order(order_id)
    qualifying = _qualifying(sale)
    refund = _package_refund(sale)
    touched: set[date] = set()

    if counted is None:
        if qualifying is None or refund is not None:
            return
        if _has_live_payment_rule_entry(sale.invoice.id):
            return  # already paid by the percentage rule: never both
        rule = _governing_rule(sale)
        if not is_ladder_rule(rule):
            return
        row = CommissionLadderSale(
            employee_profile_id=sale.seller_profile_id, sales_order_id=sale.order.id,
            commercial_invoice_id=sale.invoice.id, subscription_id=sale.subscription.id if sale.subscription else None,
            package_plan_id=sale.plan.id, commission_rule_version_id=rule.id,
            qualifying_payment_record_id=qualifying.payment_id, qualifying_allocation_id=qualifying.allocation_id,
            qualified_at=qualifying.countable_at, ladder_month=ladder_month_of(qualifying.countable_at),
            hold_ends_at=qualifying.countable_at + HOLD_PERIOD, currency=sale.invoice.currency, status="COUNTED",
        )
        db_session.add(row)
        db_session.flush()
        touched.add(row.ladder_month)
        _queue("COMMISSION_LADDER_SALE_COUNTED", "commission_ladder_sale", row.id, {
            "sales_order_id": str(sale.order.id), "ladder_month": row.ladder_month.isoformat(),
            "qualifying_payment_record_id": str(qualifying.payment_id), "qualified_at": row.qualified_at.isoformat(),
        })
    else:
        if counted.subscription_id is None and sale.subscription is not None:
            counted.subscription_id = sale.subscription.id
        if refund is not None:
            if refund.paid_at < counted.hold_ends_at:
                _uncount(counted, "REFUND_IN_HOLD", f"Refund {refund.id} confirmed inside the 30-day hold.", now,
                         refund_id=refund.id)
                touched.add(counted.ladder_month)
            elif refund.window_override_at is not None:
                # Step 0 (2026-10-25): a Super Admin allowed this refund after
                # the 30 days (refund_policy.py). The sale stops counting and
                # its month is re-derived: an only-EARNED row is cancelled, an
                # APPROVED or PAID one gets a take-back (REVERSED) row -- the
                # one other path, besides a CORRECTION, that takes back money
                # already final, and only on the owner's own written decision.
                _uncount(counted, "REFUND_LATE_OVERRIDE",
                         f"Refund {refund.id} allowed by a Super Admin after the 30 days.", now, refund_id=refund.id)
                touched.add(counted.ladder_month)
            elif counted.refunded_after_hold_at is None:
                # Refunds already recorded before the owner's rule "no refunds
                # after 30 days" (2026-10-25) only: refunds.py now refuses a
                # refund at or after refund_window_ends_at -- the same moment
                # as this hold end -- so a NEW refund never reaches this
                # branch. For those older refunds the default stands: the
                # sale stays counted (its commission had become final) and the
                # refund is recorded on it.
                counted.refunded_after_hold_at = refund.paid_at
                counted.source_refund_id = refund.id
                _queue("COMMISSION_LADDER_REFUND_AFTER_HOLD", "commission_ladder_sale", counted.id, {
                    "sales_order_id": str(sale.order.id), "refund_id": str(refund.id),
                    "hold_ended_at": counted.hold_ends_at.isoformat(),
                })
        elif qualifying is None:
            _uncount(counted, "CORRECTION", "The money that made this sale count is no longer on it.", now)
            touched.add(counted.ladder_month)
    if touched:
        _recompute_months(sale.seller_profile_id, touched, now=now)


def _uncount(row: CommissionLadderSale, cause: str, reason: str, now: datetime, *, refund_id=None) -> None:
    row.status = "UNCOUNTED"
    row.uncounted_at = now
    row.uncount_cause = cause
    row.uncount_reason = reason
    if refund_id is not None:
        row.source_refund_id = refund_id
    db_session.flush()
    _queue("COMMISSION_LADDER_SALE_UNCOUNTED", "commission_ladder_sale", row.id, {
        "sales_order_id": str(row.sales_order_id), "ladder_month": row.ladder_month.isoformat(), "cause": cause,
    }, reason=reason)


def _ladder_rows(ladder_sale_id: uuid.UUID) -> list[CommissionLedgerEntry]:
    return db_session.execute(
        select(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.ladder_sale_id == ladder_sale_id, CommissionLedgerEntry.entry_kind == "LADDER",
               CommissionLedgerEntry.status != "CANCELLED")
        .order_by(CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()


def _recompute_months(employee_profile_id: uuid.UUID, months: set[date], *, now: datetime) -> None:
    """Re-derive each month from its counted sales and post only the
    differences (see the module docstring). Runs under the seller lock."""
    for month in sorted(months):
        sales = db_session.execute(
            select(CommissionLadderSale)
            .where(CommissionLadderSale.employee_profile_id == employee_profile_id,
                   CommissionLadderSale.ladder_month == month)
            .order_by(CommissionLadderSale.qualified_at, CommissionLadderSale.id)
            .execution_options(populate_existing=True)
        ).scalars().all()
        counted = [s for s in sales if s.status == "COUNTED"]
        month_total = ladder_total(len(counted))
        wanted = {s.id: (ladder_share(i + 1), i + 1) for i, s in enumerate(counted)}
        for sale in sales:
            share, position = wanted.get(sale.id, (_ZERO, None))
            rows = _ladder_rows(sale.id)
            posted = sum((Decimal(r.commission_amount) for r in rows), _ZERO)
            if posted == share:
                continue
            open_rows = [r for r in rows if r.status == "EARNED"]
            for row in open_rows:
                row.status = "CANCELLED"
            db_session.flush()
            settled = posted - sum((Decimal(r.commission_amount) for r in open_rows), _ZERO)
            difference = share - settled
            if difference > 0:
                entry = _ladder_entry(sale, difference, month=month, position=position, month_total=month_total,
                                      share=share, status="EARNED", now=now)
            elif difference < 0:
                # Only reachable when a share shrinks AFTER it was approved or
                # paid (a late, earlier-qualifying sale): a take-back row
                # against the sale's latest approved/paid row.
                target = next(r for r in reversed(rows) if r.status in ("APPROVED", "PAID")
                              and Decimal(r.commission_amount) > 0)
                entry = _ladder_entry(sale, difference, month=month, position=position, month_total=month_total,
                                      share=share, status="REVERSED", now=now, reversal_of=target.id)
            else:
                entry = None
            db_session.flush()
            _queue("COMMISSION_LADDER_MONTH_RECOMPUTED", "commission_ladder_sale", sale.id, {
                "ladder_month": month.isoformat(), "position": position, "share": str(share),
                "was": str(posted), "cancelled_entry_ids": [str(r.id) for r in open_rows],
                "posted_entry_id": str(entry.id) if entry is not None else None,
                "month_count": len(counted), "month_total": str(month_total),
            })


def _ladder_entry(sale: CommissionLadderSale, amount: Decimal, *, month: date, position, month_total: Decimal,
                  share: Decimal, status: str, now: datetime, reversal_of=None) -> CommissionLedgerEntry:
    entry = CommissionLedgerEntry(
        employee_profile_id=sale.employee_profile_id, commission_rule_version_id=sale.commission_rule_version_id,
        source_commercial_invoice_id=sale.commercial_invoice_id, source_payment_record_id=sale.qualifying_payment_record_id,
        source_payment_allocation_id=None,
        # For a ladder row: base = the month's ladder total at posting, the
        # "rate" = this sale's whole share at its position.
        base_amount=month_total, rate_or_fixed_applied=share, commission_amount=amount, currency=sale.currency,
        status=status, earned_at=now, entry_kind="LADDER", ladder_sale_id=sale.id, ladder_month=month,
        ladder_position=position, payable_after=sale.hold_ends_at if status == "EARNED" else now,
        reversal_of_ledger_entry_id=reversal_of,
    )
    db_session.add(entry)
    return entry


# -------------------------------------------------------------- renewals --

def _sales_owner_profile_id(subscription: Subscription) -> uuid.UUID | None:
    if subscription.sales_owner_staff_user_id is None:
        return None
    return db_session.execute(
        select(EmployeeProfile.id).where(EmployeeProfile.staff_user_id == subscription.sales_owner_staff_user_id)
    ).scalar_one_or_none()


def _renewal_period(subscription: Subscription, payment: PaymentRecord):
    """WHICH renewed period a payment of the subscription renews (a hashable
    key), or None when it renews nothing. The owner: "10 JOD per renewal";
    "a new contract at the end earns the 10 JOD renewal" -- one renewal per
    renewed period, never one per payment:
      - a package with a contract (Packages 2/3, term T months, ending E):
        a payment dated inside the contract is the contract itself, never a
        renewal; one dated after E falls in the k-th new contract period
        (E + (k-1)T, E + kT] -- one renewal per period, however many monthly
        payments it holds. Fix round (2026-10-25), verifier probe v7: every
        payment after E earned 10 JOD (E never moves -- the contract is fixed
        at the sale, 1dfa1f6108b2), so a shop paying month to month after
        its 12 months earned its seller 10 JOD a month, for ever. The period
        is read from payment_date, which may no longer be in the future
        (subscriptions/services.py::PaymentDateInFutureError), so a typed date
        cannot reach a period that has not started;
      - a package with no contract (Package 1, yearly): each payment of at
        least a year's renewal price IS one renewed year -- its own period."""
    if payment.id is None or payment.payment_date is None:
        return None
    if subscription.contract_term_months:
        ends = subscription.contract_ends_on
        if ends is None or payment.payment_date <= ends:
            return None
        from app.commercial_ops.renewal_dates import add_interval

        period = 1
        while payment.payment_date > add_interval(ends, months=subscription.contract_term_months * period):
            period += 1
        return ("CONTRACT", period)
    from app.commercial_sales.packages import renewal_price

    price = renewal_price(subscription)
    if price is not None and price[1] == payment.currency and Decimal(payment.amount) < price[0]:
        return None
    return ("PAYMENT", payment.id)


def _renewal_rows(payment_id: uuid.UUID) -> list[CommissionLedgerEntry]:
    return db_session.execute(
        select(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.source_payment_record_id == payment_id,
               CommissionLedgerEntry.entry_kind == "RENEWAL", CommissionLedgerEntry.status != "CANCELLED")
        .order_by(CommissionLedgerEntry.created_at, CommissionLedgerEntry.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()


def _subscription_renewal_candidates(subscription: Subscription, sale_payment_ids: set) -> list[PaymentRecord]:
    """Every payment of the subscription that is not the sale's own."""
    return [p for p in db_session.execute(
        select(PaymentRecord).where(PaymentRecord.subscription_id == subscription.id)
        .execution_options(populate_existing=True)
    ).scalars().all() if p.id not in sale_payment_ids]


def _period_holder(subscription: Subscription, period, payments: list[PaymentRecord]) -> PaymentRecord | None:
    """The payment that holds this period's renewal: a live (net > 0)
    renewal row -- whatever the payment's status now (a refund after the
    hold keeps it, see evaluate_renewal_payment)."""
    for candidate in payments:
        if _renewal_period(subscription, candidate) != period:
            continue
        if sum((Decimal(r.commission_amount) for r in _renewal_rows(candidate.id)), _ZERO) > _ZERO:
            return candidate
    return None


def _post_renewal(payment: PaymentRecord, subscription: Subscription, owner_profile_id: uuid.UUID, sale,
                  *, now: datetime) -> None:
    confirmed_at = payment_confirmed_at(payment.id) or now
    rule = ladder_rule_for(owner_profile_id, confirmed_at)
    if not is_ladder_rule(rule):
        return
    entry = CommissionLedgerEntry(
        employee_profile_id=owner_profile_id, commission_rule_version_id=rule.id,
        source_commercial_invoice_id=sale.invoice.id if sale is not None else None,
        source_payment_record_id=payment.id, source_payment_allocation_id=None,
        base_amount=Decimal(payment.amount), rate_or_fixed_applied=RENEWAL_COMMISSION,
        commission_amount=RENEWAL_COMMISSION, currency=payment.currency, status="EARNED", earned_at=now,
        entry_kind="RENEWAL", ladder_month=ladder_month_of(confirmed_at), payable_after=confirmed_at + HOLD_PERIOD,
    )
    db_session.add(entry)
    db_session.flush()
    _queue("COMMISSION_RENEWAL_EARNED", "commission_ledger_entry", entry.id, {
        "payment_record_id": str(payment.id), "subscription_id": str(subscription.id),
        "commission_amount": str(RENEWAL_COMMISSION), "period": str(_renewal_period(subscription, payment)),
    })


def _take_back_renewal(payment: PaymentRecord, rows: list[CommissionLedgerEntry], *, now: datetime) -> bool:
    """Take a payment's renewal back (cancel what is held, a REVERSED row
    for what was approved). False when it is kept: a refund after the hold
    is final, like a ladder sale's (evaluate_sale)."""
    posted = sum((Decimal(r.commission_amount) for r in rows), _ZERO)
    if posted <= _ZERO:
        return False
    open_rows = [r for r in rows if r.status == "EARNED"]
    if payment.status == "REFUNDED" and all(r.payable_after is not None and r.payable_after <= now for r in rows):
        return False
    for row in open_rows:
        row.status = "CANCELLED"
    db_session.flush()
    settled = posted - sum((Decimal(r.commission_amount) for r in open_rows), _ZERO)
    if settled > _ZERO:
        target = next(r for r in rows if r.status in ("APPROVED", "PAID") and Decimal(r.commission_amount) > 0)
        db_session.add(CommissionLedgerEntry(
            employee_profile_id=target.employee_profile_id, commission_rule_version_id=target.commission_rule_version_id,
            source_commercial_invoice_id=target.source_commercial_invoice_id, source_payment_record_id=payment.id,
            source_payment_allocation_id=None, base_amount=target.base_amount,
            rate_or_fixed_applied=target.rate_or_fixed_applied, commission_amount=-settled, currency=target.currency,
            status="REVERSED", earned_at=now, entry_kind="RENEWAL", ladder_month=target.ladder_month,
            payable_after=now, reversal_of_ledger_entry_id=target.id,
        ))
        db_session.flush()
    _queue("COMMISSION_RENEWAL_TAKEN_BACK", "payment_record", payment.id, {
        "payment_status": payment.status, "cancelled_entry_ids": [str(r.id) for r in open_rows],
        "taken_back_after_approval": str(settled),
    })
    return True


def evaluate_renewal_payment(payment: PaymentRecord, *, now: datetime | None = None) -> None:
    """Post (or take back) the 10 JOD renewal commission for one payment --
    at most one per renewed period (_renewal_period). Idempotent."""
    now = now or utcnow()
    if payment.subscription_id is None:
        return
    db_session.flush()
    subscription = db_session.get(Subscription, payment.subscription_id)
    if subscription is None:
        return
    owner_profile_id = _sales_owner_profile_id(subscription)
    if owner_profile_id is None:
        return
    sale = _load_sale(subscription.sales_order_id) if subscription.sales_order_id is not None else None
    sale_payment_ids = _sale_payment_ids(sale) if sale is not None else set()
    if payment.id in sale_payment_ids:
        return  # the sale's own payment is the sale, not a renewal
    lock_seller(owner_profile_id)
    period = _renewal_period(subscription, payment)
    rows = _renewal_rows(payment.id)
    if payment.status != "CONFIRMED" or period is None:
        if _take_back_renewal(payment, rows, now=now) and period is not None:
            _hand_period_on(subscription, period, owner_profile_id, sale, sale_payment_ids, now=now)
        return
    if rows:
        return  # it holds (or held) its renewal already: a retry, a re-saved note
    if _period_holder(subscription, period, _subscription_renewal_candidates(subscription, sale_payment_ids)):
        return  # another payment of the same period already earned it
    _post_renewal(payment, subscription, owner_profile_id, sale, now=now)


def _hand_period_on(subscription: Subscription, period, owner_profile_id: uuid.UUID, sale, sale_payment_ids: set,
                    *, now: datetime) -> None:
    """A period's renewal was taken back (its payment bounced, was voided,
    or refunded inside the hold): the period's earliest-confirmed OTHER
    confirmed payment, if any, earns it -- the shop did renew once."""
    payments = _subscription_renewal_candidates(subscription, sale_payment_ids)
    if _period_holder(subscription, period, payments) is not None:
        return
    waiting = [p for p in payments if p.status == "CONFIRMED" and _renewal_period(subscription, p) == period
               and not _renewal_rows(p.id)]
    if not waiting:
        return
    waiting.sort(key=lambda p: (payment_confirmed_at(p.id) or _utc(p.created_at), str(p.id)))
    _post_renewal(waiting[0], subscription, owner_profile_id, sale, now=now)


def _seller_lock_key(employee_profile_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"{_SELLER_LOCK_NAMESPACE}|{employee_profile_id}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def on_payment_status_written(payment: PaymentRecord, *, now: datetime | None = None) -> None:
    """correct_payment's hook (the one writer of payment status), inside its
    transaction: a later monthly payment may count a monthly sale, and a
    renewal payment earns (or loses) its 10 JOD.

    Two seller locks may be needed -- the sale's seller (the ladder) and the
    subscription's sales owner (the renewal) -- so both are taken HERE, in
    lock-key order, before either step runs (each step re-takes its own: a
    no-op). Taken in the order the steps need them, two payments with the
    roles swapped would wait for each other (fix round, 2026-10-25)."""
    if payment.subscription_id is None:
        return
    subscription = db_session.get(Subscription, payment.subscription_id)
    if subscription is None:
        return
    sellers: set[uuid.UUID] = set()
    if subscription.sales_order_id is not None:
        sale = _load_sale(subscription.sales_order_id)
        if sale is not None:
            sellers.add(sale.seller_profile_id)
    owner_profile_id = _sales_owner_profile_id(subscription)
    if owner_profile_id is not None:
        sellers.add(owner_profile_id)
    for seller_profile_id in sorted(sellers, key=_seller_lock_key):
        lock_seller(seller_profile_id)
    if subscription.sales_order_id is not None:
        evaluate_sale(subscription.sales_order_id, now=now)
    evaluate_renewal_payment(payment, now=now)


# ------------------------------------------------------- what people see --

def ladder_month_summary(employee_profile_id: uuid.UUID, month: date, *, now: datetime | None = None) -> dict:
    """One seller's month: sales counted, the ladder total, how much of it
    is still held and how much is payable, and the renewals."""
    now = now or utcnow()
    counted = db_session.execute(
        select(CommissionLadderSale).where(
            CommissionLadderSale.employee_profile_id == employee_profile_id, CommissionLadderSale.ladder_month == month,
            CommissionLadderSale.status == "COUNTED",
        )
    ).scalars().all()
    rows = db_session.execute(
        select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.employee_profile_id == employee_profile_id, CommissionLedgerEntry.ladder_month == month,
            CommissionLedgerEntry.entry_kind.in_(("LADDER", "RENEWAL")), CommissionLedgerEntry.status != "CANCELLED",
        )
    ).scalars().all()
    ladder = [r for r in rows if r.entry_kind == "LADDER"]
    renewals = [r for r in rows if r.entry_kind == "RENEWAL"]
    total = sum((Decimal(r.commission_amount) for r in ladder), _ZERO)
    held = sum((Decimal(r.commission_amount) for r in ladder if r.payable_after and r.payable_after > now), _ZERO)
    renewals_total = sum((Decimal(r.commission_amount) for r in renewals), _ZERO)
    renewals_held = sum((Decimal(r.commission_amount) for r in renewals if r.payable_after and r.payable_after > now),
                        _ZERO)
    return {
        "month": month, "count": len(counted), "total": total, "held": held, "payable": total - held,
        "renewals": len([r for r in renewals if r.reversal_of_ledger_entry_id is None]), "renewals_total": renewals_total,
        "renewals_held": renewals_held, "currency": LADDER_CURRENCY,
        "refunded_after_hold": len([s for s in counted if s.refunded_after_hold_at is not None]),
    }


def ladder_months_for(employee_profile_id: uuid.UUID, *, limit: int = 12, now: datetime | None = None) -> list[dict]:
    """The seller's months that have any ladder or renewal activity, newest first."""
    months = set(db_session.execute(
        select(CommissionLadderSale.ladder_month).where(CommissionLadderSale.employee_profile_id == employee_profile_id)
    ).scalars().all())
    months |= set(db_session.execute(
        select(CommissionLedgerEntry.ladder_month).where(
            CommissionLedgerEntry.employee_profile_id == employee_profile_id,
            CommissionLedgerEntry.ladder_month.is_not(None),
        )
    ).scalars().all())
    return [ladder_month_summary(employee_profile_id, m, now=now) for m in sorted(months, reverse=True)[:limit]]


def ladder_month_overview(month: date, *, now: datetime | None = None) -> list[dict]:
    """Every seller's summary for one month (managers, commissions.view_all)."""
    profile_ids = set(db_session.execute(
        select(CommissionLadderSale.employee_profile_id).where(CommissionLadderSale.ladder_month == month)
    ).scalars().all())
    profile_ids |= set(db_session.execute(
        select(CommissionLedgerEntry.employee_profile_id).where(CommissionLedgerEntry.ladder_month == month)
    ).scalars().all())
    out = []
    for profile_id in profile_ids:
        profile = db_session.get(EmployeeProfile, profile_id)
        out.append({**ladder_month_summary(profile_id, month, now=now), "employee_name": profile.full_name if profile else ""})
    return sorted(out, key=lambda s: (-s["total"], s["employee_name"]))
