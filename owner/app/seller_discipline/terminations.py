"""Seller agreement clauses 17 and 18 -- ending the agreement, and what that does.

CLAUSE 17. Either side may end the agreement with 7 days' notice, through Aura Owner. The seller
presses "End my agreement" on their profile; a manager (sellers.discipline) ends a seller's
agreement from the discipline page. The company may also end it AT ONCE for the three named
violations (clause 16; sanctions.py asks for notice_days=0). Until the effective moment the seller
keeps working as usual.

CLAUSE 18. When the notice runs out (a periodic scan, or the seller's next sign-in, whichever comes
first -- scan.py):
  * the account stops (employees.services.terminate_employee: profile TERMINATED, sign-in refused,
    every session and phone notification killed);
  * a customer whose TRIAL had already started keeps crediting the seller for its FIRST payment
    (a SellerTrialCredit row, see direct_sale._resolve_seller). Their ownership is kept until the
    trial is over, then it is released like everyone else's;
  * every other customer the seller registered returns to the company: the customer's sales owner
    (assigned_sales_staff_id) is cleared -- an unowned customer is "open" -- or, when the manager who
    ended the agreement named another seller, handed to that seller;
  * commissions already earned are paid per clause 5: NOTHING in the commission ledger is touched;
  * cash the seller still owes stays visible to managers (no cash row is touched here).
The whole effect is ONE transaction with the status flip, under a row lock, so two scans cannot
both run it, and a crash leaves the notice still due for the next pass.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.customer_onboarding.trial import trial_pilot_for_customer, trial_pilot_for_subscription
from app.employees.services import terminate_employee
from app.extensions import db_session
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.leads import CustomerAssignment
from app.models.seller_discipline import SellerTermination, SellerTrialCredit
from app.models.staff import StaffUser
from app.models.subscriptions import Subscription
from app.seller_discipline import mail
from app.seller_discipline.errors import DisciplineError
from app.seller_discipline.people import load_seller, profile_of

NOTICE_DAYS = 7
MAX_TEXT = 4000
LIVE_TRIAL_STATUSES = ("ACTIVE", "EXTENDED")


# ------------------------------------------------------------------ the notice

def open_notice_for(seller_staff_user_id) -> SellerTermination | None:
    return db_session.execute(
        select(SellerTermination).where(
            SellerTermination.seller_staff_user_id == seller_staff_user_id, SellerTermination.status == "NOTICE")
    ).scalars().first()


def give_notice(
    seller_staff_user_id, *, actor: StaffUser, by_seller: bool, reason: str = "", reassign_to_staff_user_id=None,
    notice_days: int = NOTICE_DAYS, sanction_case_id=None, commit: bool = True, supersede_running: bool = False,
    now: datetime | None = None,
) -> SellerTermination:
    """Starts the notice. Every check is made before the first write. commit=False leaves the
    commit to the caller (sanctions.apply_case writes the decision and this in one transaction)
    and the caller then calls announce()."""
    now = now or utcnow()
    reason = (reason or "").strip()
    if len(reason) > MAX_TEXT:
        raise DisciplineError("TEXT_TOO_LONG")
    seller, _profile = load_seller(seller_staff_user_id)
    if by_seller:
        if actor.id != seller.id:
            raise DisciplineError("ONLY_THE_SELLER_ENDS_THEIR_OWN")
        if reassign_to_staff_user_id:
            raise DisciplineError("ONLY_A_MANAGER_CHOOSES_WHO_GETS_CUSTOMERS")
    elif actor.id == seller.id:
        raise DisciplineError("SELF_ACTION_FORBIDDEN")
    destination = None
    if reassign_to_staff_user_id:
        destination, _dest_profile = load_seller(reassign_to_staff_user_id, statuses=("ACTIVE",))
        if destination.id == seller.id or not destination.is_active or destination.disabled_at is not None:
            raise DisciplineError("REASSIGN_TARGET_INVALID")
    running = open_notice_for(seller.id)
    if running is not None:
        if not supersede_running:
            raise DisciplineError("ALREADY_ENDING")
        # A sanction that ends the agreement at once overtakes a notice that is still running: that
        # notice now takes effect now (never later than it already would have).
        running.effective_at = min(running.effective_at, now + timedelta(days=notice_days))
        running.sanction_case_id = sanction_case_id
        db_session.flush()
        return running
    row = SellerTermination(
        id=uuid.uuid4(), seller_staff_user_id=seller.id, initiated_by_staff_user_id=actor.id, initiated_by_seller=by_seller,
        sanction_case_id=sanction_case_id, reason=reason or None, notice_given_at=now,
        effective_at=now + timedelta(days=notice_days), reassign_to_staff_user_id=destination.id if destination else None,
        status="NOTICE",
    )
    db_session.add(row)
    try:
        db_session.flush()
    except IntegrityError:
        db_session.rollback()  # two taps at once: the unique index let one notice through
        raise DisciplineError("ALREADY_ENDING")
    if commit:
        db_session.commit()
        announce(row, seller)
    return row


def announce(row: SellerTermination, seller: StaffUser | None = None) -> None:
    """Audit + courtesy email, after the commit. An immediate ending (notice 0) is announced by the
    sanction decision instead."""
    audit_record(
        actor_staff_user_id=row.initiated_by_staff_user_id, actor_role_snapshot=None, action_code="SELLER_AGREEMENT_ENDING",
        entity_type="seller_termination", entity_public_id=str(row.id), reason=row.reason,
        after_state={"seller_staff_user_id": str(row.seller_staff_user_id), "effective_at": row.effective_at.isoformat(),
                     "by_seller": row.initiated_by_seller, "sanction_case_id": str(row.sanction_case_id) if row.sanction_case_id else None,
                     "reassign_to_staff_user_id": str(row.reassign_to_staff_user_id) if row.reassign_to_staff_user_id else None},
    )
    if row.effective_at > row.notice_given_at:
        seller = seller or db_session.get(StaffUser, row.seller_staff_user_id)
        mail.queue_to_staff(seller, "seller_agreement_ending", [
            "Agreement ends: " + row.effective_at.strftime("%Y-%m-%d %H:%M UTC"),
            "Started by: " + ("you" if row.initiated_by_seller else "the company"),
        ])


# ------------------------------------------------------------- taking effect

def due_termination_ids(now: datetime, seller_staff_user_id=None) -> list:
    stmt = select(SellerTermination.id).where(SellerTermination.status == "NOTICE", SellerTermination.effective_at <= now)
    if seller_staff_user_id is not None:
        stmt = stmt.where(SellerTermination.seller_staff_user_id == seller_staff_user_id)
    return list(db_session.execute(stmt.order_by(SellerTermination.effective_at)).scalars().all())


def apply_termination(termination_id, *, now: datetime | None = None) -> bool:
    """Makes a due notice take effect. False when it is not due (or already applied): never an
    error, so a scan and a sign-in racing each other are both fine."""
    now = now or utcnow()
    row = db_session.execute(
        select(SellerTermination).where(SellerTermination.id == termination_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().first()
    if row is None or row.status != "NOTICE" or row.effective_at > now:
        db_session.rollback()
        return False
    seller = db_session.get(StaffUser, row.seller_staff_user_id)
    profile = profile_of(row.seller_staff_user_id)
    row.status, row.applied_at = "APPLIED", now
    kept, returned = _settle_customers(row, now)
    if profile is not None and profile.employment_status not in ("TERMINATED", "ARCHIVED"):
        # Commits the status flip, the customers and the account in ONE transaction.
        terminate_employee(profile, row.reason or "Seller agreement ended (clause 17)", row.initiated_by_staff_user_id)
    else:
        db_session.commit()
    audit_record(
        actor_staff_user_id=row.initiated_by_staff_user_id, actor_role_snapshot=None, action_code="SELLER_AGREEMENT_ENDED",
        entity_type="seller_termination", entity_public_id=str(row.id), reason=row.reason,
        after_state={"seller_staff_user_id": str(row.seller_staff_user_id), "trial_customers_kept": kept,
                     "customers_returned": returned, "sanction_case_id": str(row.sanction_case_id) if row.sanction_case_id else None},
    )
    return True


def _started_live_trial_subscription(customer_id, cutoff: datetime):
    """The customer's trial subscription when its trial STARTED (first device activation) by the
    cutoff and is still waiting to be paid; else None. A trial that has not started, or is already
    paid / finished, is not clause 18's case."""
    pilot = trial_pilot_for_customer(customer_id)
    if pilot is None or pilot.status not in LIVE_TRIAL_STATUSES:
        return None
    if pilot.trial_started_at is None or pilot.trial_started_at > cutoff:
        return None
    subscription = db_session.get(Subscription, pilot.subscription_id)
    return subscription.id if subscription is not None and subscription.status == "PILOT" else None


def _move_customer(customer: Customer, to_staff_user_id, by_staff_user_id, now: datetime, reason: str) -> None:
    """Closes the customer's open assignment and points the customer at `to_staff_user_id` (None =
    back to the company). Same append-only history as customers.services.assign_customer, without its
    commit, so it can sit inside a larger transaction."""
    current = db_session.execute(
        select(CustomerAssignment).where(CustomerAssignment.customer_id == customer.id, CustomerAssignment.unassigned_at.is_(None))
    ).scalars().all()
    for assignment in current:
        assignment.unassigned_at = now
    if to_staff_user_id is not None:
        db_session.add(CustomerAssignment(
            customer_id=customer.id, assigned_to_staff_user_id=to_staff_user_id, assigned_by_staff_user_id=by_staff_user_id,
            assigned_at=now, reason=reason))
    customer.assigned_sales_staff_id = to_staff_user_id
    customer.version += 1


def _destination_for(row: SellerTermination):
    """The seller the manager chose to receive the customers, if they can still take them."""
    if row.reassign_to_staff_user_id is None:
        return None
    destination = db_session.get(StaffUser, row.reassign_to_staff_user_id)
    if destination is None or not destination.is_active or destination.disabled_at is not None:
        return None
    return destination.id


def _settle_customers(row: SellerTermination, now: datetime) -> tuple[int, int]:
    customers = db_session.execute(
        select(Customer).where(Customer.assigned_sales_staff_id == row.seller_staff_user_id, Customer.archived_at.is_(None))
        .order_by(Customer.id).with_for_update().execution_options(populate_existing=True)
    ).scalars().all()
    destination = _destination_for(row)
    kept = returned = 0
    for customer in customers:
        subscription_id = _started_live_trial_subscription(customer.id, row.effective_at)
        if subscription_id is not None:
            db_session.add(SellerTrialCredit(
                termination_id=row.id, seller_staff_user_id=row.seller_staff_user_id, customer_id=customer.id,
                subscription_id=subscription_id))
            kept += 1
        else:
            _move_customer(customer, destination, row.initiated_by_staff_user_id, now, "Seller agreement ended (clause 18)")
            returned += 1
    db_session.flush()
    return kept, returned


# --------------------------------------------------------- the trial credits

def credited_staff_for_subscription(subscription_id) -> uuid.UUID | None:
    """The former seller a running trial still credits for its first payment, or None."""
    return db_session.execute(
        select(SellerTrialCredit.seller_staff_user_id).where(
            SellerTrialCredit.subscription_id == subscription_id, SellerTrialCredit.released_at.is_(None))
    ).scalars().first()


def release_finished_trial_credits(*, now: datetime | None = None) -> int:
    """A credited trial that is over (paid -> CONVERTED, or cancelled / completed unpaid) has done its
    job: the credit is released and the customer finally returns to the company (or goes to the
    seller the manager named). A trial still running keeps its credit."""
    now = now or utcnow()
    released = 0
    credits = db_session.execute(
        select(SellerTrialCredit).where(SellerTrialCredit.released_at.is_(None)).order_by(SellerTrialCredit.created_at)
    ).scalars().all()
    for credit in credits:
        pilot = trial_pilot_for_subscription(credit.subscription_id)
        subscription = db_session.get(Subscription, credit.subscription_id)
        still_running = (pilot is not None and pilot.status in LIVE_TRIAL_STATUSES
                         and subscription is not None and subscription.status == "PILOT")
        if still_running:
            continue
        termination = db_session.get(SellerTermination, credit.termination_id)
        credit.released_at = now
        credit.release_reason = "CONVERTED" if pilot is not None and pilot.status == "CONVERTED" else "TRIAL_ENDED"
        customer = db_session.get(Customer, credit.customer_id)
        if customer is not None and customer.assigned_sales_staff_id == credit.seller_staff_user_id:
            _move_customer(customer, _destination_for(termination), termination.initiated_by_staff_user_id, now,
                           "Trial finished after the seller's agreement ended (clause 18)")
        db_session.commit()
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None, action_code="SELLER_TRIAL_CREDIT_RELEASED",
            entity_type="seller_trial_credit", entity_public_id=str(credit.id),
            after_state={"customer_id": str(credit.customer_id), "release_reason": credit.release_reason})
        released += 1
    return released
