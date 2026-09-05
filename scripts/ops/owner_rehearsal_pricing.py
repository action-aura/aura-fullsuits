"""Put the business owner's 2026-09-05 price list into the rehearsal Owner.

Prices and availability are DATA in this product (set from the Owner UI,
never hard-coded -- see owner/app/catalog/services.py's own docstrings), so
this drives the same audited services the UI uses, against the rehearsal
database only. Run from owner/ with OWNER_DATABASE_URL pointing at
aura_owner_rehearsal.

  - plan REHEARSAL-RETAIL: 2 included devices (manager's phone + one till),
    price 250 JOD from today (old 500 JOD row closed, never overwritten)
  - add-on EXTRA_DEVICE: 50 JOD, AVAILABLE (device slots are enforced and
    add_devices() is a real, audited operation, so it may be sold)
  - add-on EXTRA_BRANCH: 250 JOD, stays PLANNED (nothing enforces a branch
    limit yet -- must not read as sellable)
"""
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.catalog.services import add_plan_price, seed_canonical_catalog, set_addon_availability  # noqa: E402
from app.extensions import db_session  # noqa: E402
from app.models.catalog import Addon, Plan, PlanPrice  # noqa: E402
from app.models.staff import StaffUser  # noqa: E402

app = create_app()
with app.app_context():
    actor = db_session.execute(select(StaffUser).where(StaffUser.is_super_admin.is_(True))).scalars().first()
    print("actor:", actor.email if actor else None)

    created = seed_canonical_catalog()
    print("seed (idempotent) created:", created)

    plan = db_session.execute(select(Plan).where(Plan.plan_code == "REHEARSAL-RETAIL")).scalars().first()
    print("plan before:", plan.plan_code, "included", plan.included_device_count, "max", plan.max_device_count)
    if plan.included_device_count != 2:
        plan.included_device_count = 2
        db_session.commit()
    current = db_session.execute(
        select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.effective_until.is_(None))
    ).scalars().first()
    if current is None or Decimal(current.base_price) != Decimal("250.00") or current.currency != "JOD":
        add_plan_price(plan, Decimal("250.00"), "JOD", date.today(), actor.id)
    prices = db_session.execute(select(PlanPrice).where(PlanPrice.plan_id == plan.id).order_by(PlanPrice.effective_from)).scalars().all()
    print("plan after: included", plan.included_device_count, "prices",
          [(str(p.base_price), p.currency, str(p.effective_from), str(p.effective_until)) for p in prices])

    for code, price, status in (("EXTRA_DEVICE", Decimal("50.00"), "AVAILABLE"), ("EXTRA_BRANCH", Decimal("250.00"), "PLANNED")):
        addon = db_session.execute(select(Addon).where(Addon.addon_code == code)).scalars().first()
        addon.price = price
        addon.currency = "JOD"
        db_session.commit()
        if addon.availability_status != status:
            set_addon_availability(addon, status, actor.id)
        print("addon:", addon.addon_code, addon.availability_status, str(addon.price), addon.currency)
