"""Commercial catalog services (Part I/J)."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.base import utcnow
from app.models.catalog import (
    Addon,
    AddonEntitlement,
    EntitlementDefinition,
    Plan,
    PlanEntitlement,
    PlanPrice,
    Platform,
    Product,
    ProductPlatform,
    ProductVersion,
    ReleaseChannel,
)

ALLOWED_ADDON_STATUSES = ("DRAFT", "PLANNED", "PILOT", "AVAILABLE", "RETIRED")

_CANONICAL_PRODUCTS = [
    ("AURA_RETAIL", "Aura Retail"),
    ("AURA_CLINIC", "Aura Clinic"),
]
_CANONICAL_PLATFORMS = [("WINDOWS", "Windows"), ("ANDROID", "Android")]
_CANONICAL_CHANNELS = [
    ("STABLE", "Stable"), ("RC", "Release Candidate"), ("PILOT", "Pilot"), ("BETA", "Beta"),
]
_CANONICAL_ENTITLEMENTS = [
    ("max_devices", "integer", "Maximum concurrently-registered devices"),
    ("allowed_platforms", "list", "Platforms the license may activate on"),
    ("release_channel", "string", "Release channel the license may install from"),
    ("backup_enabled", "boolean", "Backup/restore feature enabled"),
    ("low_stock_alerts_enabled", "boolean", "Retail low-stock alerts (not yet built)"),
    ("daily_report_enabled", "boolean", "Retail daily closing report (not yet built)"),
    ("whatsapp_notifications_enabled", "boolean", "WhatsApp notifications (not yet built)"),
    ("sms_notifications_enabled", "boolean", "SMS notifications (not yet built)"),
    ("digital_receipts_enabled", "boolean", "Digital receipts (not yet built)"),
    ("owner_dashboard_enabled", "boolean", "Customer-facing Owner dashboard (not yet built)"),
]
# (addon_code, product_code, name, availability_status) -- status is never AVAILABLE
# for a feature that has not actually been built anywhere in this codebase (Part J).
_CANONICAL_ADDONS = [
    ("RETAIL_LOW_STOCK_ALERTS", "AURA_RETAIL", "Low Stock Alerts", "DRAFT"),
    ("RETAIL_DAILY_CLOSING_REPORT", "AURA_RETAIL", "Daily Closing Report", "DRAFT"),
    ("RETAIL_WHATSAPP_NOTIFICATIONS", "AURA_RETAIL", "WhatsApp Notifications", "DRAFT"),
    ("RETAIL_SMS_NOTIFICATIONS", "AURA_RETAIL", "SMS Notifications", "DRAFT"),
    ("RETAIL_DIGITAL_RECEIPTS", "AURA_RETAIL", "Digital Receipts", "DRAFT"),
    ("RETAIL_OWNER_DASHBOARD", "AURA_RETAIL", "Customer Owner Dashboard", "DRAFT"),
    ("CLOUD_BACKUP", "AURA_RETAIL", "Cloud Backup", "DRAFT"),
    ("EXTRA_DEVICE", "AURA_RETAIL", "Extra Device", "PLANNED"),
    ("PRIORITY_SUPPORT", "AURA_RETAIL", "Priority Support", "PLANNED"),
    # The business owner's price list, 2026-09-05: a new branch is a paid
    # add-on (250 JOD). Seeded PLANNED like everything else here -- nothing
    # in either product enforces a branch limit yet (no `max_branches`
    # entitlement, no gate on branch creation), so it must not read as
    # sellable until that is built. Price and availability are data, set
    # from the Owner UI, never here.
    ("EXTRA_BRANCH", "AURA_RETAIL", "Extra Branch", "PLANNED"),
]


def seed_canonical_catalog() -> dict:
    """Idempotent. Seeds ONLY canonical, non-customer, non-fake data (Part AA):
    products, platforms, release channels, entitlement definitions, and
    DRAFT/PLANNED add-on catalog rows. Never seeds a customer, subscription,
    license, or installation."""
    created = {"products": 0, "platforms": 0, "channels": 0, "entitlements": 0, "addons": 0, "product_platforms": 0}

    products = {}
    for code, name in _CANONICAL_PRODUCTS:
        row = db_session.execute(select(Product).where(Product.product_code == code)).scalars().first()
        if row is None:
            row = Product(product_code=code, name=name, commercial_status="PILOT")
            db_session.add(row)
            db_session.flush()
            created["products"] += 1
        products[code] = row

    platforms = {}
    for code, name in _CANONICAL_PLATFORMS:
        row = db_session.execute(select(Platform).where(Platform.platform_code == code)).scalars().first()
        if row is None:
            row = Platform(platform_code=code, name=name)
            db_session.add(row)
            db_session.flush()
            created["platforms"] += 1
        platforms[code] = row

    for code, name in _CANONICAL_CHANNELS:
        if db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == code)).scalars().first() is None:
            db_session.add(ReleaseChannel(channel_code=code, name=name))
            created["channels"] += 1

    for product in products.values():
        for platform in platforms.values():
            exists = db_session.execute(
                select(ProductPlatform).where(ProductPlatform.product_id == product.id, ProductPlatform.platform_id == platform.id)
            ).scalars().first()
            if exists is None:
                db_session.add(ProductPlatform(product_id=product.id, platform_id=platform.id, supported=True))
                created["product_platforms"] += 1

    entitlements = {}
    for code, value_type, description in _CANONICAL_ENTITLEMENTS:
        row = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == code)).scalars().first()
        if row is None:
            row = EntitlementDefinition(entitlement_code=code, value_type=value_type, description=description)
            db_session.add(row)
            db_session.flush()
            created["entitlements"] += 1
        entitlements[code] = row

    for addon_code, product_code, name, status in _CANONICAL_ADDONS:
        exists = db_session.execute(select(Addon).where(Addon.addon_code == addon_code)).scalars().first()
        if exists is None:
            db_session.add(Addon(addon_code=addon_code, product_id=products[product_code].id, name=name, availability_status=status))
            created["addons"] += 1

    db_session.commit()
    return created


# Placeholder-pricing seed data (launch-readiness W0.3, Part A). $99.00
# flat/ONE_TIME is NOT a real commercial price -- it exists only so a fresh
# Owner deployment can cut its very first licence key. Set real pricing from
# the Owner UI (catalog -> plan -> add price) before selling anything for
# real; docs/release/go-live-runbook.md says the same thing.
_CANONICAL_PLAN_SUFFIX = "STANDARD"
_PLACEHOLDER_BASE_PRICE = Decimal("99.00")
_PLACEHOLDER_CURRENCY = "USD"


def seed_canonical_plans() -> dict:
    """Idempotent. Seeds ONE minimal, immediately-sellable plan per canonical
    product (<PRODUCT_CODE>_STANDARD) plus its current PlanPrice.

    Without this, a brand-new Owner deployment cannot issue a single licence:
    `seed-catalog` seeds products/platforms/channels/entitlement definitions/
    DRAFT add-ons only -- `owner_plans` stays empty -- and
    `issue_license_direct` issues against a `plan_id`
    (`describe_plan_for_sale` requires an effective, priced plan, see
    commercial_sales/catalog_for_sales.py). Before this function existed the
    only way through was building a plan by hand through the UI, with
    nothing telling an operator that step exists.

    Deliberately NOT folded into seed_canonical_catalog(): that function is
    invoked by nearly every Owner test via the `seeded` fixture (~700 call
    sites), and its own established convention (see _CANONICAL_ADDONS above)
    is to seed only inert, non-purchasable scaffolding -- add-ons are seeded
    DRAFT/PLANNED, never AVAILABLE, specifically because "a feature that has
    not actually been built" must never appear sellable. A plan this
    function creates must be the opposite -- immediately live and
    purchasable, by design -- so folding it into seed_canonical_catalog()
    would silently inject a real, sellable plan into every one of that
    fixture's call sites (catalog/dashboard/plan-listing counts and
    read-models alike, with no way to audit the blast radius from here).
    This is a separate, explicit release-engineering step instead, run once
    per fresh deployment -- the same reason `create-superadmin` is its own
    command rather than folded into `seed-rbac`.

    Direct model construction (not create_plan()/add_plan_price()) to match
    seed_canonical_catalog()'s own idiom immediately above -- those two
    service functions are the staff-driven, audited UI-route path
    (catalog/routes.py) and, notably, create_plan() unconditionally forces
    lifecycle_status="DRAFT", which this function must NOT do (see below).

    Requires seed_canonical_catalog() (i.e. `flask seed-catalog`) to have
    already run -- raises rather than silently seeding nothing a caller
    could mistake for success.
    """
    products = {
        code: db_session.execute(select(Product).where(Product.product_code == code)).scalars().first()
        for code, _name in _CANONICAL_PRODUCTS
    }
    if not any(products.values()):
        raise ValueError("No canonical products found -- run 'flask seed-catalog' first.")

    created = {"plans": 0, "prices": 0}
    today = utcnow().date()

    for code, name in _CANONICAL_PRODUCTS:
        product = products.get(code)
        if product is None:
            continue  # seed-catalog seeded a different product list than this code currently knows about; skip rather than guess

        plan_code = f"{code}_{_CANONICAL_PLAN_SUFFIX}"
        plan = db_session.execute(select(Plan).where(Plan.plan_code == plan_code)).scalars().first()
        if plan is None:
            plan = Plan(
                plan_code=plan_code,
                product_id=product.id,
                name=f"{name} Standard",
                # AVAILABLE, not create_plan()'s DRAFT default (see docstring
                # above) -- lifecycle_status is a display-only workflow label
                # nothing in this codebase currently gates issuance on (that
                # gate is effective_date/retirement_date/PlanPrice, enforced
                # by describe_plan_for_sale below), but showing "Draft" for a
                # plan that is, in fact, immediately purchasable would
                # actively mislead staff reading the catalog screen.
                lifecycle_status="AVAILABLE",
                # docs/owner/one-time-pricing-design.md: every product prices
                # as a single one-time device fee, never a subscription --
                # see issuance.py::resolve_license_term's own docstring for
                # why a ONE_TIME plan must never be given a retirement/end
                # date.
                billing_model="ONE_TIME",
                currency=_PLACEHOLDER_CURRENCY,
                # 2, not 1: the business owner's rule (2026-09-05) is that
                # every licence includes two devices by default -- the
                # manager's own device (usually a phone) and one cashier
                # till -- and each device beyond that is a paid extra. A
                # plan created from the UI can still choose otherwise.
                included_device_count=2,
                effective_date=today,
            )
            db_session.add(plan)
            db_session.flush()
            created["plans"] += 1

        current_price = db_session.execute(
            select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
        ).scalars().first()
        if current_price is None:
            db_session.add(
                PlanPrice(
                    plan_id=plan.id, base_price=_PLACEHOLDER_BASE_PRICE, currency=_PLACEHOLDER_CURRENCY, effective_from=today
                )
            )
            created["prices"] += 1

    db_session.commit()
    return created


def import_release_manifest(manifest: dict, actor_staff_user_id: uuid.UUID | None) -> dict:
    """Reconciles a Wave-manifest-shaped dict into owner_product_versions.
    Expects {"version": "1.0.0-rc.1", "windows": [{"product_code":..., "sha256":...}], "android": [...]}."""
    created, skipped_duplicates, rejected = [], [], []
    version_label = manifest.get("version")
    if not version_label:
        raise ValueError("Manifest missing top-level 'version'.")

    entries = []
    for platform_code, key in (("WINDOWS", "windows"), ("ANDROID", "android")):
        for item in manifest.get(key, []):
            entries.append({**item, "platform_code": platform_code})

    for entry in entries:
        product_code = entry.get("product_code")
        checksum = entry.get("sha256")
        product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().first()
        platform = db_session.execute(
            select(Platform).where(Platform.platform_code == entry["platform_code"])
        ).scalars().first()
        if product is None or platform is None:
            rejected.append({"entry": entry, "reason": "unknown product_code or platform_code"})
            continue

        existing = db_session.execute(
            select(ProductVersion).where(
                ProductVersion.product_id == product.id,
                ProductVersion.platform_id == platform.id,
                ProductVersion.version == version_label,
            )
        ).scalars().first()
        if existing is not None:
            if checksum and existing.artifact_checksum_sha256 and existing.artifact_checksum_sha256 != checksum:
                rejected.append({"entry": entry, "reason": "checksum conflicts with an already-imported release"})
            else:
                skipped_duplicates.append({"product_code": product_code, "platform": entry["platform_code"]})
            continue

        channel = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code == "RC")).scalars().first()
        row = ProductVersion(
            product_id=product.id,
            platform_id=platform.id,
            release_channel_id=channel.id if channel else None,
            version=version_label,
            artifact_checksum_sha256=checksum,
            artifact_path=entry.get("path"),
            artifact_size_bytes=entry.get("size_bytes"),
            imported_at=utcnow(),
            created_by_staff_user_id=actor_staff_user_id,
            # publication_state defaults to DRAFT (Phase 9R M10) -- import is
            # never publication. A human (or the CI pipeline, M16) must call
            # publish_release() explicitly before this version is visible to
            # any client-facing authorization check.
        )
        db_session.add(row)
        created.append({"product_code": product_code, "platform": entry["platform_code"], "version": version_label})

    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="RELEASE_MANIFEST_IMPORTED",
        entity_type="product_version",
        entity_public_id=version_label,
        after_state={"created": created, "skipped_duplicates": skipped_duplicates, "rejected": rejected},
    )
    return {"created": created, "skipped_duplicates": skipped_duplicates, "rejected": rejected}


def add_plan_price(plan: Plan, base_price, currency: str, effective_from: date, actor_staff_user_id) -> PlanPrice:
    """Never overwrites a historical price -- closes the previous current row's
    effective_until and inserts a new one (Part J)."""
    current = db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
    ).scalars().first()
    if current is not None:
        current.effective_until = effective_from
    new_price = PlanPrice(plan_id=plan.id, base_price=base_price, currency=currency, effective_from=effective_from)
    db_session.add(new_price)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PLAN_PRICE_ADDED",
        entity_type="plan",
        entity_public_id=str(plan.id),
        after_state={"base_price": str(base_price), "currency": currency, "effective_from": str(effective_from)},
    )
    return new_price


def create_plan(fields: dict, actor_staff_user_id) -> Plan:
    plan = Plan(lifecycle_status="DRAFT", **fields)
    db_session.add(plan)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="PLAN_CREATED",
        entity_type="plan",
        entity_public_id=str(plan.id),
        after_state={"plan_code": plan.plan_code, "billing_model": plan.billing_model},
    )
    return plan


def set_addon_availability(addon: Addon, status: str, actor_staff_user_id) -> None:
    if status not in ALLOWED_ADDON_STATUSES:
        raise ValueError(f"Invalid availability status: {status}")
    before = addon.availability_status
    addon.availability_status = status
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="ADDON_AVAILABILITY_CHANGED",
        entity_type="addon",
        entity_public_id=str(addon.id),
        before_state={"availability_status": before},
        after_state={"availability_status": status},
    )


def read_active_catalog(as_of: date | None = None) -> list[dict]:
    """Phase 9.5A Milestone 22/9 -- CatalogReadService. Read-only: employees
    without pricing.manage/catalog.manage select from this list, never edit
    it (price-authority-rules.md). 'Active' = effective_date <= as_of <
    retirement_date, the same effective/retired-date-window pattern already
    used by ActivationPolicy/DevicePolicyProfile resolution, not the
    workflow-shaped lifecycle_status field."""
    as_of = as_of or utcnow().date()
    plans = db_session.execute(
        select(Plan)
        .where(Plan.effective_date.is_not(None), Plan.effective_date <= as_of)
        .where((Plan.retirement_date.is_(None)) | (Plan.retirement_date > as_of))
    ).scalars().all()

    result = []
    for plan in plans:
        current_price = db_session.execute(
            select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
        ).scalars().first()
        result.append(
            {
                "plan_id": str(plan.id),
                "plan_code": plan.plan_code,
                "name": plan.name,
                "billing_model": plan.billing_model,
                "included_device_count": plan.included_device_count,
                "current_price": (
                    {"base_price": str(current_price.base_price), "currency": current_price.currency}
                    if current_price
                    else None
                ),
            }
        )
    return result
