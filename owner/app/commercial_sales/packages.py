"""The owner's packages: the rules a price list cannot hold by itself.

Step 0 (2026-10-24). The owner decided three packages, prices PER BRANCH,
each including 1 desktop + 1 phone (Aura Growth Plan, "Your answers" and the
2026-10-24 additions):
  Package 1  250 JOD the first year (100 a year + 150 first time), then 100 a year
  Package 2  35 JOD a month, 12-month contract
  Package 3  20 JOD a month, 24-month contract
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


def payment_due(subscription: Subscription, payment_number: int) -> tuple[Decimal, str] | None:
    """(amount, currency) the subscription's `payment_number`-th monthly/periodic
    payment should be (1 = the sale's own first payment).

      - payment 1: base_price + setup_fee, same as a new sale
        (catalog_for_sales first_sale_unit_price);
      - a monthly package: payments 2..12 are base_price; payment 13, 25, ... (the
        first month of each later contract year) add the yearly activation;
      - Package 1 / any plan without a contract: base_price from payment 2 on,
        exactly renewal_price.
    Assumes one payment per month for a monthly package -- the 13th payment is
    month 13. None when the plan has no current price."""
    if payment_number < 1:
        raise ValueError("payment_number starts at 1")
    renewal = renewal_price(subscription)
    if renewal is None:
        return None
    base, currency = renewal
    plan = db_session.get(Plan, subscription.plan_id)
    fee = yearly_activation_fee(plan)
    if payment_number == 1:
        price = db_session.execute(
            select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
        ).scalars().first()
        return base + Decimal(price.setup_fee or 0), currency
    if fee is not None and (payment_number - 1) % CONTRACT_YEAR_MONTHS == 0:
        return base + fee, currency
    return base, currency


def next_payment_due(subscription: Subscription) -> tuple[Decimal, str] | None:
    """payment_due() for the NEXT payment: one more than the subscription's
    PENDING/CONFIRMED payments so far (a rejected or failed payment is not a
    month paid). The sale's own first payment counts as #1 even though it is
    recorded against the sale's invoice, not the subscription (measured: a
    subscription_id-only count started one month early and quoted the
    activation on month 12), so payments allocated to the sale's invoice are
    counted too."""
    from app.models.commercial_sales import CommercialInvoice, PaymentAllocation
    from app.models.subscriptions import PaymentRecord

    paying = ("PENDING", "CONFIRMED")
    ids = set(db_session.execute(
        select(PaymentRecord.id).where(PaymentRecord.subscription_id == subscription.id,
                                       PaymentRecord.status.in_(paying))
    ).scalars().all())
    if subscription.sales_order_id is not None:
        ids |= set(db_session.execute(
            select(PaymentRecord.id)
            .join(PaymentAllocation, PaymentAllocation.payment_record_id == PaymentRecord.id)
            .join(CommercialInvoice, CommercialInvoice.id == PaymentAllocation.commercial_invoice_id)
            .where(CommercialInvoice.sales_order_id == subscription.sales_order_id,
                   PaymentAllocation.reversed_at.is_(None), PaymentRecord.status.in_(paying))
        ).scalars().all())
        ids |= set(db_session.execute(
            select(PaymentRecord.id)
            .join(CommercialInvoice, CommercialInvoice.id == PaymentRecord.commercial_invoice_id)
            .where(CommercialInvoice.sales_order_id == subscription.sales_order_id,
                   PaymentRecord.status.in_(paying))
        ).scalars().all())
    return payment_due(subscription, len(ids) + 1)
