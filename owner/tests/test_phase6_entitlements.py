"""Part O ENTITLEMENTS: precedence, typing, deny-by-default, unavailable add-ons."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.conftest import make_license, make_staff


def test_deny_by_default_when_nothing_resolves(app, seeded):
    actor_id = make_staff(app, "ent1@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        resolved = resolve_entitlements(lic, datetime.now(timezone.utc))
        assert resolved["backup_enabled"] is False
        assert resolved["max_devices"] == 0


def test_plan_entitlement_applied(app, seeded):
    actor_id = make_staff(app, "ent2@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.catalog import EntitlementDefinition, PlanEntitlement
        from app.models.licensing import License
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        defn = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "backup_enabled")).scalars().first()
        db_session.add(PlanEntitlement(plan_id=lic.plan_id, entitlement_definition_id=defn.id, value=True))
        db_session.commit()

        resolved = resolve_entitlements(lic, datetime.now(timezone.utc))
        assert resolved["backup_enabled"] is True


def test_license_override_beats_plan(app, seeded):
    actor_id = make_staff(app, "ent3@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.catalog import EntitlementDefinition, PlanEntitlement
        from app.models.licensing import License, LicenseEntitlement
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        defn = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "max_devices")).scalars().first()
        db_session.add(PlanEntitlement(plan_id=lic.plan_id, entitlement_definition_id=defn.id, value=3))
        db_session.add(LicenseEntitlement(license_id=lic.id, entitlement_definition_id=defn.id, value=10))
        db_session.commit()

        resolved = resolve_entitlements(lic, datetime.now(timezone.utc), include_source=True)
        assert resolved["max_devices"]["value"] == 10
        assert resolved["max_devices"]["_source"] == "license_override"


def test_unavailable_addon_entitlement_never_applied(app, seeded):
    actor_id = make_staff(app, "ent4@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.catalog import Addon, AddonEntitlement, EntitlementDefinition
        from app.models.licensing import License
        from app.models.subscriptions import SubscriptionAddon
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        addon = db_session.execute(select(Addon).where(Addon.availability_status != "AVAILABLE")).scalars().first()
        assert addon is not None  # Phase 5 seed guarantees this (nothing seeded AVAILABLE)
        defn = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "backup_enabled")).scalars().first()
        db_session.add(AddonEntitlement(addon_id=addon.id, entitlement_definition_id=defn.id, value=True))
        db_session.add(SubscriptionAddon(subscription_id=lic.subscription_id, addon_id=addon.id))
        db_session.commit()

        resolved = resolve_entitlements(lic, datetime.now(timezone.utc))
        assert resolved["backup_enabled"] is False  # DRAFT/PLANNED add-on entitlement never applied


def test_negative_device_limit_rejected(app, seeded):
    actor_id = make_staff(app, "ent5@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import EntitlementResolutionError, resolve_entitlements
        from app.models.catalog import EntitlementDefinition, PlanEntitlement
        from app.models.licensing import License
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        defn = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "max_devices")).scalars().first()
        db_session.add(PlanEntitlement(plan_id=lic.plan_id, entitlement_definition_id=defn.id, value=-5))
        db_session.commit()

        with pytest.raises(EntitlementResolutionError):
            resolve_entitlements(lic, datetime.now(timezone.utc))


def test_wrong_type_rejected(app, seeded):
    actor_id = make_staff(app, "ent6@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import EntitlementResolutionError, resolve_entitlements
        from app.models.catalog import EntitlementDefinition, PlanEntitlement
        from app.models.licensing import License
        from sqlalchemy import select

        lic = db_session.get(License, license_id)
        defn = db_session.execute(select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "backup_enabled")).scalars().first()
        db_session.add(PlanEntitlement(plan_id=lic.plan_id, entitlement_definition_id=defn.id, value="not-a-boolean"))
        db_session.commit()

        with pytest.raises(EntitlementResolutionError):
            resolve_entitlements(lic, datetime.now(timezone.utc))


def test_resolution_is_deterministic(app, seeded):
    actor_id = make_staff(app, "ent7@example.com")
    license_id, _ = make_license(app, actor_id)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.entitlements import resolve_entitlements
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        now = datetime.now(timezone.utc)
        first = resolve_entitlements(lic, now)
        second = resolve_entitlements(lic, now)
        assert first == second
