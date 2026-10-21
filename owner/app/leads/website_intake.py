"""Website demo-request intake -- the service half of
POST /api/intake/website-lead (app/leads/intake_routes.py).

The actionaura.me contact form stores every demo request in its own D1
database first, then (Pages Function waitUntil) forwards it here so it shows
up in the CRM. This module:

  * owns the "Website intake" system actor: a StaffUser + EmployeeProfile that
    every website lead is created by (Lead.created_by_employee_profile_id is
    NOT NULL). The account can never log in -- no usable password hash,
    is_active=False, disabled_at set, no role -- and its profile is ARCHIVED,
    so it never appears in employee pickers or assignment lists. Seeded by
    `flask seed-website-intake` (idempotent, same shape as `flask seed-rbac`);
    never created implicitly by a request.
  * validates the website's payload strictly. Both ends of the contract are
    ours, so an unexpected value is a 422 the website records in
    notify_error, not something to guess around.
  * creates the lead through the one real create_lead() (audit, status
    history and idempotency included), keyed "website:<lead_id>", then adds
    the message and business details as one LeadNote.

Replay: the same lead_id returns the existing lead (created=False) and adds
nothing. create_lead() and add_lead_note() commit separately, so a crash
between them leaves a lead without its note, and a replay does not re-add
it. The website forwards each request once, so this is accepted, not handled.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.services import LEAD_CREATION_OPERATION_CODE, add_lead_note, create_lead
from app.leads.validation import validate_lead_fields
from app.models.base import utcnow
from app.models.commercial_sales import CommercialOperationsIdempotencyKey
from app.models.employees import EmployeeProfile
from app.models.leads import Lead, LeadNote
from app.models.staff import StaffUser

WEBSITE_INTAKE_EMAIL = "website-intake@system.invalid"
WEBSITE_INTAKE_DISPLAY_NAME = "Website intake"
WEBSITE_INTAKE_EMPLOYEE_NUMBER = "SYS-WEBSITE-INTAKE"
# Deliberately NOT an Argon2 hash: verify_password() hits InvalidHash and
# returns False for every password, so this account cannot authenticate even
# if someone re-enabled it by hand.
WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH = "!no-login:website-intake"
IDEMPOTENCY_PREFIX = "website:"
# Idempotency keys a staff member may NOT supply themselves. create_lead() looks
# a key up before inserting, so a staff-typed "website:7" would resolve to (and
# silently return) the real website lead #7, or squat on the key so that the
# real request #7 is later answered as a replay of the wrong lead. The two
# staff-facing create entry points (app/leads/routes.py, app/api_operations/
# crm.py) refuse these; only this module mints them.
RESERVED_IDEMPOTENCY_PREFIXES = (IDEMPOTENCY_PREFIX,)


def is_reserved_idempotency_key(key) -> bool:
    return isinstance(key, str) and key.strip().lower().startswith(RESERVED_IDEMPOTENCY_PREFIXES)


BUSINESS_TYPES = ("", "shop", "clinic", "other")
BRANCHES = ("", "1", "2-3", "more")
LANGS = ("ar", "en")

_LEAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Invisible characters nobody types on purpose into a name or message: format
# characters (Cf: bidi overrides such as U+202E, zero-width joiners), line and
# paragraph separators (Zl/Zp: U+2028/9) and lone surrogates (Cs: legal in a
# JSON "\ud800" escape but not encodable as UTF-8, so one reaching the database
# is a 500, not a 422). Cc (control characters) become one space, except "\n"
# in the message field.
_DROPPED_CATEGORIES = frozenset({"Cf", "Zl", "Zp", "Cs"})
# What a phone number may contain after cleaning. A form that lets letters
# through is not sending a phone number, so that is a 422, not a guess.
_PHONE_RE = re.compile(r"^[0-9+() -]+$")
_MAX_NAME_LEN = 200
_MAX_PHONE_LEN = 32
_MAX_BUSINESS_NAME_LEN = 200
_MAX_MESSAGE_LEN = 2000


class WebsiteIntakeError(Exception):
    def __init__(self, code: str, http_status: int, field: str | None = None):
        self.code = code
        self.http_status = http_status
        self.field = field
        super().__init__(code if field is None else f"{code}:{field}")


@dataclass(frozen=True)
class WebsiteLeadPayload:
    lead_id: str
    name: str
    phone: str
    business_type: str
    business_name: str
    branches: str
    message: str
    lang: str


def _text(body: dict, key: str) -> str:
    value = body.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WebsiteIntakeError("INVALID_FIELD", 422, key)
    return value


def _clean(value: str, *, keep_newline: bool = False) -> str:
    out: list[str] = []
    last_was_control = False
    for ch in value:
        category = unicodedata.category(ch)
        if keep_newline and ch == "\n":
            out.append(ch)
            last_was_control = False
        elif category == "Cc":
            if not last_was_control:  # a run of controls is one space
                out.append(" ")
            last_was_control = True
        elif category in _DROPPED_CATEGORIES:
            continue
        else:
            out.append(ch)
            last_was_control = False
    return "".join(out)


def _single_line(body: dict, key: str) -> str:
    return _clean(_text(body, key)).strip()


def parse_website_lead_payload(body: dict) -> WebsiteLeadPayload:
    """Strict and all-or-nothing. Unknown keys are ignored (never stored)."""
    raw_id = body.get("lead_id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
        raise WebsiteIntakeError("INVALID_FIELD", 422, "lead_id")
    lead_id = str(raw_id).strip()
    if not _LEAD_ID_RE.match(lead_id):
        raise WebsiteIntakeError("INVALID_FIELD", 422, "lead_id")
    name = _single_line(body, "name")
    if not name or len(name) > _MAX_NAME_LEN:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "name")
    phone = _single_line(body, "phone")
    if not phone or len(phone) > _MAX_PHONE_LEN or not _PHONE_RE.match(phone):
        raise WebsiteIntakeError("INVALID_FIELD", 422, "phone")
    business_type = _single_line(body, "business_type")
    if business_type not in BUSINESS_TYPES:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "business_type")
    business_name = _single_line(body, "business_name")
    if len(business_name) > _MAX_BUSINESS_NAME_LEN:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "business_name")
    branches = _single_line(body, "branches")
    if branches not in BRANCHES:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "branches")
    message = _clean(_text(body, "message").replace("\r\n", "\n").replace("\r", "\n"), keep_newline=True).strip()
    if len(message) > _MAX_MESSAGE_LEN:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "message")
    lang = _single_line(body, "lang") or "en"
    if lang not in LANGS:
        raise WebsiteIntakeError("INVALID_FIELD", 422, "lang")
    return WebsiteLeadPayload(lead_id, name, phone, business_type, business_name, branches, message, lang)


def get_website_intake_actor() -> tuple[uuid.UUID, uuid.UUID] | None:
    """(staff_user_id, employee_profile_id), or None if never seeded."""
    row = db_session.execute(
        select(StaffUser.id, EmployeeProfile.id)
        .join(EmployeeProfile, EmployeeProfile.staff_user_id == StaffUser.id)
        .where(StaffUser.email == WEBSITE_INTAKE_EMAIL)
    ).first()
    return (row[0], row[1]) if row is not None else None


def seed_website_intake_actor() -> dict:
    """Idempotent. Creates whatever is missing. On an existing account it
    re-asserts the cannot-log-in invariants, so a hand edit can never leave a
    loginable system account behind."""
    now = utcnow()
    staff = db_session.execute(select(StaffUser).where(StaffUser.email == WEBSITE_INTAKE_EMAIL)).scalars().first()
    created_staff = staff is None
    if staff is None:
        staff = StaffUser(
            email=WEBSITE_INTAKE_EMAIL,
            display_name=WEBSITE_INTAKE_DISPLAY_NAME,
            password_hash=WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH,
            is_active=False,
            is_super_admin=False,
            mfa_required=False,
            disabled_at=now,
            disabled_reason="System account: creates leads from website demo requests. Never logs in.",
        )
        db_session.add(staff)
        db_session.flush()
    repaired = False
    if (
        staff.is_active or staff.is_super_admin or staff.disabled_at is None
        or staff.password_hash != WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH
    ):
        staff.is_active = False
        staff.is_super_admin = False
        staff.disabled_at = staff.disabled_at or now
        staff.password_hash = WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH
        staff.session_version += 1  # kills any session that could exist
        repaired = True

    profile = db_session.execute(
        select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff.id)
    ).scalars().first()
    created_profile = profile is None
    if profile is None:
        profile = EmployeeProfile(
            staff_user_id=staff.id,
            employee_number=WEBSITE_INTAKE_EMPLOYEE_NUMBER,
            full_name=WEBSITE_INTAKE_DISPLAY_NAME,
            job_title="System",
            department="Website",
            employment_start_date=date.today(),
            employment_status="ARCHIVED",
            archived_at=now,
            notes="System actor for website demo-request intake (app/leads/website_intake.py).",
            created_by_staff_user_id=staff.id,
            updated_by_staff_user_id=staff.id,
        )
        db_session.add(profile)
    db_session.commit()
    if created_staff or created_profile or repaired:
        audit_record(
            actor_staff_user_id=staff.id,
            actor_role_snapshot=None,
            action_code="WEBSITE_INTAKE_ACTOR_SEEDED",
            entity_type="staff_user",
            entity_public_id=str(staff.id),
            after_state={"created_staff": created_staff, "created_profile": created_profile, "repaired": repaired},
        )
    return {
        "staff_user_id": str(staff.id),
        "employee_profile_id": str(profile.id),
        "created": created_staff or created_profile,
        "repaired": repaired,
    }


def _lead_for_key(idempotency_key: str) -> Lead | None:
    key_row = db_session.execute(
        select(CommercialOperationsIdempotencyKey).where(
            CommercialOperationsIdempotencyKey.idempotency_key == idempotency_key,
            CommercialOperationsIdempotencyKey.operation_code == LEAD_CREATION_OPERATION_CODE,
        )
    ).scalars().first()
    return db_session.get(Lead, key_row.result_reference_id) if key_row is not None else None


def _note_body(payload: WebsiteLeadPayload) -> str:
    lines = [
        f"Website demo request #{payload.lead_id}",
        f"Contact: {payload.name}",
        f"Phone: {payload.phone}",
    ]
    if payload.business_type:
        lines.append(f"Business type: {payload.business_type}")
    if payload.business_name:
        lines.append(f"Business name: {payload.business_name}")
    if payload.branches:
        lines.append(f"Branches: {payload.branches}")
    lines.append(f"Language: {payload.lang}")
    if payload.message:
        lines += ["", "Message:", payload.message]
    return "\n".join(lines)


def ingest_website_lead(payload: WebsiteLeadPayload) -> tuple[Lead, bool]:
    """Returns (lead, created). Raises WebsiteIntakeError (503 if never
    seeded) or LeadError (validation)."""
    actor = get_website_intake_actor()
    if actor is None:
        raise WebsiteIntakeError("INTAKE_NOT_SEEDED", 503)
    staff_user_id, profile_id = actor
    idempotency_key = IDEMPOTENCY_PREFIX + payload.lead_id
    existing = _lead_for_key(idempotency_key)
    if existing is not None:
        return existing, False
    fields = validate_lead_fields(
        {
            "organization_or_prospect_name": payload.business_name or payload.name,
            "primary_contact_name": payload.name,
            "phone": payload.phone,
            "source": "WEBSITE",
        }
    )
    try:
        lead = create_lead(fields, profile_id, staff_user_id, idempotency_key=idempotency_key)
    except IntegrityError:
        # Two concurrent deliveries of the same lead_id: the unique
        # idempotency constraint let exactly one win. Answer as a replay.
        db_session.rollback()
        existing = _lead_for_key(idempotency_key)
        if existing is None:
            raise
        return existing, False
    # create_lead() and add_lead_note() commit separately. Two deliveries of one
    # lead_id can both get past the idempotency lookup above and then both reach
    # this line (the loser's IntegrityError only fires inside create_lead for the
    # key row, and a timed-out first delivery plus the website's retry can
    # interleave after it) -- which used to add the note twice. Checked right
    # before the add so the window is as small as it can be; "authored by the
    # intake actor" because no staff member can write as that account.
    already_noted = db_session.execute(
        select(LeadNote.id).where(LeadNote.lead_id == lead.id, LeadNote.author_employee_profile_id == profile_id).limit(1)
    ).first()
    if already_noted is None:
        add_lead_note(lead, _note_body(payload), profile_id, staff_user_id)
    return lead, True
