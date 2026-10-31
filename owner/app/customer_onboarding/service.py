"""The seller's one action: "Create a new customer" (new customer flow, slice S2).

One form, one press, and afterwards the customer exists with everything the
owner's flow needs:

  Customer (assigned to the seller) + primary contact (E.164 phone) + address +
  captured location + a website ACTIVE account whose password is the TEMPORARY
  one the seller typed (the customer must change it at first sign-in) + their ID
  photo and commercial-registration photo (encrypted, documents.py) + a
  Subscription in PILOT status with a PilotRecord of kind TRIAL (no dates until the
  first device activates -- trial.py) + a licence in ISSUED state whose key is
  shown ONCE to the seller (and as a ready WhatsApp text, as a backup path; the
  main path is "sign in with your email and password" -- the key never has to be
  typed).

EXACTLY ONCE -- the direct_sale.py / issuance.py pattern, for the same reason
(every service called here commits on its own, so this cannot be one outer
transaction):
  1. every check runs BEFORE the first write -- a refused form writes nothing, and
     "nothing" includes files: the photos are checked (type, size) and the KYC key
     is checked before the customer row exists;
  2. a per-key advisory lock on a DEDICATED connection serialises two presses of the
     same form (a lock on db_session would be released by the first nested commit);
  3. the ids are derived from the form's key (uuid5), so a retry after a crash
     part-way finds and reuses what the failed attempt committed, and a replay of a
     finished form returns the original rows (no second customer, no second key).
     A replay with different details is refused, never resolved to either.

DUPLICATES: a shop gets ONE trial. Before any write the CR number, the phone (as
E.164) and the email are compared with every existing customer; a match refuses with
TRIAL_ALREADY_USED unless a Sales Manager / Super Admin (trials.override_duplicate)
presses through with a written reason, which is audited with what matched. An
already-used sign-in email is refused even with an override (it is unique).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta

from flask import current_app
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError

from app.audit.services import record as audit_record
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale, open_to_sellers
from app.commercial_ops.pilot_lifecycle import approve_pilot, activate_pilot
from app.customer_accounts import policy, service as account_service
from app.customer_onboarding import documents, trial
from app.customers.services import add_contact, create_customer
from app.employees.queries import find_own_profile
from app.extensions import db_session, get_engine
from app.leads.errors import LocationValidationError
from app.leads.location import validate_location_fields
from app.leads.services import capture_location
from app.licensing.issuance import compose_activation_whatsapp_message
from app.licensing.services import create_license, issue_license_key
from app.models.base import utcnow
from app.models.catalog import Plan, Product
from app.models.commercial_ops import PilotRecord
from app.models.customer_accounts import CustomerAccount
from app.models.customers import Customer, CustomerAddress, CustomerContact
from app.models.leads import CustomerLocation
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.operational_reports.periods import jordan_today
from app.subscriptions.services import create_subscription, transition_subscription

OVERRIDE_PERMISSION = "trials.override_duplicate"
MIN_OVERRIDE_REASON_LENGTH = 10
DEFAULT_PRODUCT_CODE = "AURA_RETAIL"
CLINIC_PRODUCT_CODE = "AURA_CLINIC"
_LOCK_NAMESPACE = "customer_onboarding.create_trial_customer"
_ROW_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:aura-owner:customer_onboarding:create_trial_customer")
_PILOT_ROW_KIND = "pilot"

# English, request-context-free, keyed by a stable code (direct_sale.py pattern):
# str(exc) is this text; the screens show i18n_labels.localize_onboarding_error.
# Keep the two tables in step (test_every_onboarding_message_is_translated).
ONBOARDING_MESSAGES = {
    "FEATURE_OFF": "Creating a new customer is not switched on here.",
    "KEY_REQUIRED": "An idempotency key is required to create a customer.",
    "OWN_PROFILE_REQUIRED": "Creating a customer needs your own employee profile. Ask a manager to add one.",
    "SELLER_NOT_ACTIVE": "Only an active employee can create a customer.",
    "KYC_NOT_READY": "Customer documents cannot be stored on this server yet (no encryption key is set). Nothing was created. Ask the administrator.",
    "CONSENT_REQUIRED": "Tick the box to confirm the customer agreed to the storing of their ID and registration documents.",
    "BUSINESS_NAME_REQUIRED": "Type the shop's (business) name.",
    "NAME_TOO_LONG": "A name is too long (at most %(max)s characters).",
    "EMAIL_INVALID": "Type a valid email address for the customer.",
    "EMAIL_IN_USE": "This email address is already used by another customer's account.",
    "PASSWORD_REQUIRED": "Type a temporary password for the customer.",
    "PASSWORD_TOO_SHORT": "The temporary password must be at least 10 characters.",
    "PASSWORD_TOO_LONG": "The temporary password is too long.",
    "PASSWORD_TOO_COMMON": "That password is too common. Pick another temporary password.",
    "PASSWORD_CONTAINS_PERSONAL": "The password must not contain the customer's email or shop name.",
    "PHONE_INVALID": "Type a valid phone number, like 079 123 4567.",
    "CR_NUMBER_REQUIRED": "Type the commercial registration number.",
    "CR_NUMBER_TOO_LONG": "The registration number is too long (at most %(max)s characters).",
    "ADDRESS_REQUIRED": "Type where the shop is (address or area).",
    "LOCATION_INVALID": "The captured location is not valid. Capture it again or leave it empty.",
    "ID_PHOTO_REQUIRED": "Add a photo of the owner's ID.",
    "CR_PHOTO_REQUIRED": "Add a photo of the commercial registration.",
    "DOCUMENT_TYPE_NOT_ALLOWED": "Photos must be JPEG or PNG pictures (or a PDF).",
    "DOCUMENT_TOO_LARGE": "A photo is too large (at most %(max_mb)s MB).",
    "DOCUMENT_EMPTY": "A photo file is empty.",
    "DOCUMENT_CORRUPT": "A photo file is damaged. Take it again.",
    "PRODUCT_NOT_AVAILABLE": "This product is not available for new customers yet.",
    "PACKAGE_NOT_FOUND": "Selected package was not found.",
    "PACKAGE_WRONG_PRODUCT": "That package belongs to a different product.",
    "PACKAGE_NOT_SELLABLE": "Selected package is not currently sellable (check its effective date and price).",
    "PACKAGE_NO_PLATFORMS": "Selected package has no supported platforms configured.",
    "TRIAL_ALREADY_USED": "A shop with this %(what)s already exists, and a shop gets only one trial. Ask a Sales Manager.",
    "OVERRIDE_NOT_ALLOWED": "Only a Sales Manager or Super Admin may create a second trial for the same shop.",
    "OVERRIDE_REASON_REQUIRED": "Write why this shop should get another trial (at least %(min)s characters).",
    "KEY_USED_FOR_DIFFERENT_FORM": "This form was already used to create a different customer. Open a fresh form to create another one.",
    "ONBOARDING_IN_PROGRESS": "This customer is still being created from an earlier click. Wait a moment, then check the customer list.",
}

DUPLICATE_FIELD_CR = "cr"
DUPLICATE_FIELD_PHONE = "phone"
DUPLICATE_FIELD_EMAIL = "email"

_NAME_MAX = 255
_CR_MAX = 128
_ADDRESS_MAX = 500


class OnboardingError(ValueError):
    """A refusal with a stable code (ONBOARDING_MESSAGES); str() is the English text."""

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        template = ONBOARDING_MESSAGES[code]
        super().__init__(template % params if params else template)


@dataclass(frozen=True)
class OnboardingResult:
    customer_id: uuid.UUID
    account_id: uuid.UUID
    subscription_id: uuid.UUID
    license_id: uuid.UUID
    license_key: str | None  # shown ONCE; None on a replay
    whatsapp_message: str | None
    verify_link: str | None  # shown ONCE, only when the welcome mail was not sent
    emailed: bool
    replayed: bool


# ---------------------------------------------------------------- helpers

def _row_id_for(key: str, kind: str) -> uuid.UUID:
    return uuid.uuid5(_ROW_ID_NAMESPACE, f"{kind}|{key}")


def _lock_key(key: str) -> int:
    return int(hashlib.sha256(f"{_LOCK_NAMESPACE}|{key}".encode("utf-8")).hexdigest()[:15], 16)


@contextmanager
def _hold_lock(key: str):
    """Per-form mutual exclusion that survives the commits inside the action
    (direct_sale._hold_advisory_lock's mechanism, see its docstring)."""
    conn = get_engine().connect()
    try:
        txn = conn.begin()
        try:
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _lock_key(key)})
        except OperationalError as exc:
            txn.rollback()
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03":
                raise OnboardingError("ONBOARDING_IN_PROGRESS") from exc
            raise
        try:
            yield
        finally:
            txn.rollback()
    finally:
        conn.close()


# Countries whose numbers are routinely typed WITH the national trunk 0 after the
# country code ("+962 0791..."); E.164 never carries it. Not applied to every code
# on purpose: Italy keeps its 0 (+39 06...), so stripping blindly would corrupt it.
_TRUNK_ZERO_COUNTRY_CODES = ("962", "963", "964", "966", "970", "971", "961", "965", "968", "974", "973", "20", "44")


def normalize_phone_e164(raw: str | None, default_country_code: str = "962") -> str:
    """'079 123 4567' / '+962 79 123 4567' / '00962791234567' -> '+962791234567'.
    A number with a plus or 00 prefix is taken as typed; a number starting 0 is a
    local Jordan number; a bare 9-digit number starting 7 is a Jordan mobile.
    Digits typed in any script (Arabic-Indic, Persian) are read as ASCII, and a
    trunk 0 typed after the country code ('+962 0791...') is dropped, so the same
    phone is always the same string. Anything else, or fewer than 8 / more than 15
    digits, is refused."""
    text_in = _ascii_digits(raw).strip()
    digits = re.sub(r"\D", "", text_in)
    if not digits:
        raise OnboardingError("PHONE_INVALID")
    if text_in.startswith("+"):
        e164_digits = digits
    elif digits.startswith("00"):
        e164_digits = digits[2:]
    elif digits.startswith("0"):
        e164_digits = default_country_code + digits[1:]
    elif len(digits) == 9 and digits.startswith("7"):
        e164_digits = default_country_code + digits
    else:
        raise OnboardingError("PHONE_INVALID")
    for code in _TRUNK_ZERO_COUNTRY_CODES:
        if e164_digits.startswith(code + "0"):
            e164_digits = code + e164_digits[len(code) + 1:]
            break
    if not 8 <= len(e164_digits) <= 15 or e164_digits.startswith("0"):
        raise OnboardingError("PHONE_INVALID")
    return "+" + e164_digits


def _phone_key(raw: str | None) -> str | None:
    try:
        return normalize_phone_e164(raw)
    except OnboardingError:
        digits = re.sub(r"\D", "", _ascii_digits(raw))
        return digits or None


def _ascii_digits(raw: str | None) -> str:
    """Every Unicode decimal digit -> its ASCII digit (Arabic-Indic '١٢٣' -> '123').
    NFKC does NOT do this: it leaves Arabic-Indic digits unchanged, so a seller on an
    Arabic keyboard (normal here) typed a number that no ASCII-only comparison could
    ever match. unicodedata.decimal is the mapping that actually works."""
    return "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in (raw or ""))


# Same mapping for the SQL side, so rows stored BEFORE the ASCII rule (typed with
# Arabic-Indic U+0660.. or Extended U+06F0.. digits) still match: Postgres translate().
_NON_ASCII_DIGITS = "".join(chr(base + i) for base in (0x0660, 0x06F0) for i in range(10))
_ASCII_DIGITS_FOR_THEM = "0123456789" * 2


def _cr_key(raw: str | None) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", _ascii_digits(raw)).casefold()


def _clean_name(value: str | None, *, required_code: str | None = None, limit: int = _NAME_MAX) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned and required_code:
        raise OnboardingError(required_code)
    if len(cleaned) > limit:
        raise OnboardingError("NAME_TOO_LONG", max=limit)
    return cleaned


def find_duplicates(*, cr_number: str, phone_e164: str, email: str) -> dict:
    """{'fields': [...], 'customer_ids': [...]} -- what the new shop clashes with.
    Read-only. A customer counts whether archived or not (a trial once used stays
    used). The refusal that uses this never names the other customer."""
    fields: list[str] = []
    ids: set = set()
    cr_norm = _cr_key(cr_number)
    if cr_norm:
        rows = db_session.execute(
            select(Customer.id).where(
                Customer.commercial_registration_reference.is_not(None),
                func.lower(func.regexp_replace(
                    func.translate(Customer.commercial_registration_reference, _NON_ASCII_DIGITS, _ASCII_DIGITS_FOR_THEM),
                    "[^0-9A-Za-z]", "", "g",
                )) == cr_norm,
            )
        ).scalars().all()
        if rows:
            fields.append(DUPLICATE_FIELD_CR)
            ids.update(rows)
    phone_hits = [
        contact.customer_id for contact in db_session.execute(
            select(CustomerContact).where(CustomerContact.business_phone.is_not(None))
        ).scalars()
        if _phone_key(contact.business_phone) == phone_e164
    ]
    if phone_hits:
        fields.append(DUPLICATE_FIELD_PHONE)
        ids.update(phone_hits)
    email_hits = list(db_session.execute(
        select(CustomerAccount.customer_id).where(CustomerAccount.email == email)
    ).scalars().all()) + list(db_session.execute(
        select(CustomerContact.customer_id).where(func.lower(CustomerContact.business_email) == email)
    ).scalars().all())
    if email_hits:
        fields.append(DUPLICATE_FIELD_EMAIL)
        ids.update(email_hits)
    return {"fields": fields, "customer_ids": sorted(ids, key=str)}


def _password_error_code(exc: policy.PasswordPolicyError) -> str:
    return {
        "too_short": "PASSWORD_TOO_SHORT", "too_long": "PASSWORD_TOO_LONG",
        "too_common": "PASSWORD_TOO_COMMON", "contains_personal": "PASSWORD_CONTAINS_PERSONAL",
    }.get(exc.code, "PASSWORD_TOO_COMMON")


def _check_location(latitude, longitude, accuracy) -> dict:
    fields = {"latitude": latitude or None, "longitude": longitude or None, "accuracy_meters": accuracy or None, "source": "GPS"}
    if fields["latitude"] is None and fields["longitude"] is None:
        return {}
    if fields["latitude"] is None or fields["longitude"] is None:
        raise OnboardingError("LOCATION_INVALID")
    try:
        cleaned = validate_location_fields(fields, now=utcnow())
    except LocationValidationError as exc:
        raise OnboardingError("LOCATION_INVALID") from exc
    return {k: cleaned.get(k) for k in ("latitude", "longitude", "accuracy_meters")}


def _resolve_package(plan_id, product_code: str) -> tuple[Plan, dict]:
    if product_code != DEFAULT_PRODUCT_CODE and not (
        product_code == CLINIC_PRODUCT_CODE and current_app.config.get("CLINIC_ACCOUNTS_ENABLED")
    ):
        raise OnboardingError("PRODUCT_NOT_AVAILABLE")
    try:
        plan = db_session.get(Plan, uuid.UUID(str(plan_id))) if plan_id else None
    except ValueError:
        plan = None
    if plan is None:
        raise OnboardingError("PACKAGE_NOT_FOUND")
    product = db_session.get(Product, plan.product_id)
    if product is None or product.product_code != product_code:
        raise OnboardingError("PACKAGE_WRONG_PRODUCT")
    item = describe_plan_for_sale(plan.id)
    if item is None:
        raise OnboardingError("PACKAGE_NOT_SELLABLE")
    if not item["supported_platform_codes"]:
        raise OnboardingError("PACKAGE_NO_PLATFORMS")
    return plan, item


def sellable_packages(product_code: str = DEFAULT_PRODUCT_CODE) -> list[dict]:
    """The packages the form offers: sellable plans of the product (P1/P2/P3)."""
    plans = db_session.execute(
        select(Plan).join(Product, Product.id == Plan.product_id).where(Product.product_code == product_code).order_by(Plan.name)
    ).scalars().all()
    return [item for item in (describe_plan_for_sale(plan.id) for plan in plans) if item is not None and open_to_sellers(item)]


# ------------------------------------------------------------- the action

def create_trial_customer(
    *,
    idempotency_key: str,
    business_name: str,
    contact_name: str | None,
    email: str,
    temp_password: str,
    phone: str,
    address: str,
    city: str | None = None,
    latitude=None,
    longitude=None,
    accuracy_meters=None,
    cr_number: str,
    plan_id,
    product_code: str = DEFAULT_PRODUCT_CODE,
    language: str = "ar",
    consent: bool = False,
    id_photo: bytes = b"",
    cr_photo: bytes = b"",
    override_duplicate: bool = False,
    override_reason: str | None = None,
    actor_staff_user_id: uuid.UUID,
    actor_permission_codes: set[str],
    license_pepper: str,
    as_of: date | None = None,
) -> OnboardingResult:
    if not current_app.config.get("CUSTOMER_ONBOARDING_ENABLED"):
        raise OnboardingError("FEATURE_OFF")
    if not idempotency_key:
        raise OnboardingError("KEY_REQUIRED")
    try:
        email_norm = account_service.normalize_email(email)
    except account_service.AccountError:
        raise OnboardingError("EMAIL_INVALID")
    # TWO locks, always in this order: the form's own (a double press) and the sign-in
    # address's (two DIFFERENT forms racing on one email -- the loser would otherwise
    # have committed a customer, its photos and a trial before the account's unique
    # constraint refused it, leaving an orphan customer without an account).
    with _hold_lock(idempotency_key), _hold_lock("email|" + email_norm):
        # -- read-only until every check has passed --------------------------
        profile = find_own_profile(actor_staff_user_id)
        if profile is None:
            raise OnboardingError("OWN_PROFILE_REQUIRED")
        if profile.employment_status != "ACTIVE" or profile.archived_at is not None:
            raise OnboardingError("SELLER_NOT_ACTIVE")
        if not consent:
            raise OnboardingError("CONSENT_REQUIRED")
        legal_name = _clean_name(business_name, required_code="BUSINESS_NAME_REQUIRED")
        contact = _clean_name(contact_name) or legal_name
        if not (temp_password or "").strip():
            raise OnboardingError("PASSWORD_REQUIRED")
        try:
            policy.validate(temp_password, email=email_norm, legal_name=legal_name)
        except policy.PasswordPolicyError as exc:
            raise OnboardingError(_password_error_code(exc))
        phone_e164 = normalize_phone_e164(phone)
        cr_clean = _ascii_digits(_clean_name(cr_number, required_code="CR_NUMBER_REQUIRED", limit=_CR_MAX))
        if not _cr_key(cr_clean):
            # "---" / only symbols: an empty key would make find_duplicates skip the CR
            # check entirely, i.e. a number that can never clash with anything.
            raise OnboardingError("CR_NUMBER_REQUIRED")
        address_clean = _clean_name(address, required_code="ADDRESS_REQUIRED", limit=_ADDRESS_MAX)
        city_clean = _clean_name(city, limit=128) or None
        location_fields = _check_location(latitude, longitude, accuracy_meters)
        language = language if language in account_service.LANGUAGES else "ar"
        if not id_photo:
            raise OnboardingError("ID_PHOTO_REQUIRED")
        if not cr_photo:
            raise OnboardingError("CR_PHOTO_REQUIRED")
        try:
            documents.clean_file(id_photo)
            documents.clean_file(cr_photo)
        except documents.DocumentError as exc:
            raise OnboardingError(exc.code, **exc.params)
        if not documents.storage_is_ready():
            raise OnboardingError("KYC_NOT_READY")
        plan, plan_item = _resolve_package(plan_id, product_code)

        customer_id = _row_id_for(idempotency_key, "customer")
        existing = db_session.get(Customer, customer_id)
        if existing is not None:
            replay = _resume_or_replay(existing, idempotency_key, email_norm=email_norm, legal_name=legal_name, plan=plan)
            if replay is not None:
                return replay
        else:
            _check_duplicates(
                cr_number=cr_clean, phone_e164=phone_e164, email=email_norm, override=override_duplicate,
                reason=override_reason, codes=actor_permission_codes,
            )
            if account_service.get_account_by_email(email_norm) is not None:
                raise OnboardingError("EMAIL_IN_USE")

        # -- writes start here: each step reuses what an interrupted attempt left --
        today = as_of or jordan_today()
        customer = existing or create_customer(
            {
                "id": customer_id, "legal_name": legal_name, "commercial_registration_reference": cr_clean,
                "country": "JO", "city": city_clean, "primary_language": language,
                "assigned_sales_staff_id": actor_staff_user_id, "acquisition_source": "SELLER_ONBOARDING",
            },
            actor_staff_user_id,
        )
        _ensure_contact_and_address(customer, contact=contact, email=email_norm, phone=phone_e164,
                                    address=address_clean, city=city_clean, actor_staff_user_id=actor_staff_user_id)
        _ensure_location(customer, location_fields, address_clean, profile.id, actor_staff_user_id)
        _ensure_documents(customer.id, id_photo, cr_photo, actor_staff_user_id)

        account = account_service.get_account_for_customer(customer.id)
        verify_link = None
        emailed = False
        if account is None:
            try:
                account, issued = account_service.create_account_with_temp_password(
                    customer, email_norm, language, temp_password, actor_staff_user_id
                )
            except account_service.AccountError as exc:
                if exc.code == "email_in_use":
                    raise OnboardingError("EMAIL_IN_USE")
                raise
            if issued is not None:
                emailed = issued.emailed
                verify_link = None if issued.emailed else issued.link

        subscription = _ensure_trial_subscription(customer, plan, idempotency_key, actor_staff_user_id)
        _ensure_trial_pilot(subscription, plan, idempotency_key, today, actor_staff_user_id)
        license_row, full_key = _ensure_issued_license(
            customer, subscription, plan, plan_item, idempotency_key, license_pepper, actor_staff_user_id
        )
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_ONBOARDED",
            entity_type="customer", entity_public_id=str(customer.id), reason=(override_reason or None) if override_duplicate else None,
            after_state={
                "plan_id": str(plan.id), "subscription_id": str(subscription.id), "license_id": str(license_row.id),
                "account_id": str(account.id), "duplicate_override": bool(override_duplicate),
            },
        )

    whatsapp = None
    if full_key is not None:
        whatsapp = compose_activation_whatsapp_message(customer_name=customer.legal_name, product_name=plan_item["name"], full_key=full_key)
    return OnboardingResult(
        customer_id=customer.id, account_id=account.id, subscription_id=subscription.id, license_id=license_row.id,
        license_key=full_key, whatsapp_message=whatsapp, verify_link=verify_link, emailed=emailed, replayed=False,
    )


def _check_duplicates(*, cr_number, phone_e164, email, override, reason, codes) -> None:
    found = find_duplicates(cr_number=cr_number, phone_e164=phone_e164, email=email)
    if not found["fields"]:
        return
    if DUPLICATE_FIELD_EMAIL in found["fields"] and db_session.execute(
        select(CustomerAccount.id).where(CustomerAccount.email == email)
    ).first() is not None:
        raise OnboardingError("EMAIL_IN_USE")  # unique: no override can make a second account on one address
    if not override:
        raise OnboardingError("TRIAL_ALREADY_USED", what=",".join(found["fields"]), fields=tuple(found["fields"]))
    if OVERRIDE_PERMISSION not in codes:
        raise OnboardingError("OVERRIDE_NOT_ALLOWED")
    if len((reason or "").strip()) < MIN_OVERRIDE_REASON_LENGTH:
        raise OnboardingError("OVERRIDE_REASON_REQUIRED", min=MIN_OVERRIDE_REASON_LENGTH)


def _resume_or_replay(customer: Customer, key: str, *, email_norm, legal_name, plan) -> OnboardingResult | None:
    """A customer already exists under this form key. Same details -> either a
    finished action (return its rows, no key) or a half-finished one (None: the
    caller resumes it). Different details -> refused."""
    account = account_service.get_account_for_customer(customer.id)
    subscription = db_session.get(Subscription, _row_id_for(key, "subscription"))
    same = (
        customer.legal_name == legal_name
        and (account is None or account.email == email_norm)
        and (subscription is None or subscription.plan_id == plan.id)
    )
    if not same:
        raise OnboardingError("KEY_USED_FOR_DIFFERENT_FORM")
    license_row = db_session.get(License, _row_id_for(key, "license"))
    if license_row is not None and license_row.status != "DRAFT" and account is not None and subscription is not None:
        return OnboardingResult(
            customer_id=customer.id, account_id=account.id, subscription_id=subscription.id, license_id=license_row.id,
            license_key=None, whatsapp_message=None, verify_link=None, emailed=False, replayed=True,
        )
    return None


def _ensure_contact_and_address(customer, *, contact, email, phone, address, city, actor_staff_user_id) -> None:
    has_contact = db_session.execute(
        select(CustomerContact.id).where(CustomerContact.customer_id == customer.id)
    ).first() is not None
    if not has_contact:
        add_contact(customer, {"name": contact, "business_email": email, "business_phone": phone,
                               "is_primary": True, "preferred_channel": "WHATSAPP"}, actor_staff_user_id)
    has_address = db_session.execute(
        select(CustomerAddress.id).where(CustomerAddress.customer_id == customer.id)
    ).first() is not None
    if not has_address:
        db_session.add(CustomerAddress(customer_id=customer.id, line1=address, city=city, country="JO", is_primary=True))
        db_session.commit()


def _ensure_location(customer, location_fields, address, profile_id, actor_staff_user_id) -> None:
    if db_session.execute(select(CustomerLocation.id).where(CustomerLocation.customer_id == customer.id)).first() is not None:
        return
    fields = {"latitude": None, "longitude": None, "accuracy_meters": None, "manual_address": address,
              "source": "GPS" if location_fields else "MANUAL", **location_fields}
    capture_location(
        lead_id=None, customer_id=customer.id, fields=fields,
        actor_employee_profile_id=profile_id, actor_staff_user_id=actor_staff_user_id,
    )


def _ensure_documents(customer_id, id_photo: bytes, cr_photo: bytes, actor_staff_user_id) -> None:
    have = {d.kind for d in documents.documents_of(customer_id, kinds=("NATIONAL_ID", "COMMERCIAL_REGISTRATION"))}
    stored = []
    try:
        for kind, content in (("NATIONAL_ID", id_photo), ("COMMERCIAL_REGISTRATION", cr_photo)):
            if kind not in have:
                stored.append(documents.store_document(
                    customer_id=customer_id, kind=kind, content=content, uploaded_by_staff_user_id=actor_staff_user_id))
        db_session.commit()
    except Exception:
        db_session.rollback()
        for document in stored:
            documents.discard_blob(document)
        raise
    for document in stored:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_DOCUMENT_STORED",
            entity_type="customer_document", entity_public_id=str(document.id),
            after_state={"kind": document.kind, "customer_id": str(customer_id), "size_bytes": document.size_bytes},
        )


def _ensure_trial_subscription(customer, plan, key, actor_staff_user_id) -> Subscription:
    subscription = db_session.get(Subscription, _row_id_for(key, "subscription"))
    if subscription is None:
        subscription = create_subscription(
            {
                "id": _row_id_for(key, "subscription"), "customer_id": customer.id, "product_id": plan.product_id,
                "plan_id": plan.id, "billing_cycle": plan.billing_model, "start_date": None, "end_date": None,
                "device_allowance": plan.included_device_count, "sales_owner_staff_user_id": actor_staff_user_id,
            },
            actor_staff_user_id,
        )
    if subscription.status == "DRAFT":
        transition_subscription(subscription, "PILOT", actor_staff_user_id, reason="7-day trial created with the customer.")
    return subscription


def _ensure_trial_pilot(subscription, plan, key, today: date, actor_staff_user_id) -> PilotRecord:
    pilot = db_session.get(PilotRecord, _row_id_for(key, _PILOT_ROW_KIND))
    if pilot is None:
        # Provisional dates only: the real clock starts at the first device
        # activation (trial.start_trial_on_activation), or at the latest 14 days
        # from now (trial.start_overdue_trials). pilot_end must be after
        # pilot_start, so it holds the latest the trial could possibly end.
        pilot = PilotRecord(
            id=_row_id_for(key, _PILOT_ROW_KIND), subscription_id=subscription.id, customer_id=subscription.customer_id,
            product_id=subscription.product_id, pilot_start=today,
            pilot_end=today + timedelta(days=trial.FALLBACK_START_AFTER_DAYS + trial.TRIAL_LENGTH_DAYS),
            allowed_device_count=plan.included_device_count, support_level=plan.support_level,
            sales_owner_staff_user_id=actor_staff_user_id, max_extensions_allowed=trial.MAX_TRIAL_EXTENSIONS,
            created_by_staff_user_id=actor_staff_user_id, status="DRAFT", pilot_kind="TRIAL",
            trial_length_days=trial.TRIAL_LENGTH_DAYS,
            agreed_limitations="Free 7-day trial. After it ends the till stops selling until the package is paid; the shop's data stays.",
        )
        db_session.add(pilot)
        db_session.commit()
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="PILOT_CREATED",
            entity_type="pilot_record", entity_public_id=str(pilot.id),
            after_state={"status": "DRAFT", "subscription_id": str(subscription.id), "kind": "TRIAL"},
        )
    if pilot.status == "DRAFT":
        approve_pilot(pilot, actor_staff_user_id)
    if pilot.status == "APPROVED":
        activate_pilot(pilot, actor_staff_user_id)
    return pilot


def _ensure_issued_license(customer, subscription, plan, plan_item, key, pepper, actor_staff_user_id):
    license_row = db_session.get(License, _row_id_for(key, "license"))
    if license_row is None:
        license_row = create_license(
            {
                "id": _row_id_for(key, "license"), "customer_id": customer.id, "subscription_id": subscription.id,
                "product_id": plan.product_id, "plan_id": plan.id,
                "allowed_platforms": ",".join(plan_item["supported_platform_codes"]),
                "device_limit": plan.included_device_count, "valid_from": None, "valid_until": None,
            },
            actor_staff_user_id,
        )
    full_key = None
    if license_row.status == "DRAFT":
        license_row, full_key = issue_license_key(license_row, pepper, f"onboard-{key}"[:128], actor_staff_user_id)
    return license_row, full_key
