"""The owner's packages: the rules a price list cannot hold by itself.

Step 0 (2026-10-24). The owner decided three packages, prices PER BRANCH,
each including 1 desktop + 1 phone (Aura Growth Plan, "Your answers" and the
2026-10-24 additions):
  Package 1  250 JOD the first year (100 a year + 150 first time), then 100 a year
  Package 2  35 JOD a month, 12-month contract
  Package 3  25 JOD a month, 24-month contract
  extra devices ONLY as a pair (1 desktop + 1 phone), 50 JOD once, every
  package, never on the renewal.

They live in the catalog as data (Plan + PlanPrice + the EXTRA_DEVICE_PAIR
add-on; `flask seed-packages` writes them). What the data alone cannot say
is here, in one place, so every path applies it the same way:
  - a NEW sale costs base_price + setup_fee
    (catalog_for_sales.describe_plan_for_sale: first_sale_unit_price);
  - a renewal costs base_price only, whatever the device count (renewal_price);
  - the contract is copied onto the subscription at the sale and its end
    date fixed then (contract_terms).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.commercial_ops.renewal_dates import add_interval
from app.extensions import db_session
from app.models.catalog import Plan, PlanPrice
from app.models.subscriptions import Subscription


def contract_terms(plan: Plan, start: date | None) -> dict:
    """The Subscription fields for the contract this plan is sold on:
    {contract_term_months, contract_ends_on}. Both None for a plan with no
    contract (Package 1), or when there is no start date to count from.

    The contract is not the licence term: a monthly package's licence runs
    through the PAID month (issuance.resolve_license_term), so an unpaid
    month shows up as an expired licence -- the owner's rule is that the
    shop then keeps selling and loses online features and updates (product
    side). The contract only records what the customer committed to, for
    the later months and the commission ladder."""
    term = plan.contract_term_months
    if not term or start is None:
        return {"contract_term_months": None, "contract_ends_on": None}
    return {"contract_term_months": term, "contract_ends_on": add_interval(start, months=term)}


def renewal_price(subscription: Subscription) -> tuple[Decimal, str] | None:
    """(amount, currency) one more period of this subscription costs: its
    plan's CURRENT base_price -- never the setup fee (that was the first
    sale's), never the extra pairs (the owner: "the 100 JOD yearly renewal
    does not grow with extra devices"), so device_allowance is deliberately
    not read here. None when the plan has no current price."""
    price = db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == subscription.plan_id, PlanPrice.effective_until.is_(None))
    ).scalars().first()
    if price is None:
        return None
    return Decimal(price.base_price), price.currency


# -------------------------------------------- yearly activation (monthly) --
# Owner rule (2026-10-30): the monthly packages (Package 2: 35 a month, 12-month
# contract; Package 3: 25 a month, 24-month contract) charge the 100 JOD
# activation EVERY contract year, not only on the first sale. It needs no new
# column: the 100 is already the plan price's setup_fee (package_seed.py), and a
# monthly plan is recognisable by billing_model MONTHLY + a contract term.
# Package 1 (yearly, no contract) keeps its setup fee as a first-sale-only
# charge: its renewal is base_price alone (renewal_price above, unchanged).
CONTRACT_YEAR_MONTHS = 12


def has_yearly_activation(plan: Plan) -> bool:
    return plan.billing_model == "MONTHLY" and bool(plan.contract_term_months)


def yearly_activation_fee(plan: Plan) -> Decimal | None:
    """The activation a monthly package charges each contract year, or None
    (Package 1, any plan without a contract, or a zero fee)."""
    if not has_yearly_activation(plan):
        return None
    price = db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
    ).scalars().first()
    if price is None or not price.setup_fee or Decimal(price.setup_fee) <= 0:
        return None
    return Decimal(price.setup_fee)


def contract_start(subscription: Subscription) -> date | None:
    """The day the subscription's contract began: contract_ends_on minus the
    contract term (contract_terms stored the end as start + term, and the start
    itself is not stored -- start_date/end_date are the LICENCE term, which
    renewals move). None for a subscription without contract dates."""
    ends, term = subscription.contract_ends_on, subscription.contract_term_months
    if ends is None or not term:
        return None
    return add_interval(ends, months=-term)


def contract_year_bounds(start: date, year: int) -> tuple[date, date]:
    """[first day, first day of the next year) of contract year `year` (1-based)
    -- start + 12*(year-1) months up to start + 12*year months. The contract
    years keep counting after the contract ends (a Package 2 shop that carries
    on month to month is in contract year 2, 3, ...)."""
    return (add_interval(start, months=CONTRACT_YEAR_MONTHS * (year - 1)),
            add_interval(start, months=CONTRACT_YEAR_MONTHS * year))


def contract_year_of(subscription: Subscription, on: date) -> int | None:
    """Which contract year `on` falls in (a date before the start is year 1);
    None when the subscription has no contract dates."""
    start = contract_start(subscription)
    if start is None:
        return None
    year = 1
    while on >= contract_year_bounds(start, year)[1]:
        year += 1
    return year


_PAYING = ("PENDING", "CONFIRMED")  # a rejected, failed, voided or refunded payment is not money in


def counted_payments(subscription: Subscription) -> list:
    """Every PENDING/CONFIRMED payment of the subscription. The sale's own
    payment is recorded against the sale's invoice (or the order), not the
    subscription (measured: a subscription_id-only list missed it and quoted
    the first-sale price again), so payments allocated to or recorded for the
    sale's invoice are included. Status is a filter here and nowhere else: a
    FAILED or VOIDED payment must never make a month or an activation look paid."""
    from app.models.commercial_sales import CommercialInvoice, PaymentAllocation
    from app.models.subscriptions import PaymentRecord

    ids = set(db_session.execute(
        select(PaymentRecord.id).where(PaymentRecord.subscription_id == subscription.id,
                                       PaymentRecord.status.in_(_PAYING))
    ).scalars().all())
    if subscription.sales_order_id is not None:
        ids |= set(db_session.execute(
            select(PaymentRecord.id)
            .join(PaymentAllocation, PaymentAllocation.payment_record_id == PaymentRecord.id)
            .join(CommercialInvoice, CommercialInvoice.id == PaymentAllocation.commercial_invoice_id)
            .where(CommercialInvoice.sales_order_id == subscription.sales_order_id,
                   PaymentAllocation.reversed_at.is_(None), PaymentRecord.status.in_(_PAYING))
        ).scalars().all())
        ids |= set(db_session.execute(
            select(PaymentRecord.id)
            .join(CommercialInvoice, CommercialInvoice.id == PaymentRecord.commercial_invoice_id)
            .where(CommercialInvoice.sales_order_id == subscription.sales_order_id,
                   PaymentRecord.status.in_(_PAYING))
        ).scalars().all())
    if not ids:
        return []
    return list(db_session.execute(select(PaymentRecord).where(PaymentRecord.id.in_(ids))).scalars().all())


def activation_carried(year_amounts: list[Decimal], base: Decimal, fee: Decimal) -> bool:
    """Has the yearly activation been paid, given every counted payment of ONE
    contract year in the plan's currency?

    WHY a SUM that must split exactly, and not one payment at a time (verifier
    findings, 2026-10-30). The old test asked "is any single payment >= month +
    activation?". It failed both ways: a shop that pays 140 for four months at the
    start of year 2 (4 x 35, no activation in it) looked as if it had paid the
    100; and a shop that paid the activation as 100 and then 35 was never
    recognised, so 135 was quoted all year.

    Nothing records what a payment was FOR, so the rule is worked from the money:
    the year's payments carry the activation when their total is the activation
    plus a whole number of months -- total = fee + m x base, m >= 0 (m is the
    months covered). 140 is not (40 left over: it is four months, so the 100 is
    still owed); 100 + 35 and a single 135 both are; so are 170 (the activation +
    two months) and any split of those. A total that does not split exactly (a
    part-paid activation such as 100 then 40) is NOT taken as carried: quoting the
    activation again is the safe direction, and staff can enter the real amount (a
    different amount is flagged and audited, never refused). The one case the
    money cannot separate is a plan whose fee is itself a whole number of months
    (Package 3: 100 = 4 x 25, so 125 is both "5 months" and "activation + 1");
    there the quote itself (125) must count as carried, so it does.
    """
    total = sum(year_amounts, Decimal(0))
    if base <= 0 or total < fee:
        return False
    return (total - fee) % base == 0


def next_payment_due(subscription: Subscription, payment_date: date | None = None) -> tuple[Decimal, str] | None:
    """(amount, currency) a payment of this subscription dated `payment_date`
    (default: Jordan's today) should be. None when the plan has no current price.

    Verifier finding (2026-10-30, MEDIUM): this used to count payment RECORDS
    and quote the activation on "the 13th". Contract years run by DATE, so a
    split payment (deposit + balance), a combined one (70 JOD for two months) or
    a lapse moved the +100 to the wrong month. Now:

      * first payment of a subscription (nothing counted yet): base + setup fee,
        as a new sale costs (catalog_for_sales first_sale_unit_price);
      * a monthly package, contract year 1: base. The sale's own payment carried
        the setup fee, however it was split;
      * a monthly package, contract year k >= 2 (start + 12*(k-1) months, Jordan
        business dates): base + the yearly activation, due ONCE -- until a counted
        payments dated in that year have "carried" it (the plan's currency). A
        month's 35, or 70 for two months, cannot contain the activation, so the
        next payment is asked for it. Worked out from payment amount, date and
        status alone (nothing is recorded per payment to say what it was for): see
        activation_carried -- the year's SUM must be the activation plus a whole
        number of months, so 100 then 35 carries it and 140 (four months) does not;
      * Package 1 / any plan without a contract: base after the first payment;
      * a monthly package with no contract dates stored is treated as year 1: it
        can never quote an activation it cannot place on the calendar.
    """
    from app.operational_reports.periods import jordan_today

    renewal = renewal_price(subscription)
    if renewal is None:
        return None
    base, currency = renewal
    plan = db_session.get(Plan, subscription.plan_id)
    fee = yearly_activation_fee(plan)
    on = payment_date or jordan_today()
    year = contract_year_of(subscription, on) if fee is not None else None
    counted = counted_payments(subscription)
    if year is None or year == 1:
        if counted:
            return base, currency
        price = db_session.execute(
            select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
        ).scalars().first()
        return base + Decimal(price.setup_fee or 0), currency
    first_day, next_year_first_day = contract_year_bounds(contract_start(subscription), year)
    carried = activation_carried(
        [Decimal(p.amount) for p in counted
         if first_day <= p.payment_date < next_year_first_day and p.currency == currency],
        base, fee)
    return (base if carried else base + fee), currency


# ------------------------------------------ what staff are shown and warned --
# Verifier finding (2026-10-30, HIGH): payment_due / next_payment_due had NO
# production caller. Staff record a month-13 payment by hand on the
# subscription screen, and nothing told them the 100 JOD yearly activation was
# part of it -- so it would simply never be charged. The three pieces below put
# the number in front of the person recording the money, and keep a trail when
# they enter something else:
#   next_payment_quote  -- the number, split into "month" and "activation" so a
#                          screen can say what it is made of;
#   check_payment_amount -- the entered amount against it (a different amount is
#                          legitimate -- a part payment, two months at once -- so
#                          it is never refused, only flagged and audited);
#   AmountCheck.audit_state -- what lands in the payment's audit entry.
@dataclass(frozen=True)
class PaymentQuote:
    currency: str
    month: Decimal        # the package's own price for a month (renewal_price)
    activation: Decimal   # the yearly activation this payment carries, else 0

    @property
    def total(self) -> Decimal:
        return self.month + self.activation


def next_payment_quote(subscription: Subscription, payment_date: date | None = None) -> PaymentQuote | None:
    """What the NEXT payment of a monthly-package subscription should be, in
    parts. None for any plan without a yearly activation (Package 1, plans with
    no contract): their payments are left exactly as they were."""
    plan = db_session.get(Plan, subscription.plan_id)
    if plan is None or not has_yearly_activation(plan):
        return None
    due = next_payment_due(subscription, payment_date)
    renewal = renewal_price(subscription)
    if due is None or renewal is None:
        return None
    return PaymentQuote(currency=due[1], month=renewal[0], activation=due[0] - renewal[0])


@dataclass(frozen=True)
class AmountCheck:
    """The amount staff entered, set against what was due."""
    quote: PaymentQuote
    entered: Decimal
    entered_currency: str

    @property
    def same_currency(self) -> bool:
        return (self.entered_currency or "").upper() == self.quote.currency.upper()

    @property
    def matches(self) -> bool:
        return self.same_currency and self.entered == self.quote.total

    @property
    def difference(self) -> Decimal | None:
        """entered - due; None when the currencies differ (there is no honest
        subtraction between JOD and USD)."""
        return self.entered - self.quote.total if self.same_currency else None

    def audit_state(self) -> dict:
        """Goes into the payment's PAYMENT_RECORD_CREATED entry (after_state
        -> "due_check"): the audit trail records what was asked, what was
        entered and the gap, so a short or a combined payment is visible later."""
        return {
            "expected_amount": str(self.quote.total),
            "expected_currency": self.quote.currency,
            "month": str(self.quote.month),
            "activation": str(self.quote.activation),
            "entered_amount": str(self.entered),
            "entered_currency": self.entered_currency,
            "difference": None if self.difference is None else str(self.difference),
            "matches": self.matches,
        }


def check_payment_amount(subscription: Subscription | None, amount: Decimal, currency: str,
                         payment_date: date | None = None) -> AmountCheck | None:
    """None when the subscription has no yearly activation (nothing to compare)."""
    if subscription is None:
        return None
    quote = next_payment_quote(subscription, payment_date)
    if quote is None:
        return None
    return AmountCheck(quote=quote, entered=Decimal(amount), entered_currency=currency or "")
