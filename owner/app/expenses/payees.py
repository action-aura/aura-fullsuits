"""Phase 9.5E -- Payee service. Genuinely new authority (Milestone 1's audit
found no existing vendor/payee entity to reuse)."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.models.expenses import PAYEE_TYPES, Payee


def create_payee(
    *,
    payee_type: str,
    display_name: str,
    employee_profile_id: uuid.UUID | None,
    external_contact_reference: str | None,
    created_by_staff_user_id: uuid.UUID,
) -> Payee:
    if payee_type not in PAYEE_TYPES:
        raise ExpenseError("PAYEE_TYPE_INVALID")
    if payee_type == "EMPLOYEE" and employee_profile_id is None:
        raise ExpenseError("EMPLOYEE_BENEFICIARY_REQUIRED")
    if payee_type == "EMPLOYEE" and external_contact_reference:
        raise ExpenseError("EXTERNAL_CONTACT_NOT_ALLOWED_FOR_EMPLOYEE")
    if payee_type == "EXTERNAL":
        employee_profile_id = None

    payee = Payee(
        payee_type=payee_type,
        display_name=display_name,
        employee_profile_id=employee_profile_id,
        external_contact_reference=external_contact_reference,
        is_active=True,
        created_by_staff_user_id=created_by_staff_user_id,
    )
    db_session.add(payee)
    db_session.flush()
    db_session.commit()

    audit_record(
        actor_staff_user_id=created_by_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_PAYEE_CREATED",
        entity_type="expense_payee",
        entity_public_id=str(payee.id),
        after_state={"payee_type": payee_type, "display_name": display_name},
    )
    return payee


def deactivate_payee(payee: Payee, *, actor_staff_user_id: uuid.UUID) -> Payee:
    payee.is_active = False
    db_session.commit()

    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="EXPENSE_PAYEE_DEACTIVATED",
        entity_type="expense_payee",
        entity_public_id=str(payee.id),
        after_state={"is_active": False},
    )
    return payee


def list_active_payees() -> list[Payee]:
    return db_session.execute(
        select(Payee).where(Payee.is_active.is_(True)).order_by(Payee.display_name)
    ).scalars().all()
