"""Phase 9.5C Milestone 9/10 -- manual interaction logging and follow-ups,
for both Lead and Customer. Both domains share the exact same shape
(interaction_type/summary/occurred_at; due_at/completed_at/cancelled_at/
notes), so one pair of functions handles both via a `parent_field` name
rather than four near-identical copies -- the model classes themselves
stay genuinely separate (LeadInteraction vs CustomerInteraction etc., per
crm-domain-reuse-matrix.md's own reasoning: pre- and post-conversion
history are distinct concepts even though the shape matches).
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.errors import LeadError
from app.models.base import utcnow
from app.models.leads import (
    INTERACTION_TYPES,
    CustomerFollowup,
    CustomerInteraction,
    LeadFollowup,
    LeadInteraction,
)
from app.services.pagination import DEFAULT_PAGE_SIZE, paginate

_MAX_SUMMARY_LEN = 4000
_MAX_FUTURE_SKEW = timedelta(minutes=5)


def _log_interaction(model, parent_field: str, parent_id: uuid.UUID, fields: dict, actor_employee_profile_id, actor_staff_user_id, entity_type: str):
    interaction_type = fields.get("interaction_type")
    if interaction_type not in INTERACTION_TYPES:
        raise LeadError("INTERACTION_TYPE_INVALID", interaction_type=interaction_type)
    summary = (fields.get("summary") or "").strip() or None
    if summary and len(summary) > _MAX_SUMMARY_LEN:
        raise LeadError("INTERACTION_SUMMARY_TOO_LONG", max_len=_MAX_SUMMARY_LEN)
    occurred_at = fields.get("occurred_at") or utcnow()
    if occurred_at > utcnow() + _MAX_FUTURE_SKEW:
        raise LeadError("INTERACTION_OCCURRED_AT_TOO_FUTURE")

    row = model(
        **{parent_field: parent_id},
        employee_profile_id=actor_employee_profile_id,
        interaction_type=interaction_type,
        summary=summary,
        occurred_at=occurred_at,
    )
    db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code=f"{entity_type.upper()}_INTERACTION_LOGGED",
        entity_type=entity_type,
        entity_public_id=str(parent_id),
        after_state={"interaction_type": interaction_type},
    )
    return row


def log_lead_interaction(lead_id: uuid.UUID, fields: dict, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> LeadInteraction:
    return _log_interaction(LeadInteraction, "lead_id", lead_id, fields, actor_employee_profile_id, actor_staff_user_id, "lead")


def log_customer_interaction(customer_id: uuid.UUID, fields: dict, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> CustomerInteraction:
    return _log_interaction(CustomerInteraction, "customer_id", customer_id, fields, actor_employee_profile_id, actor_staff_user_id, "customer")


def _create_followup(model, parent_field: str, parent_id: uuid.UUID, fields: dict, actor_employee_profile_id, actor_staff_user_id, entity_type: str):
    due_at = fields.get("due_at")
    if due_at is None:
        raise LeadError("FOLLOWUP_DUE_AT_REQUIRED")
    assigned_employee_profile_id = fields.get("employee_profile_id") or actor_employee_profile_id
    notes = (fields.get("notes") or "").strip() or None

    row = model(
        **{parent_field: parent_id},
        employee_profile_id=assigned_employee_profile_id,
        due_at=due_at,
        notes=notes,
    )
    db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code=f"{entity_type.upper()}_FOLLOWUP_CREATED",
        entity_type=entity_type,
        entity_public_id=str(parent_id),
        after_state={"due_at": due_at.isoformat()},
    )
    return row


def create_lead_followup(lead_id: uuid.UUID, fields: dict, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> LeadFollowup:
    return _create_followup(LeadFollowup, "lead_id", lead_id, fields, actor_employee_profile_id, actor_staff_user_id, "lead")


def create_customer_followup(customer_id: uuid.UUID, fields: dict, actor_employee_profile_id: uuid.UUID, actor_staff_user_id: uuid.UUID) -> CustomerFollowup:
    return _create_followup(CustomerFollowup, "customer_id", customer_id, fields, actor_employee_profile_id, actor_staff_user_id, "customer")


def followup_status(followup) -> str:
    """Derived, never stored -- OPEN/COMPLETED/CANCELLED, matching the
    governing spec's explicit instruction not to persist a redundant
    status column when it's fully derivable from existing timestamps."""
    if followup.cancelled_at is not None:
        return "CANCELLED"
    if followup.completed_at is not None:
        return "COMPLETED"
    return "OPEN"


def is_overdue(followup, *, as_of=None) -> bool:
    as_of = as_of or utcnow()
    return followup_status(followup) == "OPEN" and followup.due_at < as_of


def _complete_followup(followup, actor_staff_user_id: uuid.UUID, entity_type: str, parent_id: uuid.UUID):
    if followup.cancelled_at is not None:
        raise LeadError("FOLLOWUP_ALREADY_CANCELLED")
    if followup.completed_at is not None:
        return followup  # idempotent: completing an already-completed followup is a safe no-op
    followup.completed_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code=f"{entity_type.upper()}_FOLLOWUP_COMPLETED",
        entity_type=entity_type,
        entity_public_id=str(parent_id),
    )
    return followup


def complete_lead_followup(followup: LeadFollowup, actor_staff_user_id: uuid.UUID) -> LeadFollowup:
    return _complete_followup(followup, actor_staff_user_id, "lead", followup.lead_id)


def complete_customer_followup(followup: CustomerFollowup, actor_staff_user_id: uuid.UUID) -> CustomerFollowup:
    return _complete_followup(followup, actor_staff_user_id, "customer", followup.customer_id)


def _cancel_followup(followup, reason: str | None, actor_staff_user_id: uuid.UUID, entity_type: str, parent_id: uuid.UUID):
    if followup.completed_at is not None:
        raise LeadError("FOLLOWUP_ALREADY_COMPLETED")
    if not (reason or "").strip():
        raise LeadError("REASON_REQUIRED_FOR_FOLLOWUP_CANCEL")
    if followup.cancelled_at is not None:
        return followup  # idempotent
    followup.cancelled_at = utcnow()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code=f"{entity_type.upper()}_FOLLOWUP_CANCELLED",
        entity_type=entity_type,
        entity_public_id=str(parent_id),
        reason=reason,
    )
    return followup


def cancel_lead_followup(followup: LeadFollowup, reason: str, actor_staff_user_id: uuid.UUID) -> LeadFollowup:
    return _cancel_followup(followup, reason, actor_staff_user_id, "lead", followup.lead_id)


def cancel_customer_followup(followup: CustomerFollowup, reason: str, actor_staff_user_id: uuid.UUID) -> CustomerFollowup:
    return _cancel_followup(followup, reason, actor_staff_user_id, "customer", followup.customer_id)


def list_own_lead_followups_due_today(actor_employee_profile_id: uuid.UUID, *, as_of=None, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    as_of = as_of or utcnow()
    day_start = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    stmt = (
        select(LeadFollowup)
        .where(
            LeadFollowup.employee_profile_id == actor_employee_profile_id,
            LeadFollowup.completed_at.is_(None),
            LeadFollowup.cancelled_at.is_(None),
            LeadFollowup.due_at >= day_start,
            LeadFollowup.due_at < day_end,
        )
        .order_by(LeadFollowup.due_at)
    )
    return paginate(stmt, page, page_size)


def list_own_lead_followups_overdue(actor_employee_profile_id: uuid.UUID, *, as_of=None, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    as_of = as_of or utcnow()
    stmt = (
        select(LeadFollowup)
        .where(
            LeadFollowup.employee_profile_id == actor_employee_profile_id,
            LeadFollowup.completed_at.is_(None),
            LeadFollowup.cancelled_at.is_(None),
            LeadFollowup.due_at < as_of,
        )
        .order_by(LeadFollowup.due_at)
    )
    return paginate(stmt, page, page_size)
