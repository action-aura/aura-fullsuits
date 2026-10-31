"""New customer flow, slice S2: the seller's one "Create a new customer" action.

Every guard here is proven by what it did or did not WRITE (row counts and files
on disk), never by a status code alone: a 400 cannot tell "refused before any
write" from "refused after half the rows". Each deny test has its matching allow
test (the override must work for a manager, the duplicate check must not trip on
a different shop, the document must open for the one role that may open it).
"""
from __future__ import annotations

import base64
import os
import threading
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_customer_accounts_service import GOOD
from tests.test_issuance_sale import _profile

PEPPER = "test-license-pepper"
KEY32 = base64.b64encode(bytes(range(32))).decode()
OTHER_KEY32 = base64.b64encode(bytes(range(100, 132))).decode()


# ------------------------------------------------------------------ fixtures

def _jpeg(secret: bytes = b"GPS 31.95N 35.91E Pixel-7") -> bytes:
    """A tiny JPEG that parses as segments: SOI, JFIF, an EXIF APP1 carrying
    `secret`, a comment, then the scan. Not a viewable picture, but it has the
    exact structure the cleaner walks."""
    jfif = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    exif_body = b"Exif\x00\x00" + secret
    exif = b"\xff\xe1" + (len(exif_body) + 2).to_bytes(2, "big") + exif_body
    comment = b"\xff\xfe" + (2 + 5).to_bytes(2, "big") + b"hello"
    sos = b"\xff\xda" + (8).to_bytes(2, "big") + b"\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\x56\xff\xd9"
    return b"\xff\xd8" + jfif + exif + comment + sos


def _png(secret: bytes = b"Author=Someone") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + kind + data + b"\x00\x00\x00\x00"

    ihdr = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Comment\x00" + secret) + chunk(b"IDAT", b"\x00") + chunk(b"IEND", b"")


@pytest.fixture()
def kyc(app, tmp_path):
    app.config.update(CUSTOMER_ONBOARDING_ENABLED=True, KYC_KEYS=f"k1:{KEY32}", KYC_DIRECTORY=str(tmp_path / "kyc"),
                      CUSTOMER_PORTAL_BASE_URL="https://actionaura.me", SMTP_HOST="")
    return tmp_path / "kyc"


def _world(app):
    w = {
        "seller": make_staff(app, f"seller-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"]),
        "manager": make_staff(app, f"mgr-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES_MANAGER"], mfa=True),
        "other_seller": make_staff(app, f"seller2-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"]),
        "admin": make_staff(app, f"admin-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True),
    }
    for who in ("seller", "manager", "other_seller", "admin"):
        _profile(app, w[who], f"EMP-{who[:3].upper()}-{uuid.uuid4().hex[:4]}", who.title())
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan
        from app.extensions import db_session
        from app.models.catalog import Product

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_RETAIL")).scalars().one()
        product.is_sellable = True
        product.is_active = True
        db_session.commit()
        plan = create_plan(
            {"plan_code": f"NF_{uuid.uuid4().hex[:6]}", "product_id": product.id, "name": "Aura Retail Yearly",
             "billing_model": "ANNUAL", "billing_interval_months": 12, "effective_date": date.today() - timedelta(days=1),
             "included_device_count": 2, "currency": "JOD"},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, 100, "JOD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        w["plan"] = plan.id
    return w


def _codes(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.rbac import get_staff_permission_codes

        return get_staff_permission_codes(db_session.get(StaffUser, staff_id))


def onboard(app, w, *, who="seller", **overrides):
    """Runs the action in an app context. Returns the result (or raises OnboardingError)."""
    from app.customer_onboarding import service

    n = uuid.uuid4().hex[:6]
    args = dict(
        idempotency_key=str(uuid.uuid4()), business_name=f"Haddad Market {n}", contact_name="Omar Haddad",
        email=f"omar-{n}@haddad.example", temp_password=GOOD, phone="079 123 4567", address="Rainbow Street, Amman",
        city="Amman", latitude="31.9539", longitude="35.9106", accuracy_meters="12", cr_number=f"CR-{n}-77",
        plan_id=w["plan"], language="ar", consent=True, id_photo=_jpeg(), cr_photo=_png(),
        actor_staff_user_id=w[who], actor_permission_codes=_codes(app, w[who]), license_pepper=PEPPER,
    )
    args.update(overrides)
    if "phone" not in overrides:
        args["phone"] = "079 %03d %04d" % (int(n[:3], 16) % 1000, int(n[3:], 16) % 10000)
    with app.app_context():
        return service.create_trial_customer(**args)


def counts(app) -> dict:
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.customer_accounts import CustomerAccount
        from app.models.customer_onboarding import CustomerDocument
        from app.models.customers import Customer, CustomerAddress, CustomerContact
        from app.models.leads import CustomerLocation
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        out = {m.__name__: db_session.execute(select(func.count()).select_from(m)).scalar_one()
               for m in (Customer, CustomerContact, CustomerAddress, CustomerLocation, CustomerAccount, CustomerDocument,
                         Subscription, PilotRecord, License)}
        root = app.config["KYC_DIRECTORY"]
        out["files"] = sum(len(f) for _, _, f in os.walk(root)) if os.path.isdir(root) else 0
        return out


NOTHING = {"Customer": 0, "CustomerContact": 0, "CustomerAddress": 0, "CustomerLocation": 0, "CustomerAccount": 0,
           "CustomerDocument": 0, "Subscription": 0, "PilotRecord": 0, "License": 0, "files": 0}


# ------------------------------------------------------------- the whole action

def test_one_action_creates_everything_the_flow_needs(app, seeded, kyc):
    w = _world(app)
    result = onboard(app, w)
    assert result.replayed is False and result.license_key and result.license_key.startswith("AURA")
    assert counts(app) == {**NOTHING, "Customer": 1, "CustomerContact": 1, "CustomerAddress": 1, "CustomerLocation": 1,
                           "CustomerAccount": 1, "CustomerDocument": 2, "Subscription": 1, "PilotRecord": 1, "License": 1, "files": 2}
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.customer_accounts import CustomerAccount, CustomerAccountToken
        from app.models.customers import Customer, CustomerContact
        from app.models.leads import CustomerLocation
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        customer = db_session.get(Customer, result.customer_id)
        assert customer.assigned_sales_staff_id == w["seller"]  # the seller owns the customer
        assert customer.commercial_registration_reference.startswith("CR-")
        contact = db_session.execute(select(CustomerContact)).scalars().one()
        assert contact.business_phone.startswith("+9627") and contact.is_primary  # E.164
        location = db_session.execute(select(CustomerLocation)).scalars().one()
        assert location.source == "GPS" and float(location.latitude) == pytest.approx(31.9539)
        account = db_session.execute(select(CustomerAccount)).scalars().one()
        assert account.status == "ACTIVE" and account.password_must_change is True and account.email_verified_at is None
        assert account.password_set_by_staff_user_id == w["seller"] and account.language == "ar"
        token = db_session.execute(select(CustomerAccountToken)).scalars().one()
        assert token.kind == "VERIFY_EMAIL"
        subscription = db_session.execute(select(Subscription)).scalars().one()
        # PILOT with NO dates: the clock starts at the first device, not now.
        assert subscription.status == "PILOT" and subscription.start_date is None and subscription.end_date is None
        assert subscription.sales_owner_staff_user_id == w["seller"] and subscription.device_allowance == 2
        pilot = db_session.execute(select(PilotRecord)).scalars().one()
        assert pilot.pilot_kind == "TRIAL" and pilot.status == "ACTIVE" and pilot.trial_started_at is None
        assert pilot.max_extensions_allowed == 1 and pilot.trial_length_days == 7
        lic = db_session.execute(select(License)).scalars().one()
        assert lic.status == "ISSUED" and lic.valid_from is None and lic.valid_until is None and lic.device_limit == 2
        assert lic.key_prefix and lic.key_secret_hmac
    assert "Haddad" in result.whatsapp_message and result.license_key in result.whatsapp_message


def test_the_temporary_password_signs_in_and_must_be_changed(app, seeded, kyc):
    from app.customer_accounts import service as accounts

    w = _world(app)
    result = onboard(app, w, email="omar@haddad.example")
    with app.app_context():
        token, account = accounts.sign_in("omar@haddad.example", GOOD, "203.0.113.5", "ua")
        assert account.password_must_change is True
        # allow: choosing their own password clears the flag
        new_token, account = accounts.change_password(token, GOOD, "My-Own-Horse-Battery-9", "203.0.113.5", "ua")
        assert account.password_must_change is False
        # and the temporary one no longer works
        with pytest.raises(accounts.SignInFailed):
            accounts.sign_in("omar@haddad.example", GOOD, "203.0.113.5", "ua")


def test_the_password_is_never_stored_audited_or_mailed(app, seeded, kyc):
    app.config["SMTP_HOST"] = "smtp.test"
    w = _world(app)
    secret = "Zebra-Quartz-Lantern-42"
    result = onboard(app, w, temp_password=secret)
    assert result.emailed is True and result.verify_link is None  # mailed -> not shown
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.customer_accounts import CustomerAccount
        from app.models.email_outbox import EmailOutbox

        mail = db_session.execute(select(EmailOutbox)).scalars().one()
        assert mail.kind == "customer_welcome" and "#t=" in mail.body_text and secret not in mail.body_text
        assert secret not in (mail.body_html or "")
        assert db_session.execute(select(CustomerAccount.password_hash)).scalar_one() != secret
        for row in db_session.execute(select(AuditLog)).scalars():
            assert secret not in repr(row.after_state_redacted) + repr(row.before_state_redacted) + (row.reason or "")


def test_with_email_off_the_verify_link_is_returned_once(app, seeded, kyc):
    w = _world(app)
    result = onboard(app, w)
    assert result.emailed is False and result.verify_link.startswith("https://actionaura.me/ar/account/verify-email/#t=")
    from urllib.parse import unquote

    with app.app_context():
        from app.customer_accounts import service as accounts
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        accounts.verify_email_with_token(unquote(result.verify_link.split("#t=", 1)[1]), "203.0.113.5")
        assert db_session.execute(select(CustomerAccount.email_verified_at)).scalar_one() is not None
        # single use
        with pytest.raises(accounts.AccountError):
            accounts.verify_email_with_token(unquote(result.verify_link.split("#t=", 1)[1]), "203.0.113.5")


# --------------------------------------------- refused forms write NOTHING (incl. files)

@pytest.mark.parametrize("overrides, code", [
    ({"consent": False}, "CONSENT_REQUIRED"),
    ({"business_name": "  "}, "BUSINESS_NAME_REQUIRED"),
    ({"email": "not-an-email"}, "EMAIL_INVALID"),
    ({"temp_password": "short"}, "PASSWORD_TOO_SHORT"),
    ({"temp_password": "password123"}, "PASSWORD_TOO_COMMON"),
    ({"phone": "12"}, "PHONE_INVALID"),
    ({"cr_number": ""}, "CR_NUMBER_REQUIRED"),
    ({"address": ""}, "ADDRESS_REQUIRED"),
    ({"latitude": "95", "longitude": "10"}, "LOCATION_INVALID"),
    ({"latitude": "31.9", "longitude": ""}, "LOCATION_INVALID"),
    ({"id_photo": b""}, "ID_PHOTO_REQUIRED"),
    ({"cr_photo": b""}, "CR_PHOTO_REQUIRED"),
    ({"id_photo": b"MZ\x90\x00 this is an exe"}, "DOCUMENT_TYPE_NOT_ALLOWED"),
    ({"cr_photo": b"<html><script>alert(1)</script></html>"}, "DOCUMENT_TYPE_NOT_ALLOWED"),
    ({"id_photo": b"\xff\xd8\xff" + b"0" * (10 * 1024 * 1024 + 1)}, "DOCUMENT_TOO_LARGE"),
    ({"id_photo": b"\xff\xd8\xff\xe1\x00"}, "DOCUMENT_CORRUPT"),  # JPEG whose segment runs off the end
    ({"product_code": "AURA_CLINIC"}, "PRODUCT_NOT_AVAILABLE"),
    ({"plan_id": str(uuid.uuid4())}, "PACKAGE_NOT_FOUND"),
])
def test_a_refused_form_writes_nothing_not_even_a_file(app, seeded, kyc, overrides, code):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, **overrides)
    assert caught.value.code == code
    assert counts(app) == NOTHING


def test_no_kyc_key_means_nothing_is_created(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    app.config["KYC_KEYS"] = ""
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w)
    assert caught.value.code == "KYC_NOT_READY" and counts(app) == NOTHING
    # a malformed key is the same refusal, and a well-formed one works again
    app.config["KYC_KEYS"] = "k1:not-base64!!"
    with pytest.raises(OnboardingError):
        onboard(app, w)
    assert counts(app) == NOTHING
    app.config["KYC_KEYS"] = f"k1:{KEY32}"
    onboard(app, w)
    assert counts(app)["Customer"] == 1


def test_the_feature_is_off_by_default(app, seeded, tmp_path):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    app.config.update(KYC_KEYS=f"k1:{KEY32}", KYC_DIRECTORY=str(tmp_path / "kyc"))
    assert app.config["CUSTOMER_ONBOARDING_ENABLED"] is False
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w)
    assert caught.value.code == "FEATURE_OFF" and counts(app) == NOTHING


def test_a_seller_without_a_profile_cannot_create_a_customer(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    w["noprofile"] = make_staff(app, "noprofile@example.com", role_codes=["SALES"])
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="noprofile")
    assert caught.value.code == "OWN_PROFILE_REQUIRED" and counts(app) == NOTHING


# ------------------------------------------------------------- exactly once

def test_pressing_the_same_form_twice_is_one_customer_and_one_key(app, seeded, kyc):
    w = _world(app)
    key = str(uuid.uuid4())
    first = onboard(app, w, idempotency_key=key, email="same@haddad.example", business_name="Same Shop")
    again = onboard(app, w, idempotency_key=key, email="same@haddad.example", business_name="Same Shop")
    assert first.license_key and again.replayed is True and again.license_key is None and again.whatsapp_message is None
    assert (again.customer_id, again.license_id, again.subscription_id) == (first.customer_id, first.license_id, first.subscription_id)
    assert counts(app)["Customer"] == 1 and counts(app)["License"] == 1 and counts(app)["files"] == 2


def test_the_same_form_pressed_at_the_same_moment_is_still_one_customer(app, seeded, kyc):
    w = _world(app)
    key = str(uuid.uuid4())
    results, errors = [], []

    def press():
        try:
            results.append(onboard(app, w, idempotency_key=key, email="race@haddad.example", business_name="Race Market"))
        except Exception as exc:  # noqa: BLE001 -- recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=press) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert sum(1 for r in results if r.license_key) == 1  # exactly one press saw the key
    assert counts(app) == {**NOTHING, "Customer": 1, "CustomerContact": 1, "CustomerAddress": 1, "CustomerLocation": 1,
                           "CustomerAccount": 1, "CustomerDocument": 2, "Subscription": 1, "PilotRecord": 1, "License": 1, "files": 2}


def test_two_different_forms_racing_on_one_email_make_exactly_one_customer(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    results, errors = [], []

    def press():
        try:
            results.append(onboard(app, w, email="contested@haddad.example"))  # a fresh key, shop and CR each press
        except OnboardingError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=press) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and errors and set(errors) <= {"EMAIL_IN_USE"}, (len(results), errors)
    # the losers left NOTHING behind: no orphan customer, photo, subscription or file
    assert counts(app) == {**NOTHING, "Customer": 1, "CustomerContact": 1, "CustomerAddress": 1, "CustomerLocation": 1,
                           "CustomerAccount": 1, "CustomerDocument": 2, "Subscription": 1, "PilotRecord": 1, "License": 1, "files": 2}


def test_the_same_key_with_different_details_is_refused(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    key = str(uuid.uuid4())
    onboard(app, w, idempotency_key=key, business_name="First Shop", email="first@shop.example")
    before = counts(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, idempotency_key=key, business_name="Second Shop", email="second@shop.example")
    assert caught.value.code == "KEY_USED_FOR_DIFFERENT_FORM" and counts(app) == before


def test_a_crash_part_way_is_finished_by_the_retry_without_duplicates(app, seeded, kyc, monkeypatch):
    """The services commit one by one. Break the LAST step once (the licence), then press again with the same
    key: the customer, account, documents, subscription and pilot must be reused, not made twice."""
    from app.customer_onboarding import service

    w = _world(app)
    key = str(uuid.uuid4())
    real = service._ensure_issued_license
    monkeypatch.setattr(service, "_ensure_issued_license", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        onboard(app, w, idempotency_key=key, email="crash@haddad.example", business_name="Crash Shop")
    mid = counts(app)
    assert mid["Customer"] == 1 and mid["License"] == 0 and mid["CustomerAccount"] == 1
    monkeypatch.setattr(service, "_ensure_issued_license", real)
    result = onboard(app, w, idempotency_key=key, email="crash@haddad.example", business_name="Crash Shop")
    assert result.license_key and result.replayed is False
    assert counts(app) == {**NOTHING, "Customer": 1, "CustomerContact": 1, "CustomerAddress": 1, "CustomerLocation": 1,
                           "CustomerAccount": 1, "CustomerDocument": 2, "Subscription": 1, "PilotRecord": 1, "License": 1, "files": 2}


# ------------------------------------------------------------------ duplicates

def _dup_case(app, w, **second):
    first = onboard(app, w, cr_number="CR 123-45", phone="079 555 0000", email="first@dup.example")
    return first, second


@pytest.mark.parametrize("field, second", [
    ("cr", {"cr_number": "cr12345"}),                 # same CR, different spacing/case/dash
    ("phone", {"phone": "+962 79 555 0000"}),         # same phone typed as E.164
    ("phone", {"phone": "0795550000"}),               # same phone typed locally
    ("email", {"email": "FIRST@dup.example"}),        # same email, different case
])
def test_a_second_trial_for_the_same_shop_is_refused_and_writes_nothing(app, seeded, kyc, field, second):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    _dup_case(app, w)
    before = counts(app)
    args = {"cr_number": "OTHER-CR-9", "phone": "079 888 0001", "email": "someone@else.example", **second}
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="other_seller", **args)
    assert caught.value.code in ("TRIAL_ALREADY_USED", "EMAIL_IN_USE")
    assert counts(app) == before
    if caught.value.code == "TRIAL_ALREADY_USED":
        assert field in caught.value.params["fields"]


def test_a_different_shop_is_not_a_duplicate(app, seeded, kyc):
    w = _world(app)
    _dup_case(app, w)
    onboard(app, w, who="other_seller", cr_number="CR 999-11", phone="079 111 2222", email="fresh@shop.example")
    assert counts(app)["Customer"] == 2


def test_only_a_manager_with_a_reason_may_override_a_duplicate(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    _dup_case(app, w)
    dup = dict(cr_number="CR 123-45", phone="079 555 0000", email="second@dup.example")
    before = counts(app)
    # deny: a seller asking for the override
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="seller", override_duplicate=True, override_reason="the owner begged me please", **dup)
    assert caught.value.code == "OVERRIDE_NOT_ALLOWED" and counts(app) == before
    # deny: a manager without a real reason
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="manager", override_duplicate=True, override_reason="ok", **dup)
    assert caught.value.code == "OVERRIDE_REASON_REQUIRED" and counts(app) == before
    # allow: a manager with a reason -> a second customer and trial, audited with the reason
    result = onboard(app, w, who="manager", override_duplicate=True,
                     override_reason="Two branches with one registration, owner confirmed by phone", **dup)
    assert counts(app)["Customer"] == 2 and counts(app)["PilotRecord"] == 2
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        row = db_session.execute(select(AuditLog).where(
            AuditLog.action_code == "CUSTOMER_ONBOARDED", AuditLog.entity_public_id == str(result.customer_id))).scalars().one()
        assert "Two branches" in row.reason and row.after_state_redacted["duplicate_override"] is True


def test_the_same_signin_email_can_never_be_overridden(app, seeded, kyc):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    _dup_case(app, w)
    before = counts(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="manager", override_duplicate=True, override_reason="a perfectly good long reason",
                cr_number="NEW-CR", phone="079 000 1111", email="first@dup.example")
    assert caught.value.code == "EMAIL_IN_USE" and counts(app) == before


def test_the_database_itself_allows_one_trial_per_customer(app, seeded, kyc):
    """The partial unique index, with every application check bypassed."""
    from sqlalchemy.exc import IntegrityError

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.subscriptions import Subscription

        first = db_session.execute(select(PilotRecord)).scalars().one()
        other_sub = Subscription(customer_id=result.customer_id, product_id=first.product_id,
                                 plan_id=db_session.get(Subscription, first.subscription_id).plan_id, status="PILOT")
        db_session.add(other_sub)
        db_session.flush()

        def pilot(kind):
            return PilotRecord(subscription_id=other_sub.id, customer_id=result.customer_id, product_id=first.product_id,
                               pilot_start=date.today(), pilot_end=date.today() + timedelta(days=5), pilot_kind=kind)

        db_session.add(pilot("TRIAL"))
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()
        # allow: an ordinary PILOT for the same customer is still fine
        other_sub = Subscription(customer_id=result.customer_id, product_id=first.product_id,
                                 plan_id=db_session.get(Subscription, first.subscription_id).plan_id, status="PILOT")
        db_session.add(other_sub)
        db_session.flush()
        db_session.add(pilot("PILOT"))
        db_session.flush()


# ------------------------------------------------------------------ documents

def test_documents_are_encrypted_at_rest_and_round_trip(app, seeded, kyc):
    from app.customer_onboarding import documents

    w = _world(app)
    secret = b"GPS 31.95N 35.91E Pixel-7"
    result = onboard(app, w, id_photo=_jpeg(secret), cr_photo=_png(b"Author=Someone"))
    with app.app_context():
        from app.extensions import db_session

        rows = documents.documents_of(result.customer_id)
        assert {r.kind for r in rows} == {"NATIONAL_ID", "COMMERCIAL_REGISTRATION"}
        for row in rows:
            blob = open(os.path.join(app.config["KYC_DIRECTORY"], *row.storage_key.split("/")), "rb").read()
            assert blob.startswith(b"AKYC1") and b"\xff\xd8\xff" not in blob[:20] and b"\x89PNG" not in blob  # no plaintext
            assert row.enc_key_id == "k1" and row.size_bytes > 0
            plain, content_type = documents.read_document(row)
            assert content_type == ("image/jpeg" if row.kind == "NATIONAL_ID" else "image/png")
            assert plain.startswith(b"\xff\xd8\xff" if row.kind == "NATIONAL_ID" else b"\x89PNG")
            # metadata stripped before it was ever stored
            assert secret not in plain and b"Author=Someone" not in plain and b"Exif" not in plain


def test_metadata_stripping_keeps_the_picture_and_removes_gps_and_text(app, seeded):
    from app.customer_onboarding import documents

    cleaned = documents.strip_jpeg_metadata(_jpeg())
    assert b"GPS" not in cleaned and b"Exif" not in cleaned and b"JFIF" in cleaned and b"hello" in cleaned
    assert cleaned.endswith(b"\x12\x34\x56\xff\xd9")  # the scan data is byte-identical
    cleaned_png = documents.strip_png_metadata(_png())
    assert b"tEXt" not in cleaned_png and b"IDAT" in cleaned_png and cleaned_png.endswith(b"IEND\x00\x00\x00\x00")


def test_a_blob_swapped_to_another_document_or_customer_does_not_decrypt(app, seeded, kyc):
    from app.customer_onboarding import documents

    w = _world(app)
    a = onboard(app, w)
    b = onboard(app, w)
    with app.app_context():
        rows_a = documents.documents_of(a.customer_id)
        rows_b = documents.documents_of(b.customer_id)
        path_a = os.path.join(app.config["KYC_DIRECTORY"], *rows_a[0].storage_key.split("/"))
        path_b = os.path.join(app.config["KYC_DIRECTORY"], *rows_b[0].storage_key.split("/"))
        original = open(path_b, "rb").read()
        open(path_b, "wb").write(open(path_a, "rb").read())  # somebody copies A's ID over B's
        with pytest.raises(documents.DocumentError) as caught:
            documents.read_document(rows_b[0])
        assert caught.value.code == "DOCUMENT_UNREADABLE"
        open(path_b, "wb").write(original)
        assert documents.read_document(rows_b[0])[0]  # allow: the right blob reads again


def test_a_tampered_blob_and_a_missing_key_are_refused(app, seeded, kyc):
    from app.customer_onboarding import documents

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        row = documents.documents_of(result.customer_id)[0]
        path = os.path.join(app.config["KYC_DIRECTORY"], *row.storage_key.split("/"))
        good = open(path, "rb").read()
        open(path, "wb").write(good[:-1] + bytes([good[-1] ^ 1]))
        with pytest.raises(documents.DocumentError):
            documents.read_document(row)
        open(path, "wb").write(good)
        # rotation: the first key is for new files, the old one still reads old files
        app.config["KYC_KEYS"] = f"k2:{OTHER_KEY32},k1:{KEY32}"
        assert documents.read_document(row)[0]
        # a key list that no longer holds the file's key cannot read it
        app.config["KYC_KEYS"] = f"k2:{OTHER_KEY32}"
        with pytest.raises(documents.DocumentError) as caught:
            documents.read_document(row)
        assert caught.value.code == "KYC_KEY_UNAVAILABLE"


def test_a_storage_key_that_escapes_the_directory_is_refused(app, seeded, kyc):
    from app.customer_onboarding import documents

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session

        row = documents.documents_of(result.customer_id)[0]
        row.storage_key = "../../../../etc/passwd"
        with pytest.raises(documents.DocumentError):
            documents.read_document(row)
        db_session.rollback()


def test_retention_dates_and_purge(app, seeded, kyc):
    from app.customer_onboarding import documents

    w = _world(app)
    result = onboard(app, w)
    with app.app_context():
        from app.extensions import db_session

        rows = documents.documents_of(result.customer_id)
        assert all(r.purge_after is not None for r in rows)
        assert documents.purge_expired_documents() == 0  # nothing due yet: allow side, nothing deleted
        assert counts_files(app) == 2
        from datetime import datetime, timezone

        far = datetime.now(timezone.utc) + timedelta(days=365)
        assert documents.purge_expired_documents(now=far) == 2
        assert counts_files(app) == 0
        assert all(r.purged_at is not None for r in db_session.execute(select(type(rows[0]))).scalars())
        assert documents.documents_of(result.customer_id) == []
        assert documents.purge_expired_documents(now=far) == 0  # idempotent
        # conversion keeps them: cleared purge_after
    w2 = onboard(app, w)
    with app.app_context():
        assert documents.keep_for_customer_lifetime(w2.customer_id) == 2


def counts_files(app) -> int:
    return counts(app)["files"]


# ------------------------------------------------------- routes (the real screens)

def _page_token(client):
    return get_csrf(client.get("/customers/new-trial").get_data(as_text=True))


def _post_form(client, w, **over):
    n = uuid.uuid4().hex[:6]
    data = {
        "csrf_token": _page_token(client), "idempotency_key": str(uuid.uuid4()), "business_name": f"Route Shop {n}",
        "contact_name": "Rana", "phone": "079 777 %04d" % (int(n[:3], 16) % 10000), "cr_number": f"RCR-{n}",
        "address": "Abdoun", "city": "Amman", "plan_id": str(w["plan"]), "email": f"rana-{n}@route.example",
        "temp_password": GOOD, "language": "ar", "consent": "1", "product_code": "AURA_RETAIL",
    }
    data.update(over)
    import io

    data["id_photo"] = (io.BytesIO(over.get("_id", _jpeg())), "id.jpg", "image/jpeg")
    data["cr_photo"] = (io.BytesIO(over.get("_cr", _png())), "cr.png", "image/png")
    data.pop("_id", None)
    data.pop("_cr", None)
    return client.post("/customers/new-trial", data=data, content_type="multipart/form-data")


def _big_jpeg(megabytes: float) -> bytes:
    base = _jpeg()
    return base[:-2] + b"\x00" * int(megabytes * 1024 * 1024) + b"\xff\xd9"


def test_two_big_phone_photos_fit_but_an_absurd_body_is_refused_before_anything_is_written(app, client, seeded, kyc):
    w = _world(app)
    force_login(client, app, w["seller"])
    # 2 x 7 MB = 14 MB: over Flask's global 12 MB ceiling, inside this one form's own limit
    ok = _post_form(client, w, _id=_big_jpeg(7), _cr=_big_jpeg(7))
    assert ok.status_code == 200 and counts(app)["CustomerDocument"] == 2
    before = counts(app)
    huge = _post_form(client, w, _id=_big_jpeg(11), _cr=_big_jpeg(11))  # 22 MB: over the form's limit too
    assert huge.status_code == 413 and counts(app) == before
    # and the global ceiling still binds every other route (the raise is for this POST only)
    other = client.post("/auth/login", data={"csrf_token": "x", "email": "a@b.example", "password": "p" * (13 * 1024 * 1024)})
    assert other.status_code == 413


def test_the_form_is_a_404_while_the_feature_is_off(app, client, seeded, tmp_path):
    w = _world(app)
    force_login(client, app, w["seller"])
    assert client.get("/customers/new-trial").status_code == 404
    assert client.post("/customers/new-trial", data={}).status_code in (400, 404)
    assert counts(app)["Customer"] == 0


def test_the_form_is_mobile_first_and_asks_for_camera_photos(app, client, seeded, kyc):
    w = _world(app)
    force_login(client, app, w["seller"])
    html = client.get("/customers/new-trial").get_data(as_text=True)
    assert html.count('capture="environment"') == 2 and 'type="file"' in html and 'accept="image/*' in html
    assert 'inputmode="tel"' in html and 'type="email"' in html and 'enctype="multipart/form-data"' in html
    assert "font-size: 16px" in html  # a smaller font makes iOS zoom the page when a field is focused
    assert 'name="override_duplicate"' not in html  # a seller never sees the manager-only override
    assert 'name="plan_id"' in html and "Aura Retail" in html
    # allow: a manager does see the override
    client2 = app.test_client()
    force_login(client2, app, w["manager"])
    assert 'name="override_duplicate"' in client2.get("/customers/new-trial").get_data(as_text=True)


def test_a_viewer_without_the_permission_gets_403_and_nothing_is_written(app, client, seeded, kyc):
    w = _world(app)
    viewer = make_staff(app, "viewer@example.com", role_codes=["VIEWER"])
    force_login(client, app, viewer)
    assert client.get("/customers/new-trial").status_code == 403
    assert counts(app) == NOTHING


def test_posting_the_form_creates_the_customer_and_shows_the_key_once(app, client, seeded, kyc):
    w = _world(app)
    force_login(client, app, w["seller"])
    resp = _post_form(client, w)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert 'data-onboarding-result="1"' in html and "AURA-" in html and 'data-link-shown-once="1"' in html
    assert GOOD not in html  # the password never travels back
    assert counts(app)["Customer"] == 1 and counts(app)["CustomerDocument"] == 2


def test_a_refused_post_keeps_the_text_but_not_the_password_and_writes_nothing(app, client, seeded, kyc):
    w = _world(app)
    force_login(client, app, w["seller"])
    resp = _post_form(client, w, business_name="Typed Shop", consent="")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 400 and 'data-onboarding-error="1"' in html
    assert 'value="Typed Shop"' in html and GOOD not in html
    assert counts(app) == NOTHING


def test_a_seller_cannot_open_a_document_but_a_manager_can_and_it_is_audited(app, client, seeded, kyc):
    w = _world(app)
    force_login(client, app, w["seller"])
    _post_form(client, w)
    with app.app_context():
        from app.customer_onboarding import documents
        from app.extensions import db_session
        from app.models.customers import Customer

        cid = db_session.execute(select(Customer.id)).scalar_one()
        doc = documents.documents_of(cid, kinds=("NATIONAL_ID",))[0]
        url = f"/customers/{cid}/documents/{doc.id}"
        doc_id = doc.id
    # deny: the seller who took the photo (and the customer's own seller)
    assert client.get(url).status_code == 403
    # allow: a Sales Manager
    mgr = app.test_client()
    force_login(mgr, app, w["manager"])
    ok = mgr.get(url)
    assert ok.status_code == 200 and ok.mimetype == "image/jpeg" and ok.data.startswith(b"\xff\xd8\xff")
    assert ok.headers["Cache-Control"] == "no-store" and ok.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in ok.headers["Content-Security-Policy"]
    # the exact customer must match the document (no reading A's file through B's url)
    assert mgr.get(f"/customers/{uuid.uuid4()}/documents/{doc_id}").status_code == 404
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        viewed = db_session.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.action_code == "CUSTOMER_DOCUMENT_VIEWED", AuditLog.entity_public_id == str(doc_id))).scalar_one()
        assert viewed == 1  # the 403 and the 404 left no "viewed" row


def test_every_onboarding_message_is_translated(app, seeded):
    from app.customer_onboarding.service import ONBOARDING_MESSAGES
    from app.i18n_labels import localize_onboarding_error

    with app.test_request_context("/?lang=ar"):
        from flask_babel import force_locale

        with force_locale("ar"):
            for code in ONBOARDING_MESSAGES:
                params = {"max": 5, "max_mb": 10, "min": 10, "what": "x", "fields": ("cr",)}
                english = ONBOARDING_MESSAGES[code] % {k: v for k, v in params.items()} if "%(" in ONBOARDING_MESSAGES[code] else ONBOARDING_MESSAGES[code]
                arabic = localize_onboarding_error(code, **params)
                assert arabic != code and arabic != english, code


# ------------------------------------- r32: Arabic-keyboard digits must not blind the duplicate check
# (NFKC leaves U+0660.. digits unchanged, so only unicodedata.decimal normalises them.)

ARABIC_INDIC = "".join(chr(0x0660 + int(d)) for d in "1234509")   # 1234509
EXTENDED = "".join(chr(0x06F0 + int(d)) for d in "1234509")       # 1234509 (Persian/Urdu forms)


@pytest.mark.parametrize("typed", [ARABIC_INDIC, EXTENDED, f"{ARABIC_INDIC[:5]}-{ARABIC_INDIC[5:]}"])
def test_a_cr_typed_in_arabic_digits_is_still_a_duplicate(app, seeded, kyc, typed):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    onboard(app, w, cr_number="1234509")
    before = counts(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="other_seller", cr_number=typed)
    assert caught.value.code == "TRIAL_ALREADY_USED" and "cr" in caught.value.params["fields"]
    assert counts(app) == before


def test_an_old_row_stored_with_arabic_digits_is_matched_by_an_ascii_cr(app, seeded, kyc):
    """Rows saved before the ASCII rule hold the digits as typed; the SQL side must map them too."""
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    first = onboard(app, w, cr_number="777")
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.get(Customer, first.customer_id).commercial_registration_reference = ARABIC_INDIC
        db_session.commit()
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="other_seller", cr_number="1234509")
    assert caught.value.code == "TRIAL_ALREADY_USED" and "cr" in caught.value.params["fields"]


def test_a_new_arabic_digit_cr_is_allowed_and_stored_as_ascii(app, seeded, kyc):
    w = _world(app)
    result = onboard(app, w, cr_number=ARABIC_INDIC)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        assert db_session.get(Customer, result.customer_id).commercial_registration_reference == "1234509"


@pytest.mark.parametrize("typed", ["---", " / . ", "—"])
def test_a_cr_with_nothing_comparable_in_it_is_refused(app, seeded, kyc, typed):
    """An empty key used to mean 'skip the CR duplicate check' -- a number that can never clash."""
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, cr_number=typed)
    assert caught.value.code == "CR_NUMBER_REQUIRED" and counts(app) == NOTHING


# ------------------------------------- r32: the same phone is always the same string

AR = lambda digits: "".join(chr(0x0660 + int(d)) for d in digits)  # noqa: E731 -- Arabic-Indic digits


@pytest.mark.parametrize("typed", [
    f"+{AR('962')} {AR('79')} {AR('123')} {AR('4567')}",   # plus form, Arabic-Indic digits
    AR("0791234567"),                                       # local form, Arabic-Indic digits
    "+962 079 123 4567",                                    # trunk 0 typed after the country code
    "+962 (0) 79 123 4567",
    "00962 0791234567",                                     # same slip with the 00 prefix
    f"+{AR('962')} {AR('0791234567')}",                     # both slips at once
])
def test_the_same_jordan_mobile_is_one_string_however_it_is_typed(typed):
    from app.customer_onboarding.service import normalize_phone_e164

    assert normalize_phone_e164(typed) == "+962791234567"


@pytest.mark.parametrize("typed, expected", [
    ("+44 7911 123456", "+447911123456"),        # a genuine non-Jordan number is untouched
    ("+39 06 1234 5678", "+390612345678"),       # Italy keeps its 0: stripping it blindly would corrupt the number
    ("+971 50 123 4567", "+971501234567"),
    ("+44 (0)7911 123456", "+447911123456"),     # the UK trunk 0 is dropped
])
def test_other_countries_numbers_are_not_mangled(typed, expected):
    from app.customer_onboarding.service import normalize_phone_e164

    assert normalize_phone_e164(typed) == expected


@pytest.mark.parametrize("typed", ["+962 079 555 0000", f"+{AR('962')} {AR('0795550000')}", AR("0795550000")])
def test_a_phone_typed_with_a_slip_is_still_a_duplicate(app, seeded, kyc, typed):
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    onboard(app, w, phone="079 555 0000")
    before = counts(app)
    with pytest.raises(OnboardingError) as caught:
        onboard(app, w, who="other_seller", phone=typed)
    assert caught.value.code == "TRIAL_ALREADY_USED" and "phone" in caught.value.params["fields"]
    assert counts(app) == before


# ------------------------------------- r32: two forms for the SAME shop cannot both win

@pytest.mark.parametrize("same", ["cr", "phone", "both"])
def test_two_forms_for_the_same_shop_at_the_same_moment_make_one_customer(app, seeded, kyc, monkeypatch, same):
    """Different emails, different form keys, same CR and phone. Without a lock on the
    shop's identity both pass find_duplicates before either commits. The barrier makes
    that interleaving certain: it holds the first thread inside the duplicate check until
    the second has had its own look (it times out and carries on when the second is
    legitimately waiting on the lock -- that is the fixed behaviour)."""
    from app.customer_onboarding import service
    from app.customer_onboarding.service import OnboardingError

    w = _world(app)
    barrier = threading.Barrier(2)
    real = service.find_duplicates

    def meet_after_looking(**kwargs):
        found = real(**kwargs)
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(service, "find_duplicates", meet_after_looking)
    results, errors = [], []

    def press(n):
        try:
            results.append(onboard(
                app, w, email=f"twin{n}@haddad.example", business_name=f"Twin Market {n}",
                cr_number="TWIN-4242" if same in ("cr", "both") else f"OTHER-{n}",
                phone="079 777 4242" if same in ("phone", "both") else f"079 888 000{n}"))
        except Exception as exc:  # noqa: BLE001 -- recorded and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=press, args=(n,)) for n in (1, 2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and len(errors) == 1, (results, errors)
    assert isinstance(errors[0], OnboardingError) and errors[0].code == "TRIAL_ALREADY_USED", errors[0]
    assert same in errors[0].params["fields"] or same == "both"
    assert counts(app)["Customer"] == 1 and counts(app)["License"] == 1
