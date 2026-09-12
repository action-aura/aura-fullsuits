"""Put the branch allowance into the rehearsal Owner as DATA, the way the
Owner UI would: the `max_branches` entitlement definition (seeded by the
canonical catalogue once the seed carries it; created here if the running
code predates that), the plan's value (1 branch included), and the
EXTRA_BRANCH add-on's value (2 branches in total). A per-licence override
(`LicenseEntitlement`) is the ops path for more.

Prints the resolved entitlements for the rehearsal licence afterwards --
the exact dict the next check-in signs into the till's assertion.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db_session  # noqa: E402
from app.licensing_service.entitlements import resolve_entitlements  # noqa: E402
from app.models.catalog import Addon, AddonEntitlement, EntitlementDefinition, Plan, PlanEntitlement  # noqa: E402
from app.models.licensing import License  # noqa: E402

LICENSE_ID = "69e67d3f-73e8-4a51-b7b1-8185afa76e36"
PLAN_CODE = "REHEARSAL-RETAIL"
INCLUDED_BRANCHES = 1
WITH_EXTRA_BRANCH = 2

app = create_app()
with app.app_context():
    definition = db_session.execute(
        select(EntitlementDefinition).where(EntitlementDefinition.entitlement_code == "max_branches")
    ).scalars().first()
    if definition is None:
        definition = EntitlementDefinition(
            entitlement_code="max_branches", value_type="integer",
            description="Maximum branches the licence may run (0 = no limit; the till treats absent/0 as unlimited)",
        )
        db_session.add(definition)
        db_session.flush()
        print("created entitlement definition max_branches")
    else:
        print("entitlement definition max_branches exists")

    plan = db_session.execute(select(Plan).where(Plan.plan_code == PLAN_CODE)).scalars().first()
    row = db_session.execute(
        select(PlanEntitlement).where(PlanEntitlement.plan_id == plan.id,
                                      PlanEntitlement.entitlement_definition_id == definition.id)
    ).scalars().first()
    if row is None:
        db_session.add(PlanEntitlement(plan_id=plan.id, entitlement_definition_id=definition.id, value=INCLUDED_BRANCHES))
    else:
        row.value = INCLUDED_BRANCHES
    print(f"plan {PLAN_CODE}: max_branches = {INCLUDED_BRANCHES}")

    addon = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_BRANCH")).scalars().first()
    row = db_session.execute(
        select(AddonEntitlement).where(AddonEntitlement.addon_id == addon.id,
                                       AddonEntitlement.entitlement_definition_id == definition.id)
    ).scalars().first()
    if row is None:
        db_session.add(AddonEntitlement(addon_id=addon.id, entitlement_definition_id=definition.id, value=WITH_EXTRA_BRANCH))
    else:
        row.value = WITH_EXTRA_BRANCH
    print(f"add-on EXTRA_BRANCH: max_branches = {WITH_EXTRA_BRANCH} (status {addon.availability_status})")
    db_session.commit()

    lic = db_session.get(License, LICENSE_ID)
    resolved = resolve_entitlements(lic, datetime.now(timezone.utc), include_source=True)
    print("resolved for the rehearsal licence:", {k: v for k, v in resolved.items() if k in ("max_devices", "max_branches")})
