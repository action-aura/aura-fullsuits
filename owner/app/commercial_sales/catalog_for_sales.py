"""Phase 9.5D Milestone 4 -- sales-facing catalog read service.

Read-only wrapper over the existing catalog authority (app/catalog/,
app/models/catalog.py) -- does not duplicate Product/Plan/PlanPrice/Addon/
Entitlement, only shapes their current-effective state for Quote/Order
line selection. No employee without pricing.manage/catalog.manage can
reach a mutating catalog path through this module (there are none here).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from flask import current_app
from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import (
    Addon,
    AddonEntitlement,
    EntitlementDefinition,
    Plan,
    PlanEntitlement,
    PlanPrice,
    Product,
    ProductPlatform,
)
from app.models.base import utcnow

# Real, minimal, honestly-scoped discount policy: the existing catalog
# schema (Milestone 1's audit) has no per-plan/per-addon/per-employee
# discount-limit column anywhere -- introducing one would be new catalog
# schema, out of this module's "read-only wrapper" scope. A single global
# threshold is the smallest real implementation: any line discount at or
# below this percentage of the line's gross needs no approval; above it,
# or any unit-price override, or any zero-price line, always requires one
# (Milestone 6). This is a real, named limitation -- a true per-employee/
# per-plan limit is a documented gap for a later phase, not silently
# assumed solved.
DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL = Decimal("10")

# The seeded add-on (app/catalog/services.py::_CANONICAL_ADDONS) whose
# quantity on an order line is a number of devices beyond the plan's
# included count. Read by fulfillment (licence device limit) and by the
# one-screen sale (its price). Matched by code, not by a new column.
EXTRA_DEVICE_ADDON_CODE = "EXTRA_DEVICE"

# The owner's FINAL rule (2026-10-31, replacing the 2026-10-24 "pairs only"
# rule): extra devices are sold ONE KIND AT A TIME and are strictly typed --
# an extra phone (Android only) and an extra desktop (Windows only), 50 JOD
# each, once, tax-inclusive, the same on every package, never on the renewal.
# One unit of either add-on is ONE device of that kind, so an order line's
# quantity is the number of phones / desktops. Seeded PLANNED by
# seed_canonical_catalog (inert), priced and put on sale by `flask
# seed-packages`. The one-screen sale sells only these two.
EXTRA_PHONE_ADDON_CODE = "EXTRA_PHONE"
EXTRA_DESKTOP_ADDON_CODE = "EXTRA_DESKTOP"

# RETIRED for sale (seed-packages marks the add-on RETIRED) but kept for
# history: orders made while "1 desktop + 1 phone for one price" was the offer
# still carry this line, and fulfilment / refunds / the ledger must keep
# reading them. A unit is one desktop AND one phone.
EXTRA_DEVICE_PAIR_ADDON_CODE = "EXTRA_DEVICE_PAIR"

# What one unit of each device add-on gives a licence, as
# (extra desktops, extra phones, untyped any-kind devices). License.device_limit
# stays the TOTAL every existing reader uses; the first two numbers also go to
# License.extra_desktop_slots / extra_phone_slots so activation can admit only
# the bought KIND (commercial_ops/device_slot_ops.py::fits_typed_slots). The
# single EXTRA_DEVICE (quote screen / old orders) is untyped, any kind, as it
# always was. Matched by code, like EXTRA_DEVICE_ADDON_CODE, not by a new column.
DEVICE_SLOTS_BY_ADDON_CODE = {
    EXTRA_DEVICE_ADDON_CODE: (0, 0, 1),
    EXTRA_DEVICE_PAIR_ADDON_CODE: (1, 1, 0),
    EXTRA_DESKTOP_ADDON_CODE: (1, 0, 0),
    EXTRA_PHONE_ADDON_CODE: (0, 1, 0),
}

# Devices one unit of each device add-on adds in TOTAL (the sum of the three
# numbers above). The commission ledger and refunds key on this dict, so every
# device add-on -- including the typed ones -- is excluded from the commission
# base exactly like extra devices always were.
DEVICES_PER_UNIT_BY_ADDON_CODE = {code: sum(slots) for code, slots in DEVICE_SLOTS_BY_ADDON_CODE.items()}


def _entitlement_summary(rows: list[tuple[EntitlementDefinition, dict]]) -> dict:
    return {ed.entitlement_code: value for ed, value in rows}


def _yearly_activation_fee(plan):
    from app.commercial_sales.packages import yearly_activation_fee

    return yearly_activation_fee(plan)


CLINIC_PRODUCT_CODE = "AURA_CLINIC"


def open_to_sellers(described: dict) -> bool:
    """May a SELLER offer this described plan (a describe_plan_for_sale result)?

    Aura Clinic is not sold yet: its plan ("Aura Clinic Standard", 99 USD) sits
    in the catalog as a sellable plan and used to appear in the sellers'
    package dropdowns. The customer-onboarding flag OWNER_CLINIC_ACCOUNTS_ENABLED
    is the one switch for "Clinic is live", so the seller screens follow it
    instead of a hard-coded plan code. There is no per-plan 'sellable by
    sellers' column (adding one is a schema change); the Retail test plans are
    handled in data instead -- `flask seed-packages --retire-other-retail-plans`
    retires everything but the owner's three packages. Not applied to
    describe_plan_for_sale itself: the quote/order pipeline keeps its own bar."""
    if described["product_code"] == CLINIC_PRODUCT_CODE and not current_app.config.get("CLINIC_ACCOUNTS_ENABLED"):
        return False
    return True


def describe_plan_for_sale(plan_id: uuid.UUID, *, as_of: date | None = None) -> dict | None:
    """One Plan's current sellable state, or None if inactive/expired/not
    found -- callers must treat None as 'not sellable', never fall back to
    a stale cached value."""
    as_of = as_of or utcnow().date()
    plan = db_session.get(Plan, plan_id)
    if plan is None:
        return None

    product = db_session.get(Product, plan.product_id)
    if product is None or not product.is_sellable or not product.is_active:
        return None
    if plan.effective_date is None or plan.effective_date > as_of:
        return None
    if plan.retirement_date is not None and plan.retirement_date <= as_of:
        return None

    current_price = db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
    ).scalars().first()
    if current_price is None:
        return None

    platforms = db_session.execute(
        select(ProductPlatform).where(ProductPlatform.product_id == product.id, ProductPlatform.supported.is_(True))
    ).scalars().all()

    entitlement_rows = db_session.execute(
        select(EntitlementDefinition, PlanEntitlement.value)
        .join(PlanEntitlement, PlanEntitlement.entitlement_definition_id == EntitlementDefinition.id)
        .where(PlanEntitlement.plan_id == plan.id)
    ).all()

    return {
        "plan_id": str(plan.id),
        "plan_code": plan.plan_code,
        "product_id": str(product.id),
        "product_code": product.product_code,
        "name": plan.name,
        "billing_model": plan.billing_model,
        "billing_interval_months": plan.billing_interval_months,
        "included_device_count": plan.included_device_count,
        "max_device_count": plan.max_device_count,
        "price_version_id": str(current_price.id),
        "unit_price": current_price.base_price,
        "setup_fee": current_price.setup_fee,
        # Step 0 (2026-10-24): what a NEW sale of this package costs -- the
        # first period plus the one-time setup fee (the owner's Package 1:
        # 100 a year + 150 first time = 250). Before, setup_fee was stored
        # and charged nowhere: the one-screen sale and the quote pipeline
        # both priced base_price only (P1 sold for 100). Both now price a
        # new sale from this one key, so one package never has two prices.
        "first_sale_unit_price": Decimal(current_price.base_price) + Decimal(current_price.setup_fee or 0),
        # What each later period costs: base_price alone -- no setup fee,
        # and never the extra pairs (the owner: the 100 JOD renewal stays
        # 100 however many pairs the shop bought). packages.renewal_price.
        "renewal_unit_price": Decimal(current_price.base_price),
        "contract_term_months": plan.contract_term_months,
        # Monthly packages charge the setup fee (the 100 JOD activation) again at
        # the start of every later contract year; None for Package 1.
        # packages.next_payment_due.
        "yearly_activation_fee": _yearly_activation_fee(plan),
        "currency": current_price.currency,
        "effective_start": plan.effective_date.isoformat() if plan.effective_date else None,
        "effective_end": plan.retirement_date.isoformat() if plan.retirement_date else None,
        "supported_platform_codes": [pp.platform.platform_code for pp in platforms],
        "entitlement_summary": _entitlement_summary(entitlement_rows),
        "discount_permitted": True,
        "max_discount_percentage_without_approval": DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL,
        "price_override_requires_approval": True,
    }


def describe_addon_for_sale(addon_id: uuid.UUID) -> dict | None:
    addon = db_session.get(Addon, addon_id)
    if addon is None or addon.availability_status != "AVAILABLE":
        return None

    product = db_session.get(Product, addon.product_id)
    if product is None or not product.is_sellable or not product.is_active:
        return None
    if addon.price is None:
        return None

    entitlement_rows = db_session.execute(
        select(EntitlementDefinition, AddonEntitlement.value)
        .join(AddonEntitlement, AddonEntitlement.entitlement_definition_id == EntitlementDefinition.id)
        .where(AddonEntitlement.addon_id == addon.id)
    ).all()

    return {
        "addon_id": str(addon.id),
        "addon_code": addon.addon_code,
        "product_id": str(product.id),
        "product_code": product.product_code,
        "name": addon.name,
        # Addon has no PlanPrice-style versioned history (Milestone 1's
        # audit) -- addon.id itself stands in for price_version_id, since
        # there is no separate historical price row to snapshot. A real,
        # named limitation: an Addon price change is not individually
        # versioned the way Plan prices are.
        "price_version_id": str(addon.id),
        "unit_price": addon.price,
        "currency": addon.currency,
        "entitlement_summary": _entitlement_summary(entitlement_rows),
        "discount_permitted": True,
        "max_discount_percentage_without_approval": DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL,
        "price_override_requires_approval": True,
    }


def requires_line_approval(
    *, unit_price: Decimal, override_unit_price: Decimal | None, discount_amount: Decimal, line_gross: Decimal
) -> bool:
    """Real, minimal exception-detection rule (Milestone 6 consumes this):
    any price override, any zero-price line, or any discount exceeding the
    global percentage threshold. Purely a predicate -- never itself creates
    or resolves a CommercialApproval row."""
    if override_unit_price is not None and override_unit_price != unit_price:
        return True
    if override_unit_price == Decimal("0") or (override_unit_price is None and unit_price == Decimal("0")):
        return True
    if line_gross > 0 and discount_amount > 0:
        discount_pct = (discount_amount / line_gross) * Decimal("100")
        if discount_pct > DEFAULT_MAX_DISCOUNT_PERCENTAGE_WITHOUT_APPROVAL:
            return True
    return False
