"""Seller agreement clause 8 -- whose customer is it, and when does a seller's right lapse.

THE CLAUSE. A customer belongs to the seller who registered it first in Aura Owner (the system's
records decide any dispute). A seller may not approach a customer registered to another seller to
close the deal for themselves, unless the two agree in writing. The seller's right in a customer
LAPSES if 15 days pass from registering it with neither a subscription nor documented activity.

WHAT "OWNER" IS HERE. Customer.assigned_sales_staff_id (the same pointer every CRM screen scopes by).
Nothing about it changes except that it can now become NULL ("open") when the right lapses.

THE CLOCK (derived on every read, never stored, so it cannot drift from the facts):
    started    = the latest of  the customer's registration,  the start of the CURRENT assignment (a manager
                 handing a customer to a seller, or a seller claiming an open one, starts a fresh 15 days),
                 and the day this rule was switched on (owner_system_settings; the migration stamps it, so
                 customers registered long before the rule do not all lapse on its first night)
    activity   = the latest note or logged interaction the OWNER recorded on the customer
    expires_at = max(started, activity) + 15 days
    never      = once the customer has ANY subscription (a trial -- PILOT -- counts, a paid one counts), the
                 right is permanent; and a customer whose owner is not a seller (a manager's own, a lead a
                 manager converted) is not on a clock at all.

WHEN IT LAPSES (scan_lapsed, run by `flask sellers scan`, or on the spot when somebody tries to claim): the owner
is cleared, the current assignment row is closed, one owner_customer_ownership_releases row records WHO lost it and
WHY, it is audited, and the seller is emailed. The customer is now OPEN: any seller can claim it. A claim on a
customer somebody owns is refused (CUSTOMER_NOT_OPEN) and changes nothing; a manager can always reassign (the
existing customers.assign action, audited, which also closes the open record).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.audit import SystemSetting
from app.models.base import utcnow
from app.models.customers import Customer, CustomerNote
from app.models.employees import EmployeeProfile
from app.models.leads import CustomerAssignment, CustomerInteraction
from app.models.sellers import CustomerOwnershipRelease
from app.models.staff import StaffUser
from app.models.subscriptions import Subscription
from app.sellers import notify, people
from app.sellers.errors import SellerRulesError

LAPSE_DAYS = 15
EXPIRING_SOON_DAYS = 3
RULE_STARTED_KEY = "seller_customer_lapse_started_at"

RELEASE_LAPSED = "LAPSED_15_DAYS"
RELEASE_AGREEMENT_ENDED = "AGREEMENT_ENDED"


# ------------------------------------------------------------------ the clock

def rule_started_at() -> datetime | None:
    row = db_session.execute(select(SystemSetting).where(SystemSetting.key == RULE_STARTED_KEY)).scalars().first()
    if row is None or not isinstance(row.value, dict) or not row.value.get("started_at"):
        return None
    return datetime.fromisoformat(row.value["started_at"])


def has_subscription(customer_id) -> bool:
    return db_session.execute(
        select(Subscription.id).where(Subscription.customer_id == customer_id, Subscription.status != "DRAFT").limit(1)
    ).first() is not None


def _current_assignment_started(customer_id) -> datetime | None:
    return db_session.execute(
        select(func.max(CustomerAssignment.assigned_at)).where(
            CustomerAssignment.customer_id == customer_id, CustomerAssignment.unassigned_at.is_(None))
    ).scalar_one_or_none()


def last_activity_at(customer: Customer) -> datetime | None:
    """The latest documented activity by the CURRENT owner: a note, or a logged call / visit. What others
    (a manager's note, the system) recorded is not the seller's activity."""
    owner_id = customer.assigned_sales_staff_id
    if owner_id is None:
        return None
    note = db_session.execute(
        select(func.max(CustomerNote.created_at)).where(
            CustomerNote.customer_id == customer.id, CustomerNote.author_staff_user_id == owner_id,
            CustomerNote.archived_at.is_(None))
    ).scalar_one_or_none()
    interaction = db_session.execute(
        select(func.max(CustomerInteraction.created_at))
        .join(EmployeeProfile, EmployeeProfile.id == CustomerInteraction.employee_profile_id)
        .where(CustomerInteraction.customer_id == customer.id, EmployeeProfile.staff_user_id == owner_id)
    ).scalar_one_or_none()
    return max((t for t in (note, interaction) if t is not None), default=None)


def clock_started_at(customer: Customer) -> datetime:
    candidates = [customer.created_at, _current_assignment_started(customer.id), rule_started_at()]
    return max(t for t in candidates if t is not None)


def owner_as_seller(customer: Customer) -> StaffUser | None:
    """The owning seller, or None when the customer has no owner or the owner is not a seller."""
    if customer.assigned_sales_staff_id is None:
        return None
    staff = db_session.get(StaffUser, customer.assigned_sales_staff_id)
    return staff if people.is_seller(staff) else None


def expires_at(customer: Customer) -> datetime | None:
    """When the owner's right lapses, or None when it cannot (no seller owner, or a subscription exists)."""
    if customer.archived_at is not None or owner_as_seller(customer) is None or has_subscription(customer.id):
        return None
    basis = max(t for t in (clock_started_at(customer), last_activity_at(customer)) if t is not None)
    # In UTC: the database hands times back in its session zone, and adding days to a zone-aware time moves the WALL clock,
    # so across a daylight-saving change "15 days" would silently become 15 days and an hour (found by the tests).
    return basis.astimezone(timezone.utc) + timedelta(days=LAPSE_DAYS)


def describe(customer: Customer, *, now: datetime | None = None) -> dict:
    """What the customer page and the dashboard say: state OPEN / FIXED / RUNNING / NONE, plus the date."""
    now = now or utcnow()
    if customer.assigned_sales_staff_id is None:
        return {"state": "OPEN" if open_release_for(customer.id) is not None else "NONE", "expires_at": None, "days_left": None}
    if owner_as_seller(customer) is None:
        return {"state": "NONE", "expires_at": None, "days_left": None}
    if has_subscription(customer.id):
        return {"state": "FIXED", "expires_at": None, "days_left": None}
    when = expires_at(customer)
    return {"state": "RUNNING", "expires_at": when, "days_left": max(0, (when - now).days) if when else None}


# ------------------------------------------------------------------ releasing

def open_release_for(customer_id) -> CustomerOwnershipRelease | None:
    return db_session.execute(
        select(CustomerOwnershipRelease).where(
            CustomerOwnershipRelease.customer_id == customer_id, CustomerOwnershipRelease.closed_at.is_(None))
    ).scalars().first()


def release_customer(customer: Customer, *, reason: str, now: datetime | None = None, actor_staff_user_id=None,
                     notify_seller: bool = True) -> CustomerOwnershipRelease:
    """Clears the owner and records why. Commits. Idempotent per customer: a customer with no owner is
    returned as it is (its open record, if any)."""
    now = now or utcnow()
    previous = customer.assigned_sales_staff_id
    if previous is None:
        existing = open_release_for(customer.id)
        if existing is not None:
            return existing
    # one open record per customer (the partial unique index): anything stale is closed first
    stale = open_release_for(customer.id)
    if stale is not None:
        stale.closed_at, stale.closed_how = now, "SUPERSEDED"
        db_session.flush()
    for assignment in db_session.execute(select(CustomerAssignment).where(
            CustomerAssignment.customer_id == customer.id, CustomerAssignment.unassigned_at.is_(None))).scalars():
        assignment.unassigned_at = now
    customer.assigned_sales_staff_id = None
    customer.version += 1
    row = CustomerOwnershipRelease(customer_id=customer.id, previous_owner_staff_user_id=previous, reason=reason, released_at=now)
    db_session.add(row)
    seller = db_session.get(StaffUser, previous) if previous is not None else None
    if notify_seller and seller is not None:
        notify.queue("seller_customer_released", seller, [customer.legal_name, reason_text(reason)])
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_OWNERSHIP_RELEASED",
        entity_type="customer", entity_public_id=str(customer.id),
        before_state={"assigned_to_staff_user_id": str(previous) if previous else None},
        after_state={"reason": reason, "release_id": str(row.id)},
    )
    return row


def reason_text(reason: str) -> str:
    return {
        RELEASE_LAPSED: "15 days passed with no subscription and no documented activity",
        RELEASE_AGREEMENT_ENDED: "your agreement with the company ended",
    }.get(reason, reason)


def scan_lapsed(*, now: datetime | None = None, apply: bool = True) -> dict:
    """Releases every customer whose owner's 15 days are up. Idempotent: a released customer has no owner."""
    now = now or utcnow()
    floor = now - timedelta(days=LAPSE_DAYS)
    candidates = db_session.execute(
        select(Customer).where(
            Customer.assigned_sales_staff_id.is_not(None), Customer.archived_at.is_(None), Customer.created_at < floor,
            ~select(Subscription.id).where(Subscription.customer_id == Customer.id, Subscription.status != "DRAFT").exists(),
        ).order_by(Customer.created_at, Customer.id)
    ).scalars().all()
    due = [c for c in candidates if (e := expires_at(c)) is not None and e < now]
    result = {"checked": len(candidates), "lapsed": len(due), "released": 0}
    if not apply:
        return result
    for customer in due:
        release_customer(customer, reason=RELEASE_LAPSED, now=now)
        result["released"] += 1
    return result


# ------------------------------------------------------------------ claiming

def open_customers() -> list[tuple[Customer, CustomerOwnershipRelease]]:
    rows = db_session.execute(
        select(Customer, CustomerOwnershipRelease)
        .join(CustomerOwnershipRelease, CustomerOwnershipRelease.customer_id == Customer.id)
        .where(CustomerOwnershipRelease.closed_at.is_(None), Customer.assigned_sales_staff_id.is_(None),
               Customer.archived_at.is_(None))
        .order_by(CustomerOwnershipRelease.released_at, Customer.id)
    ).all()
    return [(c, r) for c, r in rows]


def claim(customer_id, *, seller: StaffUser, now: datetime | None = None) -> Customer:
    """A seller takes an OPEN customer. Only open ones: a customer somebody still owns is refused and
    left exactly as it was. A customer whose owner's 15 days are up but whom the nightly scan has not
    released yet counts as open (it is released here, on the spot, then claimed)."""
    now = now or utcnow()
    if not people.is_seller(seller):
        raise SellerRulesError("CLAIM_SELLERS_ONLY")
    customer = db_session.execute(
        select(Customer).where(Customer.id == customer_id).with_for_update().execution_options(populate_existing=True)
    ).scalars().first()
    if customer is None or customer.archived_at is not None:
        db_session.rollback()
        raise SellerRulesError("CUSTOMER_NOT_FOUND")
    if customer.assigned_sales_staff_id is not None:
        lapsed = expires_at(customer)
        if lapsed is None or lapsed >= now:
            db_session.rollback()
            raise SellerRulesError("CUSTOMER_NOT_OPEN")
        release_customer(customer, reason=RELEASE_LAPSED, now=now)
        customer = db_session.execute(
            select(Customer).where(Customer.id == customer_id).with_for_update().execution_options(populate_existing=True)
        ).scalars().first()
    release = open_release_for(customer.id)
    if release is None:
        db_session.rollback()
        raise SellerRulesError("CUSTOMER_NOT_OPEN")  # unowned, but never released by a rule: a manager's to hand out
    customer.assigned_sales_staff_id = seller.id
    customer.version += 1
    db_session.add(CustomerAssignment(
        customer_id=customer.id, assigned_to_staff_user_id=seller.id, assigned_by_staff_user_id=seller.id,
        assigned_at=now, reason="Claimed an open customer"))
    release.closed_at, release.closed_how, release.claimed_by_staff_user_id = now, "CLAIMED", seller.id
    db_session.commit()
    audit_record(
        actor_staff_user_id=seller.id, actor_role_snapshot=None, action_code="CUSTOMER_CLAIMED",
        entity_type="customer", entity_public_id=str(customer.id),
        after_state={"assigned_to_staff_user_id": str(seller.id), "release_id": str(release.id)},
    )
    return customer


def close_release_on_assignment(customer: Customer, *, actor_staff_user_id, now: datetime | None = None) -> None:
    """Called by the existing assign_customer (a manager handing the customer to someone): the customer
    is no longer open. Does not commit; the caller's commit carries it."""
    release = open_release_for(customer.id)
    if release is not None:
        release.closed_at, release.closed_how = now or utcnow(), "REASSIGNED"
        release.claimed_by_staff_user_id = customer.assigned_sales_staff_id


def expiring_soon_for(staff_user_id, *, now: datetime | None = None) -> list[tuple[Customer, datetime]]:
    """The seller's own customers whose right ends within EXPIRING_SOON_DAYS (soonest first)."""
    now = now or utcnow()
    limit = now + timedelta(days=EXPIRING_SOON_DAYS)
    mine = db_session.execute(
        select(Customer).where(Customer.assigned_sales_staff_id == staff_user_id, Customer.archived_at.is_(None),
                               Customer.created_at < limit)
    ).scalars().all()
    out = [(c, e) for c in mine if (e := expires_at(c)) is not None and e <= limit]
    return sorted(out, key=lambda pair: (pair[1], str(pair[0].id)))
