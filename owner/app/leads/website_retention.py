"""24-month purge of unconverted WEBSITE leads.

Why this exists: the actionaura.me privacy page promises "We keep demo
requests for 24 months, then delete them. This applies to the copies in our
mailbox and in our internal customer system too." The website intake
(app/leads/website_intake.py) copies every demo request into owner_leads
(created by the "Website intake" system actor) with a LeadNote holding the contact and message text.
Nothing deleted those copies, so the promise was not true for the internal
customer system. This module makes it true; it is run nightly by
deploy/oracle-free/run_scheduled_ops.sh via `flask leads purge-website --apply`.

What counts as "a demo request that was never acted on" (and so is purged):

  * It is an intake lead BY ORIGIN: Lead.created_by_employee_profile_id is the
    "Website intake" actor's profile. Never by Lead.source: that is a
    staff-editable label ("WEBSITE" is in the lead form's picker), so selecting
    on it would delete a lead a salesperson typed in by hand, and a staff member
    re-tagging an intake lead to "REFERRAL" would exempt it from the 24-month
    promise. If the actor was never seeded there are no intake leads, and the
    purge selects nothing.
  * It has been DORMANT for the whole window: created_at, updated_at and
    last_interaction_at are all older than the cutoff, and no follow-up is due
    on or after the cutoff. The 24-month promise counts from the last activity
    on the request, so a lead staff are still working is never deleted under
    them. (LeadFollowup.due_at is the follow-up's time column.)
  * converted_at IS NULL, and no Customer.converted_from_lead_id and no
    Quote.lead_id points at it. Those two are the only foreign keys onto
    owner_leads from outside app/models/leads.py. A lead that became a customer
    or a quote is business history, not "a demo request" any more, so it is kept
    (and deleting it would break those FKs).

Audit holds COUNTS ONLY. The whole point is to delete personal data, so each
WEBSITE_LEADS_PURGED row carries {deleted, older_than_days, cutoff, batch} and
never a name, email or phone. One row is written per committed batch, inside
that batch's own transaction, so a purge that dies half-way still leaves an
audit row for every batch that really deleted something (a single row written
after the loop would be lost with the crash).

Known residue, recorded deliberately: the create_lead() audit row's
after_state keeps organization_or_prospect_name (the business name, or the
contact's name when no business name was given). The audit chain is
append-only and hash-chained (app/audit/services.py), so that row cannot be
edited or removed without breaking verify_chain(). It carries no phone or
email and is accepted as a known residue, not contact data.

Dry run (the default) only counts; it deletes nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, or_, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.services import LEAD_CREATION_OPERATION_CODE
from app.leads.website_intake import get_website_intake_actor
from app.models.audit import SystemSetting
from app.models.commercial_sales import CommercialOperationsIdempotencyKey, Quote
from app.models.customers import Customer
from app.models.leads import (
    CustomerLocation,
    Lead,
    LeadAssignment,
    LeadContact,
    LeadFollowup,
    LeadInteraction,
    LeadNote,
    LeadProductInterest,
    LeadStatusHistory,
)

logger = logging.getLogger(__name__)

WEBSITE_LEAD_RETENTION_DAYS = 730  # 24 months, as the privacy page says (the default)

# The owner changes this number from the Control Center (System page) instead of
# asking a developer. It lives in the existing owner_system_settings table, so no
# schema change. WHY a bounded range: a typo (0, 6000) must never mean "delete
# everything tonight" or "keep personal data for 500 years"; 1..60 months is the
# range the business accepts.
# COUPLING: the public privacy page on actionaura.me states "24 months". If this
# value is changed, that page must be updated too, or the promise to visitors is
# no longer true. The UI card says so next to the input.
RETENTION_SETTING_KEY = "website_lead_retention_months"
DEFAULT_RETENTION_MONTHS = 24
MIN_RETENTION_MONTHS = 1
MAX_RETENTION_MONTHS = 60


def months_to_days(months: int) -> int:
    """24 -> 730, exactly today's constant. The window counts from the lead's
    LAST activity (see _candidate_conditions), not from its creation."""
    return months * 365 // 12


def _valid_months(value) -> bool:
    # bool is an int subclass; True must not be read as "1 month".
    return isinstance(value, int) and not isinstance(value, bool) and MIN_RETENTION_MONTHS <= value <= MAX_RETENTION_MONTHS


def _setting_row(session):
    return session.execute(select(SystemSetting).where(SystemSetting.key == RETENTION_SETTING_KEY)).scalars().first()


def get_retention_months(session) -> int:
    """Stored months, or the 24-month default when there is no row. A stored
    value that is malformed or out of range also falls back to 24 (and logs a
    warning): the purge runs unattended every night and must keep the published
    promise rather than crash or guess."""
    row = _setting_row(session)
    if row is None:
        return DEFAULT_RETENTION_MONTHS
    months = row.value.get("months") if isinstance(row.value, dict) else None
    if not _valid_months(months):
        logger.warning(
            "owner_system_settings[%s] holds an invalid value (%r); using the default of %d months",
            RETENTION_SETTING_KEY, row.value, DEFAULT_RETENTION_MONTHS,
        )
        return DEFAULT_RETENTION_MONTHS
    return months


def set_retention_months(session, months, staff_user) -> int:
    """Validate and store the month count. Returns the previous value. Does not
    commit: the caller writes the audit row (which commits) on the same session,
    so the change and its audit trail land together or not at all."""
    if not _valid_months(months):
        raise ValueError(
            f"months must be a whole number from {MIN_RETENTION_MONTHS} to {MAX_RETENTION_MONTHS}"
        )
    previous = get_retention_months(session)
    row = _setting_row(session)
    if row is None:
        session.add(SystemSetting(key=RETENTION_SETTING_KEY, value={"months": months}, updated_by_staff_user_id=staff_user.id))
    else:
        row.value = {"months": months}
        row.updated_by_staff_user_id = staff_user.id
    session.flush()
    return previous
_BATCH_SIZE = 500

# Children first, then the lead (FKs). All carry lead_id NOT NULL.
_LEAD_CHILD_MODELS = (
    LeadProductInterest,
    LeadStatusHistory,
    LeadAssignment,
    LeadInteraction,
    LeadFollowup,
    LeadNote,
    LeadContact,
)


def _candidate_conditions(cutoff: datetime, intake_profile_id):
    return (
        Lead.created_by_employee_profile_id == intake_profile_id,
        Lead.converted_at.is_(None),
        Lead.created_at < cutoff,
        Lead.updated_at < cutoff,
        or_(Lead.last_interaction_at.is_(None), Lead.last_interaction_at < cutoff),
        ~exists().where(LeadFollowup.lead_id == Lead.id, LeadFollowup.due_at >= cutoff),
        ~exists().where(Customer.converted_from_lead_id == Lead.id),
        ~exists().where(Quote.lead_id == Lead.id),
    )


def _delete_batch(ids: list) -> None:
    for child in _LEAD_CHILD_MODELS:
        db_session.execute(delete(child).where(child.lead_id.in_(ids)))
    # customer_locations is shared by Lead and Customer, but a CHECK constraint
    # requires EXACTLY ONE of lead_id/customer_id, so a row that points at a lead
    # never also belongs to a customer: deleting by lead_id cannot touch a
    # customer's location. (A "keep it, null the lead_id" branch would violate
    # that CHECK, so there is none.)
    db_session.execute(delete(CustomerLocation).where(CustomerLocation.lead_id.in_(ids)))
    # Otherwise a replayed website:<lead_id> key would resolve to a deleted lead.
    db_session.execute(
        delete(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.operation_code == LEAD_CREATION_OPERATION_CODE,
            CommercialOperationsIdempotencyKey.result_reference_id.in_(ids),
        )
    )
    db_session.execute(delete(Lead).where(Lead.id.in_(ids)))


def purge_old_website_leads(
    *,
    older_than_days: int = WEBSITE_LEAD_RETENTION_DAYS,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    if older_than_days < 1:
        raise ValueError("older_than_days must be at least 1")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    actor = get_website_intake_actor()
    if actor is None:
        # Never seeded: no lead can have been created by the intake actor.
        return {
            "dry_run": not apply,
            "older_than_days": older_than_days,
            "cutoff": cutoff.isoformat(),
            "candidates": 0,
            "deleted": 0,
        }
    conditions = _candidate_conditions(cutoff, actor[1])

    if not apply:
        candidates = len(db_session.execute(select(Lead.id).where(*conditions)).scalars().all())
        return {
            "dry_run": True,
            "older_than_days": older_than_days,
            "cutoff": cutoff.isoformat(),
            "candidates": candidates,
            "deleted": 0,
        }

    deleted = 0
    batch = 0
    while True:
        ids = db_session.execute(select(Lead.id).where(*conditions).limit(_BATCH_SIZE)).scalars().all()
        if not ids:
            break
        batch += 1
        try:
            _delete_batch(list(ids))
            # audit_record() commits, and it does so on the same session, so the
            # deletes and their audit row land in ONE transaction: either both
            # exist or neither does.
            audit_record(
                actor_staff_user_id=None,
                actor_role_snapshot=None,  # same as the other CLI/system jobs in app/cli.py
                action_code="WEBSITE_LEADS_PURGED",
                entity_type="lead",
                entity_public_id=None,
                after_state={
                    "deleted": len(ids),
                    "older_than_days": older_than_days,
                    "cutoff": cutoff.isoformat(),
                    "batch": batch,
                },
            )
        except Exception:
            db_session.rollback()
            raise
        deleted += len(ids)
    return {
        "dry_run": False,
        "older_than_days": older_than_days,
        "cutoff": cutoff.isoformat(),
        "candidates": deleted,
        "deleted": deleted,
    }
