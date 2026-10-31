"""What a signed-in customer may be told about their own licences: ONE source for
the website's account summary and the apps' account sign-in, so the two can never
describe the same shop differently.

All reads. Every row is scoped by the customer id of the account that signed in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import Plan, Product
from app.models.commercial_ops import PilotRecord
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.licensing import License
from app.models.staff import StaffUser
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today

# A licence the customer can still be shown / still download an installer for:
# a trial, a paid shop, and a shop whose term lapsed (a locked shop must be able
# to reinstall). Suspended / cancelled / revoked ones are not listed.
LISTED_SUBSCRIPTION_STATUSES = ("PILOT", "ACTIVE", "PAST_DUE", "EXPIRED")
LISTED_LICENSE_STATUSES = ("ISSUED", "ACTIVE", "EXPIRED")


@dataclass(frozen=True)
class LicenceRow:
    license: License
    subscription: Subscription
    plan: Plan
    product: Product
    pilot: PilotRecord | None


def licence_rows(customer_id, *, product_code: str | None = None, platform_code: str | None = None) -> list[LicenceRow]:
    """The customer's listable licences, oldest first. `platform_code` keeps only
    licences allowed on that platform."""
    stmt = (
        select(License, Subscription, Plan, Product)
        .join(Subscription, Subscription.id == License.subscription_id)
        .join(Plan, Plan.id == License.plan_id)
        .join(Product, Product.id == License.product_id)
        .where(
            License.customer_id == customer_id,
            License.status.in_(LISTED_LICENSE_STATUSES),
            Subscription.status.in_(LISTED_SUBSCRIPTION_STATUSES),
        )
        .order_by(License.created_at)
    )
    if product_code:
        stmt = stmt.where(Product.product_code == product_code)
    rows = []
    for license_row, subscription, plan, product in db_session.execute(stmt).all():
        if platform_code and platform_code not in (license_row.allowed_platforms or "").split(","):
            continue
        pilot = db_session.execute(
            select(PilotRecord).where(PilotRecord.subscription_id == subscription.id, PilotRecord.pilot_kind == "TRIAL")
        ).scalars().first()
        rows.append(LicenceRow(license_row, subscription, plan, product, pilot))
    return rows


def trial_view(pilot: PilotRecord | None, today: date | None = None) -> dict:
    """{state, starts_on, ends_on, days_left}. States: NONE (not a trial),
    NOT_STARTED (waiting for the first device), ACTIVE, ENDED, CONVERTED (paid).
    days_left counts calendar days in Amman (Jordan) time: 0 = today is the last
    day the shop can sell; the trial runs through the END of ends_on."""
    empty = {"state": "NONE", "starts_on": None, "ends_on": None, "days_left": None}
    if pilot is None:
        return empty
    today = today or jordan_today()
    if pilot.status == "CONVERTED":
        return {"state": "CONVERTED", "starts_on": pilot.pilot_start.isoformat() if pilot.trial_started_at else None,
                "ends_on": None, "days_left": None}
    if pilot.trial_started_at is None:
        return {"state": "NOT_STARTED", "starts_on": None, "ends_on": None, "days_left": None}
    starts, ends = pilot.pilot_start, pilot.pilot_end
    if pilot.status in ("COMPLETED", "CANCELLED") or today > ends:
        return {"state": "ENDED", "starts_on": starts.isoformat(), "ends_on": ends.isoformat(), "days_left": 0}
    return {"state": "ACTIVE", "starts_on": starts.isoformat(), "ends_on": ends.isoformat(),
            "days_left": (ends - today).days}


def seller_of(customer_id) -> dict | None:
    """{name, phone} of the customer's assigned seller: who the customer pays and
    calls. None when nobody is assigned."""
    customer = db_session.get(Customer, customer_id)
    if customer is None or customer.assigned_sales_staff_id is None:
        return None
    staff = db_session.get(StaffUser, customer.assigned_sales_staff_id)
    if staff is None:
        return None
    profile = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff.id)
    ).scalars().first()
    name = (profile.full_name if profile and profile.full_name else staff.display_name) or None
    return {"name": name, "phone": (profile.phone if profile else None) or None}
