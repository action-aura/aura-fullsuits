"""Deterministic entitlement-resolution engine (Part O).

resolve_entitlements() is a pure function of its inputs (aside from the DB
reads that fetch those inputs) -- same license + same timestamp always
produces the same result. The output is what gets embedded, verbatim, into a
signed assertion snapshot; it is never recomputed for an already-issued
assertion.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import Addon, AddonEntitlement, EntitlementDefinition, PlanEntitlement
from app.models.licensing import LicenseEntitlement
from app.models.subscriptions import SubscriptionAddon

_TYPE_DEFAULTS = {"boolean": False, "integer": 0, "string": "", "list": [], "date": None}
_SAFETY_MAX_DEVICE_LIMIT = 1000


class EntitlementResolutionError(ValueError):
    pass


def _validate_typed_value(definition: EntitlementDefinition, value) -> None:
    value_type = definition.value_type
    if value_type == "boolean" and not isinstance(value, bool):
        raise EntitlementResolutionError(f"{definition.entitlement_code}: expected boolean, got {type(value).__name__}")
    if value_type == "integer" and not isinstance(value, int):
        raise EntitlementResolutionError(f"{definition.entitlement_code}: expected integer, got {type(value).__name__}")
    if value_type == "list" and not isinstance(value, list):
        raise EntitlementResolutionError(f"{definition.entitlement_code}: expected list, got {type(value).__name__}")
    if value_type == "string" and not isinstance(value, str):
        raise EntitlementResolutionError(f"{definition.entitlement_code}: expected string, got {type(value).__name__}")
    if definition.entitlement_code == "max_devices" and isinstance(value, int):
        if value < 0:
            raise EntitlementResolutionError("max_devices may not be negative.")
        if value > _SAFETY_MAX_DEVICE_LIMIT:
            raise EntitlementResolutionError(f"max_devices exceeds system safety limit ({_SAFETY_MAX_DEVICE_LIMIT}).")


def resolve_entitlements(license_row, at_timestamp: datetime, *, include_source: bool = False) -> dict:
    """Precedence (low -> high, highest wins): plan entitlements, active
    (AVAILABLE-only) add-on entitlements, license-specific overrides. System
    safety limits and product/platform constraints are enforced as validation
    on top, not as a resolvable entitlement source. Deny-by-default: any
    entitlement code with no resolved source gets its type's safe default."""
    definitions = {d.entitlement_code: d for d in db_session.execute(select(EntitlementDefinition)).scalars().all()}
    resolved: dict[str, dict] = {}

    for code, definition in definitions.items():
        resolved[code] = {"value": _TYPE_DEFAULTS[definition.value_type], "_source": "deny_by_default"}

    # Convention (Phase 6, established here since no Phase 5 code ever wrote
    # to these JSONB columns): value holds the raw typed JSON value directly
    # (true / 5 / "text" / ["a","b"] / "2027-01-01"), never wrapped.
    plan_rows = db_session.execute(select(PlanEntitlement).where(PlanEntitlement.plan_id == license_row.plan_id)).scalars().all()
    for row in plan_rows:
        code = row.entitlement_definition.entitlement_code
        _validate_typed_value(row.entitlement_definition, row.value)
        resolved[code] = {"value": row.value, "_source": "plan"}

    addon_rows = db_session.execute(
        select(SubscriptionAddon)
        .where(SubscriptionAddon.subscription_id == license_row.subscription_id)
    ).scalars().all()
    for sub_addon in addon_rows:
        addon = db_session.get(Addon, sub_addon.addon_id)
        if addon is None or addon.availability_status != "AVAILABLE":
            continue  # Part O: an unavailable/DRAFT/PLANNED add-on's entitlement is never applied
        addon_ent_rows = db_session.execute(select(AddonEntitlement).where(AddonEntitlement.addon_id == addon.id)).scalars().all()
        for row in addon_ent_rows:
            code = row.entitlement_definition.entitlement_code
            _validate_typed_value(row.entitlement_definition, row.value)
            resolved[code] = {"value": row.value, "_source": "addon"}

    override_rows = db_session.execute(select(LicenseEntitlement).where(LicenseEntitlement.license_id == license_row.id)).scalars().all()
    for row in override_rows:
        code = row.entitlement_definition.entitlement_code
        _validate_typed_value(row.entitlement_definition, row.value)
        resolved[code] = {"value": row.value, "_source": "license_override"}

    # Product/platform constraint enforcement (narrower of license vs. request always wins) --
    # applied by the caller (activation.py/checkin.py), which knows the requesting platform;
    # this function only guarantees the entitlement *values* are safe and typed.

    if include_source:
        return resolved
    return {code: entry["value"] for code, entry in resolved.items()}
