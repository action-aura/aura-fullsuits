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
  - a refund inside those 30 days: the sale does not count.

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
  - Everything here runs INSIDE the caller's transaction (allocation,
    payment confirmation, refund, allocation reversal): flush only, never
    commit. The caller's commit writes the cause and its commission
    together; a crash leaves neither. Audit rows are queued and written by
    write_pending_audit() after that commit (audit_record commits on its own).
  - One lock: a transaction-scoped advisory lock per SELLER, taken LAST --
    after the payment row and invoice row locks the callers already hold
    (subscriptions/services.py::lock_payment_for_update documents that
    order). Nothing holding it asks for a payment or invoice lock, so no
    cycle. Two managers confirming two payments of one seller at once are
    serialized here; the second sees the first's committed result.

Coexistence with the percentage plans: the rule in force (Jordan date of the
payment's confirmation) decides. A MONTHLY_LADDER seller gets ladder rows
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
from app.operational_reports.periods import OWNER_TIMEZONE

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


def _jordan_date(moment: datetime) -> date:
    return moment.astimezone(OWNER_TIMEZONE).date()


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
    return resolve_active_rule(employee_profile_id, _jordan_date(moment))


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
    confirmed_at: datetime


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
        confirmed_at=payment_confirmed_at(completing.payment_record_id) or _utc(completing.allocated_at),
    )
    if not sale.is_monthly:
        return first
    monthly_price = sale.package_amount
    if monthly_price <= 0:
        return None
    required = required_monthly_amount(monthly_price)
    events: list[tuple[datetime, str, Decimal, _Qualifying]] = [
        (first.confirmed_at, str(first.payment_id), sale.package_amount, first)
    ]
    if sale.subscription is not None:
        sale_payments = _sale_payment_ids(sale)
        later = db_session.execute(
            select(PaymentRecord).where(
                PaymentRecord.subscription_id == sale.subscription.id, PaymentRecord.status == "CONFIRMED"
            )
        ).scalars().all()
        for payment in later:
            if payment.id in sale_payments or payment.currency != sale.invoice.currency:
                continue
            at = payment_confirmed_at(payment.id) or _utc(payment.created_at)
            events.append((at, str(payment.id), Decimal(payment.amount),
                           _Qualifying(payment_id=payment.id, allocation_id=None, confirmed_at=at)))
    paid = _ZERO
    for _at, _id, amount, qualifying in sorted(events, key=lambda e: (e[0], e[1])):
        paid += amount
        if paid >= required:
            return qualifying
    return None


def _package_refund(sale: _Sale) -> CommercialRefund | None:
    """The confirmed refund that first gave back PACKAGE money (refunds past
    the pair's part of the invoice), or None. Refunding only the pair leaves
    the sale a sale."""
    refunds = db_session.execute(
        select(CommercialRefund).where(
            CommercialRefund.commercial_invoice_id == sale.invoice.id, CommercialRefund.status == "PAID"
        ).order_by(CommercialRefund.paid_at, CommercialRefund.id)
    ).scalars().all()
    running = _ZERO
    for refund in refunds:
        running += Decimal(refund.amount)
        if running > sale.other_amount:
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
    """Transaction-scoped: released by the caller's commit or rollback."""
    digest = hashlib.sha256(f"{_SELLER_LOCK_NAMESPACE}|{employee_profile_id}".encode("utf-8")).hexdigest()
    db_session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest[:15], 16)})


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
        rule = ladder_rule_for(sale.seller_profile_id, qualifying.confirmed_at)
        if not is_ladder_rule(rule):
            return
        row = CommissionLadderSale(
            employee_profile_id=sale.seller_profile_id, sales_order_id=sale.order.id,
            commercial_invoice_id=sale.invoice.id, subscription_id=sale.subscription.id if sale.subscription else None,
            package_plan_id=sale.plan.id, commission_rule_version_id=rule.id,
            qualifying_payment_record_id=qualifying.payment_id, qualifying_allocation_id=qualifying.allocation_id,
            qualified_at=qualifying.confirmed_at, ladder_month=ladder_month_of(qualifying.confirmed_at),
            hold_ends_at=qualifying.confirmed_at + HOLD_PERIOD, currency=sale.invoice.currency, status="COUNTED",
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
            elif counted.refunded_after_hold_at is None:
                # Default chosen here (the owner ruled only on refunds inside
                # the hold): the sale stays counted -- its commission had
                # become final -- and the refund is recorded on it.
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


def _is_renewal(subscription: Subscription, payment: PaymentRecord) -> bool:
    """A renewal is a confirmed payment that starts a NEW term for the
    subscription -- not the sale's own payment:
      - a package with no contract (Package 1, yearly): every later payment
        of at least the renewal price is one renewal year;
      - a package with a contract (Packages 2/3): monthly payments inside the
        contract are the contract itself, never renewals; a payment for a
        period after the contract ended is the new contract the doc means
        ("A new contract at the end earns the 10 JOD renewal")."""
    if subscription.contract_term_months:
        return subscription.contract_ends_on is not None and payment.payment_date > subscription.contract_ends_on
    from app.commercial_sales.packages import renewal_price

    price = renewal_price(subscription)
    if price is not None and price[1] == payment.currency and Decimal(payment.amount) < price[0]:
        return False
    return True


def _renewal_rows(payment_id: uuid.UUID) -> list[CommissionLedgerEntry]:
    return db_session.execute(
        select(CommissionLedgerEntry)
        .where(CommissionLedgerEntry.source_payment_record_id == payment_id,
               CommissionLedgerEntry.entry_kind == "RENEWAL", CommissionLedgerEntry.status != "CANCELLED")
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()


def evaluate_renewal_payment(payment: PaymentRecord, *, now: datetime | None = None) -> None:
    """Post (or take back) the 10 JOD renewal commission for one payment."""
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
    if subscription.sales_order_id is not None:
        sale = _load_sale(subscription.sales_order_id)
        if sale is not None and payment.id in _sale_payment_ids(sale):
            return  # the sale's own payment is the sale, not a renewal
    else:
        sale = None
    lock_seller(owner_profile_id)
    rows = _renewal_rows(payment.id)
    posted = sum((Decimal(r.commission_amount) for r in rows), _ZERO)
    wanted = payment.status == "CONFIRMED" and _is_renewal(subscription, payment)
    if wanted and posted == _ZERO and not rows:
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
            "commission_amount": str(RENEWAL_COMMISSION),
        })
    elif not wanted and posted > _ZERO:
        open_rows = [r for r in rows if r.status == "EARNED"]
        if payment.status == "REFUNDED" and all(r.payable_after is not None and r.payable_after <= now for r in rows):
            return  # refunded after the hold: final, like a ladder sale (see evaluate_sale)
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


def on_payment_status_written(payment: PaymentRecord, *, now: datetime | None = None) -> None:
    """correct_payment's hook (the one writer of payment status), inside its
    transaction: a later monthly payment may count a monthly sale, and a
    renewal payment earns (or loses) its 10 JOD."""
    if payment.subscription_id is None:
        return
    subscription = db_session.get(Subscription, payment.subscription_id)
    if subscription is not None and subscription.sales_order_id is not None:
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
