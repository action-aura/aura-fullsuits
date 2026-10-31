"""The e-contract: versions, publishing, and the append-only acceptance evidence.

WHO ACCEPTS WHAT
  CUSTOMER  the shop owner, in the Aura app, the first time they sign in (before the
            app lets them in). One acceptance per customer per version.
  SELLER    a staff member who sells, inside Owner. One per staff member per version.
The two audiences never mix: a seller's acceptance cannot satisfy a customer's gate
and the other way round (record_* check the version's audience, and the database
CHECKs the audience against who is named).

LIFECYCLE   draft -> (lawyer approved) -> published -> retired.
  * Publishing a version that is not lawyer-approved is REFUSED, except when the
    server runs with OWNER_CONTRACT_ALLOW_DRAFT=1 -- that switch exists only for
    practice sessions (the app then shows a DRAFT watermark because the sign-in
    answer says lawyer_approved=false). A real customer must never be bound by words
    a lawyer has not read.
  * Publishing retires the previous live version of the same audience and language
    in the same transaction (at most one is live; the database enforces it), so
    customers who accepted the old one are asked again.
  * A version's text never changes (see models/contracts.py); fix = new version.

The text the app shows is `body`; its SHA-256 (lowercase hex of the UTF-8 bytes) is
what the person is recorded as having accepted. A PDF (the lawyer's signed copy)
can be attached for the archive; it is encrypted like the customer documents.
"""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.customer_onboarding import documents
from app.extensions import db_session
from app.models.contracts import CONTRACT_AUDIENCES, CONTRACT_LANGUAGES, ContractAcceptance, ContractVersion

MAX_BODY_CHARS = 200_000
_LABEL_RE = re.compile(r"^[A-Za-z0-9._ -]{1,64}$")


class ContractError(ValueError):
    """Stable code in .code; the staff screens put it into words."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def allow_draft_publish() -> bool:
    """True only for practice servers (OWNER_CONTRACT_ALLOW_DRAFT=1)."""
    return bool(current_app.config.get("CONTRACT_ALLOW_DRAFT"))


# ------------------------------------------------------------------ versions

def create_version(
    *, audience: str, language: str, version_label: str, title: str, body: str, lawyer_approved: bool,
    approval_note: str = "", pdf_bytes: bytes | None = None, actor_staff_user_id=None,
) -> ContractVersion:
    audience = (audience or "").strip().upper()
    language = (language or "").strip().lower()
    version_label = (version_label or "").strip()
    title = (title or "").strip()
    if audience not in CONTRACT_AUDIENCES:
        raise ContractError("AUDIENCE_INVALID")
    if language not in CONTRACT_LANGUAGES:
        raise ContractError("LANGUAGE_INVALID")
    if not _LABEL_RE.match(version_label):
        raise ContractError("LABEL_INVALID")
    if not title or len(title) > 255:
        raise ContractError("TITLE_INVALID")
    # Normalise line endings once, here: a browser textarea sends CRLF and the app
    # would hash a different byte string than the one stored if it ever re-joined lines.
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        raise ContractError("BODY_REQUIRED")
    if len(body) > MAX_BODY_CHARS:
        raise ContractError("BODY_TOO_LONG")
    if lawyer_approved and not (approval_note or "").strip():
        raise ContractError("APPROVAL_NOTE_REQUIRED")
    if db_session.execute(
        select(ContractVersion.id).where(
            ContractVersion.audience == audience, ContractVersion.language == language,
            ContractVersion.version_label == version_label,
        )
    ).first() is not None:
        raise ContractError("LABEL_TAKEN")

    version_id = uuid.uuid4()
    version = ContractVersion(
        id=version_id, audience=audience, language=language, version_label=version_label, title=title, body=body,
        body_sha256=body_sha256(body), lawyer_approved=bool(lawyer_approved),
        lawyer_approval_note=(approval_note or "").strip() or None,
        lawyer_approved_at=_now() if lawyer_approved else None,
        lawyer_approved_by_staff_user_id=actor_staff_user_id if lawyer_approved else None,
        created_by_staff_user_id=actor_staff_user_id,
    )
    stored_blob = None
    if pdf_bytes:
        stored_blob = _store_pdf(version, pdf_bytes)
    db_session.add(version)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        if stored_blob:
            _discard_pdf(stored_blob)
        raise ContractError("LABEL_TAKEN")
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CONTRACT_VERSION_CREATED",
        entity_type="contract_version", entity_public_id=str(version.id),
        after_state={"audience": audience, "language": language, "label": version_label,
                     "sha256": version.body_sha256, "lawyer_approved": version.lawyer_approved,
                     "has_pdf": bool(pdf_bytes)},
    )
    return version


def approve(version: ContractVersion, *, note: str, actor_staff_user_id) -> None:
    """Records that a lawyer has read THIS text. Metadata only: the text and its hash
    are untouched (and the database would refuse it)."""
    note = (note or "").strip()
    if not note:
        raise ContractError("APPROVAL_NOTE_REQUIRED")
    if version.lawyer_approved:
        return
    version.lawyer_approved = True
    version.lawyer_approval_note = note
    version.lawyer_approved_at = _now()
    version.lawyer_approved_by_staff_user_id = actor_staff_user_id
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CONTRACT_VERSION_APPROVED",
        entity_type="contract_version", entity_public_id=str(version.id), reason=note,
    )


def publish(version: ContractVersion, *, actor_staff_user_id) -> None:
    if version.published_at is not None:
        raise ContractError("ALREADY_PUBLISHED")
    if not version.lawyer_approved and not allow_draft_publish():
        raise ContractError("NOT_LAWYER_APPROVED")
    now = _now()
    previous = db_session.execute(
        select(ContractVersion).where(
            ContractVersion.audience == version.audience, ContractVersion.language == version.language,
            ContractVersion.published_at.is_not(None), ContractVersion.retired_at.is_(None),
        )
    ).scalars().all()
    for old in previous:
        old.retired_at = now
    # Flush the retirement first: the one-live-version unique index is checked per
    # statement, so the old row must stop being live before the new one starts.
    db_session.flush()
    version.published_at = now
    version.published_by_staff_user_id = actor_staff_user_id
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        raise ContractError("ANOTHER_VERSION_IS_LIVE")
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CONTRACT_VERSION_PUBLISHED",
        entity_type="contract_version", entity_public_id=str(version.id),
        after_state={"audience": version.audience, "language": version.language, "sha256": version.body_sha256,
                     "lawyer_approved": version.lawyer_approved, "retired": [str(o.id) for o in previous]},
    )


def retire(version: ContractVersion, *, actor_staff_user_id) -> None:
    if version.published_at is None:
        raise ContractError("NOT_PUBLISHED")
    if version.retired_at is not None:
        return
    version.retired_at = _now()
    db_session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CONTRACT_VERSION_RETIRED",
        entity_type="contract_version", entity_public_id=str(version.id),
    )


def get_version(version_id) -> ContractVersion | None:
    try:
        return db_session.get(ContractVersion, uuid.UUID(str(version_id)))
    except (ValueError, TypeError):
        return None


def list_versions() -> list[ContractVersion]:
    return list(db_session.execute(
        select(ContractVersion).order_by(ContractVersion.audience, ContractVersion.language, ContractVersion.created_at.desc())
    ).scalars().all())


def live_version(audience: str, language: str) -> ContractVersion | None:
    return db_session.execute(
        select(ContractVersion).where(
            ContractVersion.audience == audience, ContractVersion.language == language,
            ContractVersion.published_at.is_not(None), ContractVersion.retired_at.is_(None),
        )
    ).scalars().first()


def live_version_for(audience: str, preferred_language: str | None) -> ContractVersion | None:
    """The version a person is shown: their language if one is live, else Arabic
    (the product is Arabic-first), else English."""
    for language in dict.fromkeys([(preferred_language or "").lower(), "ar", "en"]):
        if language in CONTRACT_LANGUAGES:
            found = live_version(audience, language)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------- acceptances

def customer_has_accepted(customer_id, version_id) -> bool:
    return db_session.execute(
        select(ContractAcceptance.id).where(
            ContractAcceptance.customer_id == customer_id, ContractAcceptance.contract_version_id == version_id
        )
    ).first() is not None


def customer_gate_is_open(customer_id, account_language: str | None) -> bool:
    """True when the customer may activate a device as far as the contract goes:
    there is no live customer contract, or they accepted the live one."""
    live = live_version_for("CUSTOMER", account_language)
    return live is None or customer_has_accepted(customer_id, live.id)


def record_customer_acceptance(
    *, account, version: ContractVersion, text_sha256: str, language: str, device_key_fingerprint: str,
    device_signature: str | None, ip: str | None, app_version: str | None, installation_label: str | None,
) -> ContractAcceptance:
    """Appends the evidence row (idempotent: the same customer accepting the same
    version again returns the existing row). Refuses a version of the wrong
    audience, one that is not live, or a hash that is not the stored text's."""
    if version.audience != "CUSTOMER":
        raise ContractError("WRONG_AUDIENCE")
    if version.published_at is None or version.retired_at is not None:
        raise ContractError("VERSION_NOT_LIVE")
    if (text_sha256 or "").strip().lower() != version.body_sha256:
        raise ContractError("TEXT_MISMATCH")
    existing = db_session.execute(
        select(ContractAcceptance).where(
            ContractAcceptance.customer_id == account.customer_id, ContractAcceptance.contract_version_id == version.id
        )
    ).scalars().first()
    if existing is not None:
        return existing
    row = ContractAcceptance(
        contract_version_id=version.id, audience="CUSTOMER", customer_id=account.customer_id, account_id=account.id,
        email_snapshot=account.email, installation_id=(installation_label or "")[:128] or None,
        device_key_fingerprint=device_key_fingerprint, device_signature=device_signature, ip=(ip or "")[:64] or None,
        app_version=(app_version or "")[:64] or None, language=version.language, text_sha256=version.body_sha256,
        accepted_at=_now(),
    )
    db_session.add(row)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()  # two devices accepted at the same instant: the first row is the evidence
        return db_session.execute(
            select(ContractAcceptance).where(
                ContractAcceptance.customer_id == account.customer_id, ContractAcceptance.contract_version_id == version.id
            )
        ).scalars().one()
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="CONTRACT_ACCEPTED_BY_CUSTOMER",
        entity_type="contract_acceptance", entity_public_id=str(row.id),
        after_state={"customer_id": str(account.customer_id), "version_id": str(version.id), "sha256": version.body_sha256},
    )
    return row


def seller_has_accepted(staff_user_id, version_id) -> bool:
    return db_session.execute(
        select(ContractAcceptance.id).where(
            ContractAcceptance.staff_user_id == staff_user_id, ContractAcceptance.contract_version_id == version_id
        )
    ).first() is not None


def record_seller_acceptance(*, staff, version: ContractVersion, ip: str | None) -> ContractAcceptance:
    if version.audience != "SELLER":
        raise ContractError("WRONG_AUDIENCE")
    if version.published_at is None or version.retired_at is not None:
        raise ContractError("VERSION_NOT_LIVE")
    existing = db_session.execute(
        select(ContractAcceptance).where(
            ContractAcceptance.staff_user_id == staff.id, ContractAcceptance.contract_version_id == version.id
        )
    ).scalars().first()
    if existing is not None:
        return existing
    row = ContractAcceptance(
        contract_version_id=version.id, audience="SELLER", staff_user_id=staff.id, email_snapshot=staff.email,
        ip=(ip or "")[:64] or None, language=version.language, text_sha256=version.body_sha256, accepted_at=_now(),
    )
    db_session.add(row)
    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return db_session.execute(
            select(ContractAcceptance).where(
                ContractAcceptance.staff_user_id == staff.id, ContractAcceptance.contract_version_id == version.id
            )
        ).scalars().one()
    audit_record(
        actor_staff_user_id=staff.id, actor_role_snapshot=None, action_code="CONTRACT_ACCEPTED_BY_SELLER",
        entity_type="contract_acceptance", entity_public_id=str(row.id),
        after_state={"version_id": str(version.id), "sha256": version.body_sha256},
    )
    return row


# ------------------------------------------------------------------ evidence

def _acceptance_json(row: ContractAcceptance, version: ContractVersion) -> dict:
    return {
        "acceptance_id": str(row.id), "audience": row.audience, "accepted_at": row.accepted_at.isoformat(),
        "customer_id": str(row.customer_id) if row.customer_id else None,
        "account_id": str(row.account_id) if row.account_id else None,
        "staff_user_id": str(row.staff_user_id) if row.staff_user_id else None,
        "email": row.email_snapshot, "language": row.language, "text_sha256": row.text_sha256,
        "device_key_fingerprint": row.device_key_fingerprint, "device_signature": row.device_signature,
        "installation_label": row.installation_id, "ip": row.ip, "app_version": row.app_version,
        "contract": {
            "version_id": str(version.id), "version_label": version.version_label, "title": version.title,
            "language": version.language, "sha256": version.body_sha256, "lawyer_approved": version.lawyer_approved,
            "published_at": version.published_at.isoformat() if version.published_at else None,
        },
    }


def _evidence(rows: list[ContractAcceptance], subject: dict) -> dict:
    versions = {}
    out = []
    for row in rows:
        version = versions.get(row.contract_version_id) or db_session.get(ContractVersion, row.contract_version_id)
        versions[row.contract_version_id] = version
        out.append(_acceptance_json(row, version))
    return {"format": "aura-contract-evidence-v1", "generated_at": _now().isoformat(), "subject": subject,
            "acceptances": out}


def evidence_for_customer(customer_id) -> dict:
    rows = db_session.execute(
        select(ContractAcceptance).where(ContractAcceptance.customer_id == customer_id).order_by(ContractAcceptance.accepted_at)
    ).scalars().all()
    return _evidence(list(rows), {"kind": "customer", "customer_id": str(customer_id)})


def evidence_for_seller(staff_user_id) -> dict:
    rows = db_session.execute(
        select(ContractAcceptance).where(ContractAcceptance.staff_user_id == staff_user_id).order_by(ContractAcceptance.accepted_at)
    ).scalars().all()
    return _evidence(list(rows), {"kind": "seller", "staff_user_id": str(staff_user_id)})


def acceptances_of_version(version_id) -> list[ContractAcceptance]:
    return list(db_session.execute(
        select(ContractAcceptance).where(ContractAcceptance.contract_version_id == version_id).order_by(ContractAcceptance.accepted_at)
    ).scalars().all())


# ---------------------------------------------------------------- PDF archive

_PDF_AAD_OWNER = "contract"  # the second AAD component; the first is the version id


def _pdf_root() -> str:
    root = os.path.join(current_app.config["KYC_DIRECTORY"], "contracts")
    os.makedirs(root, exist_ok=True)
    return root


def _store_pdf(version: ContractVersion, content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise ContractError("PDF_REQUIRED")
    if len(content) > documents.MAX_DOCUMENT_BYTES:
        raise ContractError("PDF_TOO_LARGE")
    try:
        kid, kek = documents.active_key()
    except documents.DocumentError:
        raise ContractError("KYC_NOT_CONFIGURED")
    blob = documents.seal(content, document_id=version.id, customer_id=_PDF_AAD_OWNER, kid=kid, kek=kek)
    key = f"{version.id}.bin"
    path = os.path.join(_pdf_root(), key)
    with open(path + ".part", "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(path + ".part", path)
    version.pdf_storage_key = key
    version.pdf_sha256 = hashlib.sha256(content).hexdigest()
    version.pdf_size_bytes = len(content)
    version.pdf_enc_key_id = kid
    return path


def _discard_pdf(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def read_pdf(version: ContractVersion) -> bytes:
    if not version.pdf_storage_key:
        raise ContractError("NO_PDF")
    keys = documents._load_keys()
    kek = keys.get(version.pdf_enc_key_id or "")
    if kek is None:
        raise ContractError("KYC_KEY_UNAVAILABLE")
    root = os.path.realpath(_pdf_root())
    path = os.path.realpath(os.path.join(root, version.pdf_storage_key))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise ContractError("PDF_UNREADABLE")
    with open(path, "rb") as handle:
        blob = handle.read()
    try:
        content = documents.open_blob(blob, document_id=version.id, customer_id=_PDF_AAD_OWNER, kek=kek)
    except documents.DocumentError:
        raise ContractError("PDF_UNREADABLE")
    if hashlib.sha256(content).hexdigest() != version.pdf_sha256:
        raise ContractError("PDF_UNREADABLE")
    return content
