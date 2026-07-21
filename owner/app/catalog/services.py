"""Commercial catalog services (Part I/J)."""
from __future__ import annotations

import uuid
from datetime import date

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
            imported_at=utcnow(),
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
