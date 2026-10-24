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
