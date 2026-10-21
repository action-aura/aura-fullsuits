"""24-month purge of unconverted WEBSITE leads.

Why this exists: the actionaura.me privacy page promises "We keep demo
requests for 24 months, then delete them. This applies to the copies in our
mailbox and in our internal customer system too." The website intake
(app/leads/website_intake.py) copies every demo request into owner_leads
(source "WEBSITE") with a LeadNote holding the contact and message text.
Nothing deleted those copies, so the promise was not true for the internal
customer system. This module makes it true; it is run nightly by
deploy/oracle-free/run_scheduled_ops.sh via `flask leads purge-website --apply`.

What counts as "a demo request that was never acted on" (and so is purged):
source == "WEBSITE", converted_at IS NULL, older than the cutoff, AND no
Customer.converted_from_lead_id and no Quote.lead_id points at it. Those two
are the only foreign keys onto owner_leads from outside app/models/leads.py.
A lead that became a customer or a quote is business history, not "a demo
request" any more, so it is kept (and deleting it would break those FKs).

Audit holds COUNTS ONLY. The whole point is to delete personal data, so the
WEBSITE_LEADS_PURGED row carries {deleted, older_than_days, cutoff} and never
a name, email or phone.

Known residue, recorded deliberately: the create_lead() audit row's
after_state keeps organization_or_prospect_name (the business name, or the
contact's name when no business name was given). The audit chain is
append-only and hash-chained (app/audit/services.py), so that row cannot be
edited or removed without breaking verify_chain(). It carries no phone or
email and is accepted as a known residue, not contact data.

Dry run (the default) only counts; it deletes nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.services import LEAD_CREATION_OPERATION_CODE
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

WEBSITE_LEAD_RETENTION_DAYS = 730  # 24 months, as the privacy page says
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


def _candidate_conditions(cutoff: datetime):
    return (
        Lead.source == "WEBSITE",
        Lead.converted_at.is_(None),
        Lead.created_at < cutoff,
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
    conditions = _candidate_conditions(cutoff)

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
    while True:
        ids = db_session.execute(select(Lead.id).where(*conditions).limit(_BATCH_SIZE)).scalars().all()
        if not ids:
            break
        try:
            _delete_batch(list(ids))
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        deleted += len(ids)

    if deleted:
        audit_record(
            actor_staff_user_id=None,
            actor_role_snapshot=None,  # same as the other CLI/system jobs in app/cli.py
            action_code="WEBSITE_LEADS_PURGED",
            entity_type="lead",
            entity_public_id=None,
            after_state={"deleted": deleted, "older_than_days": older_than_days, "cutoff": cutoff.isoformat()},
        )
    return {
        "dry_run": False,
        "older_than_days": older_than_days,
        "cutoff": cutoff.isoformat(),
        "candidates": deleted,
        "deleted": deleted,
    }
