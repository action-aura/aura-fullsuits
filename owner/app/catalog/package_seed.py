"""`flask seed-packages` -- the owner's three Aura Retail packages, as catalog data.

Step 0 (2026-10-24). The owner decided (Aura Growth Plan, "Your answers" and
the 2026-10-24 additions), prices PER BRANCH, each package including 1
desktop + 1 phone:
  Package 1  250 JOD the first year, then 100 JOD a year   -> ANNUAL, 100 + 150 setup fee
  Package 2  35 JOD a month, 12-month contract              -> MONTHLY, contract 12
  Package 3  20 JOD a month, 24-month contract              -> MONTHLY, contract 24
  extra devices only as a pair (1 desktop + 1 phone), 50 JOD once -> EXTRA_DEVICE_PAIR

Prices are data in this product (catalog/services.py), so this is an
explicit, idempotent release step like `seed-plans` -- never folded into
`seed-catalog`, whose rows every test fixture gets and which must stay inert.
It CREATES what is missing and never changes what exists: a package or a
price someone already changed is reported, not overwritten (the catalog
screen is where a price is changed, with its own history).

Aura Retail only. Aura Clinic is a separate product with its own licensing
(owner, 2026-10-18) and no prices were decided for it; its placeholder plan
is left alone.

It also REPORTS what would still be sold next to the packages: any other
sellable Aura Retail plan (the $99 placeholder, a rehearsal plan) and a
single EXTRA_DEVICE add-on that is on sale (the quote screen could still
sell one device at a time). With --retire-other-retail-plans it retires the
other plans (retirement_date = today) -- except a plan an open sale still
needs (a DRAFT or CONFIRMED order), which is reported and kept, because
confirming that sale requires its package to still be sellable.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.catalog.services import seed_canonical_catalog
from app.commercial_sales.catalog_for_sales import (
    EXTRA_DEVICE_ADDON_CODE,
    EXTRA_DEVICE_PAIR_ADDON_CODE,
    describe_plan_for_sale,
)
from flask_babel import lazy_gettext as _l

from app.extensions import db_session
from app.models.base import utcnow
from app.models.catalog import Addon, Plan, PlanPrice, Product
from app.models.commercial_sales import SalesOrder, SalesOrderLine

PRODUCT_CODE = "AURA_RETAIL"
CURRENCY = "JOD"
INCLUDED_DEVICES = 2  # 1 desktop + 1 phone
PAIR_PRICE = Decimal("50.00")

# (plan_code, name, billing_model, billing_interval_months, contract_term_months, base_price, setup_fee)
PACKAGES = (
    ("AURA_RETAIL_P1_YEARLY", "Aura Retail - Package 1 (yearly)", "ANNUAL", 12, None,
     Decimal("100.00"), Decimal("150.00")),
    ("AURA_RETAIL_P2_MONTHLY_12", "Aura Retail - Package 2 (monthly, 12-month contract)", "MONTHLY", 1, 12,
     Decimal("35.00"), Decimal("0.00")),
    ("AURA_RETAIL_P3_MONTHLY_24", "Aura Retail - Package 3 (monthly, 24-month contract)", "MONTHLY", 1, 24,
     Decimal("20.00"), Decimal("0.00")),
)
# Msgids for the Arabic UI (see i18n_format.catalog_label). Never read at
# runtime -- they exist so `pybabel extract` keeps these strings in the
# catalogs; the stored plan names above stay English.
_PACKAGE_DISPLAY_MSGIDS = (
    _l("Aura Retail - Package 1 (yearly)"),
    _l("Aura Retail - Package 2 (monthly, 12-month contract)"),
    _l("Aura Retail - Package 3 (monthly, 24-month contract)"),
    _l("Extra device pair (1 desktop + 1 phone)"),
)
PACKAGE_CODES = tuple(p[0] for p in PACKAGES)

_OPEN_ORDER_STATUSES = ("DRAFT", "CONFIRMED")


def _current_price(plan_id):
    return db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == plan_id, PlanPrice.effective_until.is_(None))
    ).scalars().first()


def _seed_package(product: Product, spec: tuple, today: date, report: dict) -> None:
    code, name, billing_model, interval, contract, base, setup = spec
    plan = db_session.execute(select(Plan).where(Plan.plan_code == code)).scalars().first()
    if plan is None:
        plan = Plan(
            plan_code=code, product_id=product.id, name=name, lifecycle_status="AVAILABLE",
            billing_model=billing_model, billing_interval_months=interval, contract_term_months=contract,
            currency=CURRENCY, included_device_count=INCLUDED_DEVICES, effective_date=today,
        )
        db_session.add(plan)
        db_session.flush()
        report["created"].append(code)
    else:
        wanted = (product.id, billing_model, interval, contract, INCLUDED_DEVICES)
        found = (plan.product_id, plan.billing_model, plan.billing_interval_months, plan.contract_term_months,
                 plan.included_device_count)
        if found != wanted:
            report["differs"].append({
                "plan_code": code,
                "found": {"billing_model": plan.billing_model, "billing_interval_months": plan.billing_interval_months,
                          "contract_term_months": plan.contract_term_months,
                          "included_device_count": plan.included_device_count},
            })
    price = _current_price(plan.id)
    if price is None:
        db_session.add(PlanPrice(plan_id=plan.id, base_price=base, setup_fee=setup, currency=CURRENCY,
                                 effective_from=today))
        report["prices_added"].append(code)
    elif (Decimal(price.base_price), Decimal(price.setup_fee), price.currency) != (base, setup, CURRENCY):
        report["differs"].append({
            "plan_code": code,
            "current_price": {"base_price": str(price.base_price), "setup_fee": str(price.setup_fee),
                              "currency": price.currency},
            "owner_price": {"base_price": str(base), "setup_fee": str(setup), "currency": CURRENCY},
        })


def _seed_pair(report: dict) -> None:
    pair = db_session.execute(select(Addon).where(Addon.addon_code == EXTRA_DEVICE_PAIR_ADDON_CODE)).scalars().first()
    if pair.price is None:
        pair.price = PAIR_PRICE
        pair.currency = CURRENCY
        report["pair"] = "priced"
    if (Decimal(pair.price), pair.currency) != (PAIR_PRICE, CURRENCY):
        report["differs"].append({"addon_code": pair.addon_code, "price": str(pair.price), "currency": pair.currency})
        return
    if pair.availability_status != "AVAILABLE":
        pair.availability_status = "AVAILABLE"
        report["pair"] = "put on sale"


def _has_open_sale(plan_id) -> bool:
    return db_session.execute(
        select(SalesOrder.id)
        .join(SalesOrderLine, SalesOrderLine.sales_order_id == SalesOrder.id)
        .where(SalesOrderLine.plan_id == plan_id, SalesOrder.status.in_(_OPEN_ORDER_STATUSES))
    ).first() is not None


def _other_retail_plans(product: Product, today: date, retire: bool, report: dict) -> None:
    plans = db_session.execute(
        select(Plan).where(Plan.product_id == product.id, Plan.plan_code.not_in(PACKAGE_CODES)).order_by(Plan.plan_code)
    ).scalars().all()
    for plan in plans:
        if describe_plan_for_sale(plan.id, as_of=today) is None:
            continue
        if not retire:
            report["other_sellable_retail_plans"].append(plan.plan_code)
        elif _has_open_sale(plan.id):
            report["kept_for_open_sales"].append(plan.plan_code)
        else:
            plan.retirement_date = today
            report["retired"].append(plan.plan_code)


def seed_packages(*, retire_other_retail_plans: bool = False, actor_staff_user_id=None) -> dict:
    seed_canonical_catalog()  # idempotent; makes sure EXTRA_DEVICE_PAIR exists on an older catalog
    product = db_session.execute(select(Product).where(Product.product_code == PRODUCT_CODE)).scalars().first()
    if product is None:
        raise ValueError("Aura Retail is not in the catalog -- run 'flask seed-catalog' first.")
    today = utcnow().date()
    report = {"created": [], "prices_added": [], "differs": [], "pair": "unchanged",
              "other_sellable_retail_plans": [], "retired": [], "kept_for_open_sales": [],
              "single_extra_device_on_sale": False}
    for spec in PACKAGES:
        _seed_package(product, spec, today, report)
    _seed_pair(report)
    _other_retail_plans(product, today, retire_other_retail_plans, report)
    single = db_session.execute(select(Addon).where(Addon.addon_code == EXTRA_DEVICE_ADDON_CODE)).scalars().first()
    report["single_extra_device_on_sale"] = bool(single is not None and single.availability_status == "AVAILABLE")
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="PACKAGES_SEEDED",
        entity_type="catalog", entity_public_id=PRODUCT_CODE, after_state=report,
    )
    return report
