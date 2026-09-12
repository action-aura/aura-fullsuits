"""'The customer bought a branch': attach EXTRA_BRANCH to the rehearsal
licence's subscription and make the add-on AVAILABLE (resolve_entitlements
applies only AVAILABLE add-ons -- a PLANNED one is inert by design). Prints
the resolved entitlements afterwards: the next check-in signs them into the
till's assertion, and create_branch admits a second branch.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.catalog.services import set_addon_availability  # noqa: E402
from app.extensions import db_session  # noqa: E402
from app.licensing_service.entitlements import resolve_entitlements  # noqa: E402
from app.models.catalog import Addon  # noqa: E402
from app.models.licensing import License  # noqa: E402
from app.models.staff import StaffUser  # noqa: E402
from app.models.subscriptions import SubscriptionAddon  # noqa: E402

LICENSE_ID = "69e67d3f-73e8-4a51-b7b1-8185afa76e36"

app = create_app()
with app.app_context():
    actor = db_session.execute(select(StaffUser).where(StaffUser.is_super_admin.is_(True))).scalars().first()
    lic = db_session.get(License, LICENSE_ID)
    addon = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_BRANCH")).scalars().first()
    if addon.availability_status != "AVAILABLE":
        set_addon_availability(addon, "AVAILABLE", actor.id)
    print("EXTRA_BRANCH:", addon.availability_status, str(addon.price), addon.currency)
    existing = db_session.execute(
        select(SubscriptionAddon).where(SubscriptionAddon.subscription_id == lic.subscription_id,
                                        SubscriptionAddon.addon_id == addon.id)
    ).scalars().first()
    if existing is None:
        db_session.add(SubscriptionAddon(subscription_id=lic.subscription_id, addon_id=addon.id))
        db_session.commit()
        print("attached EXTRA_BRANCH to subscription", lic.subscription_id)
    else:
        print("EXTRA_BRANCH already attached to subscription", lic.subscription_id)
    resolved = resolve_entitlements(lic, datetime.now(timezone.utc), include_source=True)
    print("resolved max_branches:", resolved.get("max_branches"))
