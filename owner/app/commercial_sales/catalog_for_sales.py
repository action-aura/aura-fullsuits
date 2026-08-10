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


def _entitlement_summary(rows: list[tuple[EntitlementDefinition, dict]]) -> dict:
    return {ed.entitlement_code: value for ed, value in rows}


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
