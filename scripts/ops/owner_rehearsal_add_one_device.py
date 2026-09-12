"""Raise the rehearsal licence's device allowance by one, through the real
audited operation, so a fourth (fresh, admin-less) till can join for the
join-existing-shop proof."""
import os
import sys

sys.path.insert(0, os.getcwd())

from sqlalchemy import select  # noqa: E402

from app import create_app  # noqa: E402
from app.commercial_ops.device_slot_ops import add_devices  # noqa: E402
from app.extensions import db_session  # noqa: E402
from app.models.licensing import License  # noqa: E402
from app.models.staff import StaffUser  # noqa: E402

LICENSE_ID = "69e67d3f-73e8-4a51-b7b1-8185afa76e36"

app = create_app()
with app.app_context():
    actor = db_session.execute(select(StaffUser).where(StaffUser.is_super_admin.is_(True))).scalars().first()
    lic = db_session.get(License, LICENSE_ID)
    before = lic.device_limit
    add_devices(lic, additional_devices=1, reason="join-existing-shop proof, fourth till 2026-09-06",
                actor_staff_user_id=actor.id, idempotency_key="rehearsal-phone-20260906-join-add1")
    db_session.commit()
    lic = db_session.get(License, LICENSE_ID)
    print(f"device_limit {before} -> {lic.device_limit}")
