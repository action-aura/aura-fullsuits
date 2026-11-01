"""Seller agreement clause 7 -- cash a seller collects goes back to the company within 24 hours.

THE CLAUSE. Every amount a seller takes from a customer is the company's. The seller hands it
over in full (no deduction, not even their own commission) by bank transfer or at the office,
within 24 hours of collecting it, and records each payment in Aura Owner. Missing the deadline
is a material breach -- but it is a matter for a person (a sanction case, sanctions.py), never
an automatic penalty: nothing here suspends, withholds or fines anyone.

WHEN AN OBLIGATION IS BORN. Whenever a payment with method CASH is recorded by a seller (a
staff member the seller agreement binds, people.is_seller). There is ONE writer of payments
(subscriptions/services.py::record_payment), so every route -- the one-screen sale, the
trial-conversion sale, the payments form, the JSON API -- is covered by one call from there.
A payment a MANAGER records is not a seller's cash and creates nothing. Collected-at is the
moment of recording (the payment's own date is a calendar day, too coarse for 24 hours).

HOW IT CLOSES (whole payment only; a partial hand-in is not modelled):
  seller uploads the bank-transfer slip          OPEN -> SUBMITTED   (same encrypted slip storage as the sale form)
  a payments.confirm holder confirms the slip    SUBMITTED -> RECEIVED
  a payments.confirm holder rejects the slip     SUBMITTED -> OPEN   (slip cleared, count + reason kept)
  a payments.confirm holder saw the cash         OPEN/SUBMITTED -> RECEIVED_AT_OFFICE
The seller can never close their own obligation (HANDIN_SELF_RECEIVE_FORBIDDEN).

OVERDUE = still OPEN after due_at. A SUBMITTED slip waiting for a manager is NOT overdue (the
wait is the company's, not the seller's). A payment later FAILED or VOIDED is not chased.

THE ALERT. One email per obligation, to the seller and to every holder of payments.confirm,
queued by scan_overdue (run by `flask sellers scan`, which the nightly scheduled-ops job calls).
overdue_notified_at is what makes it once; with email off nothing is queued and it stays unset,
so the alert goes out as soon as email is configured.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.customer_onboarding import documents
from app.extensions import db_session
from app.models.base import utcnow
from app.models.sellers import SellerCashHandIn
from app.models.staff import StaffUser
from app.models.subscriptions import PaymentRecord
from app.sellers import notify, people
from app.sellers.errors import SellerRulesError

HANDIN_HOURS = 24
CASH_METHOD = "CASH"
# A payment in one of these states is money that did not (or no longer does) exist: not chased.
UNCHASED_PAYMENT_STATUSES = ("FAILED", "VOIDED")
OVERDUE_ALERT_PERMISSION = "payments.confirm"
MAX_REASON_CHARS = 2000


# ------------------------------------------------------------------ birth

def open_for_seller_cash_payment(payment: PaymentRecord, recorded_by_staff_user_id, *, now: datetime | None = None) -> SellerCashHandIn | None:
    """Adds (never commits) the obligation for a freshly recorded payment, when it is a seller's cash.
    Called by record_payment inside the payment's own transaction: the payment and its obligation
    commit together or not at all. Returns None for anything that is not a seller's cash."""
    if payment.method != CASH_METHOD or recorded_by_staff_user_id is None:
        return None
    seller = db_session.get(StaffUser, recorded_by_staff_user_id)
    if not people.is_seller(seller):
        return None
    db_session.flush()  # the payment's id exists before anything references it
    now = now or utcnow()
    row = SellerCashHandIn(
        seller_staff_user_id=seller.id, payment_record_id=payment.id, amount=payment.amount, currency=payment.currency,
        collected_at=now, due_at=now + timedelta(hours=HANDIN_HOURS), status="OPEN",
    )
    db_session.add(row)
    return row


# ------------------------------------------------------------------ reading

def is_overdue(row: SellerCashHandIn, now: datetime | None = None) -> bool:
    return row.status == "OPEN" and (now or utcnow()) > row.due_at


def _chased(stmt):
    return stmt.join(PaymentRecord, PaymentRecord.id == SellerCashHandIn.payment_record_id).where(
        PaymentRecord.status.not_in(UNCHASED_PAYMENT_STATUSES)
    )


def get(handin_id) -> SellerCashHandIn | None:
    return db_session.get(SellerCashHandIn, handin_id)


def for_seller(staff_user_id, *, include_closed: bool = False) -> list[SellerCashHandIn]:
    stmt = select(SellerCashHandIn).where(SellerCashHandIn.seller_staff_user_id == staff_user_id)
    if not include_closed:
        stmt = stmt.where(SellerCashHandIn.status.in_(("OPEN", "SUBMITTED")))
    return list(db_session.execute(_chased(stmt).order_by(SellerCashHandIn.due_at, SellerCashHandIn.id)).scalars().all())


def for_managers(*, include_closed: bool = False) -> list[SellerCashHandIn]:
    """Everything still owed (OPEN, SUBMITTED), overdue first by due date; optionally the closed ones too."""
    stmt = select(SellerCashHandIn)
    if not include_closed:
        stmt = stmt.where(SellerCashHandIn.status.in_(("OPEN", "SUBMITTED")))
    return list(db_session.execute(_chased(stmt).order_by(SellerCashHandIn.due_at, SellerCashHandIn.id)).scalars().all())


def tile_for_seller(staff_user_id, *, now: datetime | None = None) -> dict:
    """The seller dashboard's "Cash to hand in": how many are owed, how many are overdue, and the next deadline."""
    now = now or utcnow()
    rows = [r for r in for_seller(staff_user_id) if r.status == "OPEN"]
    return {
        "open": len(rows), "overdue": sum(1 for r in rows if is_overdue(r, now)),
        "next_due_at": min((r.due_at for r in rows), default=None),
        "waiting_for_company": len([r for r in for_seller(staff_user_id) if r.status == "SUBMITTED"]),
    }


def counts_for_managers(*, now: datetime | None = None) -> tuple[int, int]:
    """(still owed, overdue) across all sellers, for the manager dashboard."""
    now = now or utcnow()
    rows = for_managers()
    return len(rows), sum(1 for r in rows if is_overdue(r, now))


def seller_name(staff_user_id) -> str:
    staff = db_session.get(StaffUser, staff_user_id)
    return staff.display_name if staff is not None else "-"


# ------------------------------------------------------------------ closing

def _locked(handin_id) -> SellerCashHandIn:
    row = db_session.execute(
        select(SellerCashHandIn).where(SellerCashHandIn.id == handin_id).with_for_update().execution_options(populate_existing=True)
    ).scalars().first()
    if row is None:
        db_session.rollback()
        raise SellerRulesError("HANDIN_NOT_FOUND")
    return row


def submit_slip(handin_id, *, seller: StaffUser, content: bytes | None) -> SellerCashHandIn:
    """The seller's bank-transfer slip. Every check runs before the first write; the file is
    stored encrypted exactly like the sale form's payment slip (customer_onboarding/documents.py)."""
    if not content:
        raise SellerRulesError("HANDIN_SLIP_REQUIRED")
    try:
        documents.clean_file(content)
    except documents.DocumentError as exc:
        raise SellerRulesError({"DOCUMENT_TYPE_NOT_ALLOWED": "HANDIN_SLIP_TYPE_NOT_ALLOWED",
                                "DOCUMENT_TOO_LARGE": "HANDIN_SLIP_TOO_LARGE"}.get(exc.code, "HANDIN_SLIP_INVALID")) from exc
    if not documents.storage_is_ready():
        raise SellerRulesError("HANDIN_SLIP_STORAGE_NOT_READY")
    row = _locked(handin_id)
    if row.seller_staff_user_id != seller.id:
        db_session.rollback()
        raise SellerRulesError("HANDIN_NOT_FOUND")  # not yours: indistinguishable from not there
    if row.status != "OPEN":
        db_session.rollback()
        raise SellerRulesError("HANDIN_NOT_OPEN")
    payment = db_session.get(PaymentRecord, row.payment_record_id)
    document = documents.store_document(
        customer_id=payment.customer_id, kind="PAYMENT_SLIP", content=content, uploaded_by_staff_user_id=seller.id,
        payment_record_id=payment.id,
    )
    db_session.flush()
    row.slip_document_id = document.id
    row.status = "SUBMITTED"
    row.submitted_at = utcnow()
    try:
        db_session.commit()
    except Exception:
        db_session.rollback()
        documents.discard_blob(document)
        raise
    audit_record(
        actor_staff_user_id=seller.id, actor_role_snapshot=None, action_code="SELLER_CASH_SLIP_SUBMITTED",
        entity_type="seller_cash_handin", entity_public_id=str(row.id),
        after_state={"status": "SUBMITTED", "payment_record_id": str(row.payment_record_id), "document_id": str(document.id)},
    )
    return row


def receive(handin_id, *, manager: StaffUser, at_office: bool) -> SellerCashHandIn:
    """A payments.confirm holder closes the obligation: `at_office=False` confirms the seller's slip
    (SUBMITTED -> RECEIVED); `at_office=True` records cash handed over in person (OPEN or SUBMITTED ->
    RECEIVED_AT_OFFICE). Never the seller themself."""
    row = _locked(handin_id)
    if row.seller_staff_user_id == manager.id:
        db_session.rollback()
        raise SellerRulesError("HANDIN_SELF_RECEIVE_FORBIDDEN")
    allowed = ("OPEN", "SUBMITTED") if at_office else ("SUBMITTED",)
    if row.status not in allowed:
        status = row.status
        db_session.rollback()
        raise SellerRulesError("HANDIN_ALREADY_CLOSED" if status in ("RECEIVED", "RECEIVED_AT_OFFICE") else "HANDIN_NO_SLIP_TO_CONFIRM")
    row.status = "RECEIVED_AT_OFFICE" if at_office else "RECEIVED"
    row.received_at = utcnow()
    row.received_by_staff_user_id = manager.id
    db_session.commit()
    audit_record(
        actor_staff_user_id=manager.id, actor_role_snapshot=None, action_code="SELLER_CASH_RECEIVED",
        entity_type="seller_cash_handin", entity_public_id=str(row.id),
        after_state={"status": row.status, "seller_staff_user_id": str(row.seller_staff_user_id), "amount": str(row.amount)},
    )
    return row


def reject_slip(handin_id, *, manager: StaffUser, reason: str) -> SellerCashHandIn:
    reason = (reason or "").strip()
    if not reason:
        raise SellerRulesError("HANDIN_REJECT_REASON_REQUIRED")
    if len(reason) > MAX_REASON_CHARS:
        raise SellerRulesError("HANDIN_REASON_TOO_LONG")
    row = _locked(handin_id)
    if row.seller_staff_user_id == manager.id:
        db_session.rollback()
        raise SellerRulesError("HANDIN_SELF_RECEIVE_FORBIDDEN")
    if row.status != "SUBMITTED":
        status = row.status
        db_session.rollback()
        raise SellerRulesError("HANDIN_ALREADY_CLOSED" if status in ("RECEIVED", "RECEIVED_AT_OFFICE") else "HANDIN_NO_SLIP_TO_CONFIRM")
    row.status = "OPEN"
    row.slip_document_id = None
    row.rejected_count += 1
    row.last_rejection_reason = reason
    row.submitted_at = None
    db_session.commit()
    audit_record(
        actor_staff_user_id=manager.id, actor_role_snapshot=None, action_code="SELLER_CASH_SLIP_REJECTED",
        entity_type="seller_cash_handin", entity_public_id=str(row.id), reason=reason,
        after_state={"status": "OPEN", "rejected_count": row.rejected_count},
    )
    return row


# ------------------------------------------------------------------ the alert

def scan_overdue(*, now: datetime | None = None, apply: bool = True) -> dict:
    """Finds obligations that are OPEN past due and not yet announced; with apply=True queues one
    email to the seller and one to every payments.confirm holder, then stamps overdue_notified_at.
    Safe to run as often as wanted: a stamped obligation is never announced again."""
    now = now or utcnow()
    due = list(db_session.execute(
        _chased(select(SellerCashHandIn).where(
            SellerCashHandIn.status == "OPEN", SellerCashHandIn.due_at < now, SellerCashHandIn.overdue_notified_at.is_(None)
        )).order_by(SellerCashHandIn.due_at, SellerCashHandIn.id)
    ).scalars().all())
    result = {"overdue_found": len(due), "announced": 0, "mails_queued": 0, "mail_off": 0}
    if not apply:
        return result
    managers = people.active_staff_holding(OVERDUE_ALERT_PERMISSION)
    for row in due:
        seller = db_session.get(StaffUser, row.seller_staff_user_id)
        lines = [
            f"{seller.display_name if seller else '-'}",
            f"{Decimal(row.amount):.2f} {row.currency}",
            row.collected_at.isoformat(timespec="minutes"),
            row.due_at.isoformat(timespec="minutes"),
        ]
        recipients = {s.id: s for s in managers}
        if seller is not None:
            recipients[seller.id] = seller
        queued = sum(1 for staff in recipients.values() if notify.queue("seller_cash_overdue", staff, lines))
        if queued == 0:
            result["mail_off"] += 1  # email off (or nobody to tell): leave it unstamped, it goes out when mail is on
            continue
        row.overdue_notified_at = now
        db_session.commit()
        result["announced"] += 1
        result["mails_queued"] += queued
        audit_record(
            actor_staff_user_id=None, actor_role_snapshot=None, action_code="SELLER_CASH_OVERDUE_ANNOUNCED",
            entity_type="seller_cash_handin", entity_public_id=str(row.id),
            after_state={"seller_staff_user_id": str(row.seller_staff_user_id), "amount": str(row.amount), "mails_queued": queued},
        )
    return result
