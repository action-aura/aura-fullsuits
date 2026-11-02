"""The copy of everything we hold about a customer, as one ZIP.

  data.json                the records: profile, contacts, addresses, notes, website account,
                           locations, interactions, subscriptions, licences, devices, orders,
                           invoices, payments, consents, contract acceptances, the originating
                           lead, the data requests themselves, and the audit entries about them.
  documents/<kind>-<n>.<ext>   the ID / commercial-registration photos (and any payment slip),
                           DECRYPTED -- only when the person building the export may open them
                           (customer_documents.view). Without that permission the ZIP carries the
                           records only and data.json says the photos were withheld, so a
                           privacy.requests holder who is not allowed to see an ID photo cannot
                           get one through this door.

What is deliberately NOT in it: the website password hash, session / invite / reset tokens, the
encrypted-file key names and storage paths, device signatures (secrets or machinery, not facts
about the person), and other customers' records.

Every export is audited (CUSTOMER_DATA_EXPORTED), and every photo put into the ZIP is audited the
same way opening it on screen is (CUSTOMER_DOCUMENT_VIEWED).
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.customer_onboarding import documents
from app.extensions import db_session
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.commercial_ops import PilotRecord
from app.models.commercial_sales import (
    CommercialInvoice, CommercialInvoiceItem, Quote, SalesOrder, SalesOrderLine)
from app.models.contracts import ContractAcceptance, ContractConsent
from app.models.customer_accounts import CustomerAccount
from app.models.customer_onboarding import CustomerDocument, TrialDeviceMark
from app.models.customers import Customer, CustomerAddress, CustomerContact, CustomerNote
from app.models.data_requests import DataRequest
from app.models.installations import Installation
from app.models.leads import (
    CustomerAssignment, CustomerFollowup, CustomerInteraction, CustomerLocation, Lead, LeadContact, LeadFollowup,
    LeadInteraction, LeadNote)
from app.models.licensing import License
from app.models.subscriptions import PaymentRecord, Subscription

# Columns that are secrets or machinery, never facts about the person.
EXCLUDED_COLUMNS = frozenset({
    "password_hash", "token_hash", "storage_key", "enc_key_id", "device_signature",
})
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "application/pdf": "pdf"}


def _plain(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in EXCLUDED_COLUMNS}


def _rows(model, *where) -> list[dict]:
    stmt = select(model).where(*where)
    order = getattr(model, "created_at", None)
    if order is not None:
        stmt = stmt.order_by(order)
    return [_plain(r) for r in db_session.execute(stmt).scalars()]


def _ids(rows: list[dict]) -> list:
    return [r["id"] for r in rows]


def collect_records(customer: Customer) -> dict:
    """Every record about the customer, keyed by what it is. Pure reads."""
    cid = customer.id
    out: dict = {"customer": _plain(customer)}
    out["contacts"] = _rows(CustomerContact, CustomerContact.customer_id == cid)
    out["addresses"] = _rows(CustomerAddress, CustomerAddress.customer_id == cid)
    out["notes"] = _rows(CustomerNote, CustomerNote.customer_id == cid)
    out["website_account"] = _rows(CustomerAccount, CustomerAccount.customer_id == cid)
    out["locations"] = _rows(CustomerLocation, CustomerLocation.customer_id == cid)
    out["interactions"] = _rows(CustomerInteraction, CustomerInteraction.customer_id == cid)
    out["followups"] = _rows(CustomerFollowup, CustomerFollowup.customer_id == cid)
    out["assignments"] = _rows(CustomerAssignment, CustomerAssignment.customer_id == cid)
    out["subscriptions"] = _rows(Subscription, Subscription.customer_id == cid)
    out["trials"] = _rows(PilotRecord, PilotRecord.customer_id == cid)
    out["licences"] = _rows(License, License.customer_id == cid)
    out["devices"] = _rows(Installation, Installation.customer_id == cid)
    out["trial_device_marks"] = _rows(TrialDeviceMark, TrialDeviceMark.customer_id == cid)
    out["quotes"] = _rows(Quote, Quote.customer_id == cid)
    out["orders"] = _rows(SalesOrder, SalesOrder.customer_id == cid)
    out["order_lines"] = _rows(SalesOrderLine, SalesOrderLine.sales_order_id.in_(_ids(out["orders"]) or [None]))
    out["invoices"] = _rows(CommercialInvoice, CommercialInvoice.customer_id == cid)
    out["invoice_items"] = _rows(
        CommercialInvoiceItem, CommercialInvoiceItem.commercial_invoice_id.in_(_ids(out["invoices"]) or [None]))
    out["payments"] = _rows(PaymentRecord, PaymentRecord.customer_id == cid)
    out["contract_acceptances"] = _rows(ContractAcceptance, ContractAcceptance.customer_id == cid)
    out["consents"] = _rows(ContractConsent, ContractConsent.customer_id == cid)
    out["documents"] = _rows(CustomerDocument, CustomerDocument.customer_id == cid)
    out["data_requests"] = _rows(DataRequest, DataRequest.customer_id == cid)
    if customer.converted_from_lead_id is not None:
        lead_id = customer.converted_from_lead_id
        out["lead"] = _rows(Lead, Lead.id == lead_id)
        out["lead_contacts"] = _rows(LeadContact, LeadContact.lead_id == lead_id)
        out["lead_notes"] = _rows(LeadNote, LeadNote.lead_id == lead_id)
        out["lead_interactions"] = _rows(LeadInteraction, LeadInteraction.lead_id == lead_id)
        out["lead_followups"] = _rows(LeadFollowup, LeadFollowup.lead_id == lead_id)
        out["lead_locations"] = _rows(CustomerLocation, CustomerLocation.lead_id == lead_id)
    subject_ids = {str(cid)}
    for key in ("website_account", "subscriptions", "licences", "orders", "invoices", "payments", "documents",
                "contract_acceptances", "data_requests"):
        subject_ids.update(str(i) for i in _ids(out[key]))
    out["audit_entries"] = [
        {"at": r.created_at, "action": r.action_code, "entity_type": r.entity_type, "entity_id": r.entity_public_id,
         "result": r.result, "reason": r.reason, "before": r.before_state_redacted, "after": r.after_state_redacted}
        for r in db_session.execute(
            select(AuditLog).where(AuditLog.entity_public_id.in_(sorted(subject_ids))).order_by(AuditLog.created_at)
        ).scalars()
    ]
    return out


def build_export(customer: Customer, *, actor_staff_user_id, may_open_documents: bool, now: datetime | None = None) -> bytes:
    """The ZIP bytes. Writes audit rows (the export, and each photo put in it)."""
    now = now or utcnow()
    records = collect_records(customer)
    photos: list[tuple[str, bytes, CustomerDocument]] = []
    notes: list[dict] = []
    live_documents = documents.documents_of(customer.id)
    if not may_open_documents:
        notes.append({"documents": "withheld: the person who built this export may not open ID / registration photos"})
    else:
        for index, document in enumerate(live_documents, start=1):
            try:
                content, content_type = documents.read_document(document)
            except documents.DocumentError as exc:
                notes.append({"document": str(document.id), "not_included": exc.code})
                continue
            photos.append((f"documents/{document.kind.lower()}-{index}.{_EXTENSIONS.get(content_type, 'bin')}", content, document))
    payload = {
        "exported_at": now, "customer_id": customer.id,
        "note": "Everything Action Aura holds about this customer. Secrets and internal keys are left out.",
        "export_notes": notes, "records": records,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.json", json.dumps(payload, default=str, ensure_ascii=False, indent=2))
        for name, content, _document in photos:
            archive.writestr(name, content)
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_DATA_EXPORTED",
        entity_type="customer", entity_public_id=str(customer.id),
        after_state={"photos_included": len(photos), "photos_withheld": not may_open_documents,
                     "records": {k: len(v) for k, v in records.items() if isinstance(v, list)}},
    )
    for _name, _content, document in photos:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_DOCUMENT_VIEWED",
            entity_type="customer_document", entity_public_id=str(document.id),
            after_state={"kind": document.kind, "customer_id": str(customer.id), "via": "data_export"},
        )
    return buffer.getvalue()
