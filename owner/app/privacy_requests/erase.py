"""The erasure of a person from a customer record.

WHAT GOES: the person. The shop's name becomes "Erased customer #<first 8 of the id>"; contacts,
addresses, locations, notes, interaction / follow-up text, the originating lead's personal fields,
the website account's address and sign-in (the account is disabled and its sessions and links die),
the ID / commercial-registration photos (the encrypted files are DELETED, not just hidden), the link
between the customer and their trial devices, and the customer's mail in the outbox.

WHAT STAYS, on purpose:
  * invoices, orders, quotes, payment records and the payment slips -- tax and accounting law;
  * the customer's tax identifier -- it is what ties those invoices to the taxpayer;
  * contract acceptances and consents -- the evidence of what was agreed and when (append-only by a
    database trigger; their email snapshot cannot be changed and is kept as evidence);
  * the audit log -- append-only and hash-chained; it is evidence too;
  * the device fingerprint of a trial device (fraud prevention, disclosed in the privacy notice) --
    only its link to the person is cut.

REFUSED while the customer is still a customer: an ACTIVE or PAST_DUE subscription, or a trial that
is still running (or a PILOT subscription we cannot place) -- the subscription must end first, with a
clear message. Nothing is written when it is refused.

Safe to run again: every step is a "set to erased" or a delete, so a run that was interrupted
(the database part committed, a file could not be removed) is simply finished by pressing the
button again -- the request stays OPEN until every encrypted file is really gone.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, delete, func, select, update

from app.audit.services import record as audit_record
from app.customer_onboarding import documents
from app.customer_onboarding.retention import _trial_is_running
from app.customer_onboarding.trial import trial_pilot_for_subscription
from app.extensions import db_session
from app.models.base import utcnow
from app.models.customer_accounts import CustomerAccount, CustomerAccountToken, CustomerSession
from app.models.customer_onboarding import CustomerDocument, TrialDeviceMark
from app.models.customers import Customer, CustomerAddress, CustomerContact, CustomerNote
from app.models.data_requests import DataRequest
from app.models.email_outbox import EmailOutbox
from app.models.leads import (
    CustomerFollowup, CustomerInteraction, CustomerLocation, Lead, LeadContact, LeadFollowup, LeadInteraction, LeadNote)
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today
from app.privacy_requests import service
from app.privacy_requests.errors import DataRequestError

ERASED_MARK = "[erased]"
ERASED_MAIL_DOMAIN = "erased.invalid"  # a reserved name that can never receive mail
_BLOCKING_STATUSES = ("ACTIVE", "PAST_DUE")


def erased_name(customer_id) -> str:
    return f"Erased customer #{str(customer_id)[:8]}"


def blocking_subscriptions(customer_id, today=None) -> list[Subscription]:
    """The subscriptions that make the customer a current customer: ACTIVE / PAST_DUE always, PILOT
    while its trial is still running (or when it is a PILOT we cannot tell is a trial)."""
    today = today or jordan_today()
    blocking = []
    for sub in db_session.execute(select(Subscription).where(Subscription.customer_id == customer_id)).scalars():
        if sub.status in _BLOCKING_STATUSES:
            blocking.append(sub)
        elif sub.status == "PILOT" and (trial_pilot_for_subscription(sub.id) is None or _trial_is_running(sub, today)):
            blocking.append(sub)
    return blocking


def _emails_of(customer: Customer, account: CustomerAccount | None, lead_id) -> set[str]:
    found = {c.business_email for c in db_session.execute(
        select(CustomerContact).where(CustomerContact.customer_id == customer.id)).scalars() if c.business_email}
    if account is not None:
        found.add(account.email)
    if lead_id is not None:
        lead = db_session.get(Lead, lead_id)
        if lead is not None and lead.email:
            found.add(lead.email)
        found.update(c.business_email for c in db_session.execute(
            select(LeadContact).where(LeadContact.lead_id == lead_id)).scalars() if c.business_email)
    return {e.strip().lower() for e in found if e}


def _scrub_customer(customer: Customer, now: datetime) -> None:
    customer.legal_name = erased_name(customer.id)
    customer.trade_name = None
    customer.city = None
    customer.country = None
    customer.timezone = None
    customer.commercial_registration_reference = None
    customer.manual_id_verification_required_at = None
    customer.lifecycle_status = "ARCHIVED"
    customer.archived_at = customer.archived_at or now
    customer.version = (customer.version or 1) + 1


def _scrub_people_records(customer_id, now: datetime) -> dict:
    n = {}
    n["contacts"] = db_session.execute(update(CustomerContact).where(CustomerContact.customer_id == customer_id).values(
        name="Erased contact", title=None, business_email=None, business_phone=None, notes=None, archived_at=now)).rowcount
    n["addresses"] = db_session.execute(update(CustomerAddress).where(CustomerAddress.customer_id == customer_id).values(
        line1=None, line2=None, city=None, country=None)).rowcount
    n["notes"] = db_session.execute(update(CustomerNote).where(CustomerNote.customer_id == customer_id).values(
        body=ERASED_MARK, archived_at=now)).rowcount
    n["interactions"] = db_session.execute(update(CustomerInteraction).where(
        CustomerInteraction.customer_id == customer_id).values(summary=None)).rowcount
    n["followups"] = db_session.execute(update(CustomerFollowup).where(
        CustomerFollowup.customer_id == customer_id).values(notes=None)).rowcount
    n["locations"] = db_session.execute(update(CustomerLocation).where(CustomerLocation.customer_id == customer_id).values(
        latitude=None, longitude=None, accuracy_meters=None, manual_address=None, reverse_geocoded_address=None,
        archived_at=now)).rowcount
    return n


def _scrub_lead(lead_id, now: datetime) -> dict:
    if lead_id is None:
        return {}
    db_session.execute(update(Lead).where(Lead.id == lead_id).values(
        organization_or_prospect_name=ERASED_MARK, primary_contact_name=None, phone=None, email=None, location_summary=None))
    db_session.execute(update(LeadContact).where(LeadContact.lead_id == lead_id).values(
        name="Erased contact", title=None, business_email=None, business_phone=None, notes=None, archived_at=now))
    db_session.execute(update(LeadNote).where(LeadNote.lead_id == lead_id).values(body=ERASED_MARK, archived_at=now))
    db_session.execute(update(LeadInteraction).where(LeadInteraction.lead_id == lead_id).values(summary=None))
    db_session.execute(update(LeadFollowup).where(LeadFollowup.lead_id == lead_id).values(notes=None))
    db_session.execute(update(CustomerLocation).where(CustomerLocation.lead_id == lead_id).values(
        latitude=None, longitude=None, accuracy_meters=None, manual_address=None, reverse_geocoded_address=None,
        archived_at=now))
    return {"lead": 1}


def _scrub_account(customer_id, actor_staff_user_id, now: datetime) -> bool:
    account = db_session.execute(select(CustomerAccount).where(CustomerAccount.customer_id == customer_id)).scalars().first()
    if account is None:
        return False
    account.email = f"erased-{str(customer_id)[:8]}@{ERASED_MAIL_DOMAIN}"
    account.password_hash = None
    account.status = "DISABLED"
    account.password_must_change = False
    account.email_verified_at = None
    account.session_version = account.session_version + 1
    if account.disabled_at is None:
        account.disabled_at = now
    account.disabled_reason = "Erased on a personal-data request"
    account.disabled_by_staff_user_id = actor_staff_user_id
    db_session.execute(update(CustomerSession).where(CustomerSession.account_id == account.id).values(
        ip_address=None, user_agent=None,
        revoked_at=func.coalesce(CustomerSession.revoked_at, now),
        revoked_reason=func.coalesce(CustomerSession.revoked_reason, "ERASED")))
    # Invite / reset / verify links only carry a hash; an EMAIL_CHANGE link also carries the new address.
    db_session.execute(delete(CustomerAccountToken).where(CustomerAccountToken.account_id == account.id))
    return True


def _scrub_outbox(emails: set[str]) -> int:
    if not emails:
        return 0
    result = db_session.execute(update(EmailOutbox).where(func.lower(EmailOutbox.to_address).in_(sorted(emails))).values(
        to_address=f"erased@{ERASED_MAIL_DOMAIN}", subject=ERASED_MARK, body_text=ERASED_MARK, body_html=None,
        status=case((EmailOutbox.status.in_(("QUEUED", "SENDING")), "CANCELLED"), else_=EmailOutbox.status)))
    return result.rowcount


def carry_out_erase(request_id, *, actor_staff_user_id, now: datetime | None = None) -> DataRequest:
    """Runs the erasure for an OPEN ERASE request and closes it DONE. Raises DataRequestError (writing
    nothing) when the request is not an open ERASE or the customer is still a customer; raises
    ERASE_FILES_NOT_DELETED (request stays OPEN) when a database part committed but a file could not
    be removed -- press the button again."""
    now = now or utcnow()
    row = service.get_open_for_update(request_id)
    if row.kind != "ERASE":
        db_session.rollback()
        raise DataRequestError("NOT_AN_ERASE_REQUEST")
    customer = db_session.get(Customer, row.customer_id)
    blocking = blocking_subscriptions(customer.id)
    if blocking:
        db_session.rollback()
        raise DataRequestError("ERASE_BLOCKED_BY_SUBSCRIPTION", statuses=", ".join(sorted({s.status for s in blocking})))

    account = db_session.execute(select(CustomerAccount).where(CustomerAccount.customer_id == customer.id)).scalars().first()
    lead_id = customer.converted_from_lead_id
    emails = _emails_of(customer, account, lead_id)

    counts = _scrub_people_records(customer.id, now)
    counts.update(_scrub_lead(lead_id, now))
    counts["account"] = int(_scrub_account(customer.id, actor_staff_user_id, now))
    counts["mail_rows"] = _scrub_outbox(emails)
    counts["trial_device_marks_unlinked"] = db_session.execute(
        update(TrialDeviceMark).where(TrialDeviceMark.customer_id == customer.id).values(customer_id=None)).rowcount
    for document in documents.mark_identity_documents_purged(customer.id, now=now):
        document.purge_after = None
    _scrub_customer(customer, now)
    db_session.commit()

    # The files go AFTER the commit (a rolled-back run never destroys a file). Every ID / registration
    # row of the customer is tried -- purged ones too -- so a run that was cut short is finished here.
    leftover = [d for d in db_session.execute(select(CustomerDocument).where(
        CustomerDocument.customer_id == customer.id, CustomerDocument.kind.in_(documents.IDENTITY_KINDS))).scalars()
        if not documents.remove_blob(d)]
    counts["files_deleted"] = db_session.execute(select(func.count()).select_from(CustomerDocument).where(
        CustomerDocument.customer_id == customer.id, CustomerDocument.kind.in_(documents.IDENTITY_KINDS))).scalar_one() - len(leftover)
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_ERASED",
        entity_type="customer", entity_public_id=str(customer.id),
        after_state={"request_id": str(row.id), "scrubbed": counts, "files_not_deleted": len(leftover)},
    )
    if leftover:
        raise DataRequestError("ERASE_FILES_NOT_DELETED", count=len(leftover))

    row = service.get_open_for_update(request_id)
    row.fulfilled_at = now
    service.close(row, status="DONE", actor_staff_user_id=actor_staff_user_id, now=now, note=(
        "Erased: the person's details, the website sign-in and the ID / registration photos were removed. "
        "Invoices, payment records, payment slips, contract acceptances and the audit log were kept as "
        "the law requires."))
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="DATA_REQUEST_DONE",
        entity_type="data_request", entity_public_id=str(row.id),
        after_state={"customer_id": str(row.customer_id), "kind": row.kind}, reason=row.answer_note,
    )
    return row
