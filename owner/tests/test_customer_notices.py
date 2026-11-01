"""feat/customer-notices (legal review v2, section (d)): the notices and consents the
shop agreement and the privacy notice promise.

  N1  every contract acceptance (customer AND seller) is mailed back to the person
  N2  the customer's two separate consents at the acceptance step
  N3  payment receipts to the shop
  N4  retention: documents and trial-device marks
  N5  the seller's form: images only
  N6  the scheduled-ops script runs the jobs the contract promises

Everything goes through the REAL endpoints / services; every guard is proven by what it
WROTE (mail rows, consent rows, documents, audit rows) in both directions.
"""
from __future__ import annotations

import hashlib
import re
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, jordan_today, make_device_keypair, make_staff
from tests.test_account_activation import (  # noqa: F401 -- world is a fixture
    CONTRACT_BODY, NEW_PASSWORD, accept, change, count, fingerprint_of, post, ready_ticket, rows, sha, sign_in_ok, world,
)
from tests.test_customer_onboarding import kyc  # noqa: F401 -- fixture

INFO = "info@actionaura.me"


def _mails(app, kind=None, to=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        db_session.expire_all()
        stmt = select(EmailOutbox).order_by(EmailOutbox.created_at)
        if kind is not None:
            stmt = stmt.where(EmailOutbox.kind == kind)
        if to is not None:
            stmt = stmt.where(EmailOutbox.to_address == to)
        found = db_session.execute(stmt).scalars().all()
        db_session.expunge_all()
        return found


def _mail_on(app):
    app.config.update(SMTP_HOST="smtp.test", CUSTOMER_MAIL_FROM="", CUSTOMER_MAIL_REPLY_TO="")


# =====================================================================  N1 customer

def test_a_customer_acceptance_mails_the_customer_their_copy_once(app, client, world):
    from app.models.contracts import ContractAcceptance

    _mail_on(app)
    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    # a REFUSED acceptance (a hash of some other text) writes no evidence and sends no mail
    refused = accept(client, key, ticket, world["version_id"], sha("something else"))
    assert refused.status_code == 409
    assert count(app, ContractAcceptance) == 0 and _mails(app, "customer_contract_accepted") == []

    good = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), installation_id="local-inst-7")
    assert good.status_code == 201
    pressing_twice = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY))
    assert pressing_twice.status_code == 201 and count(app, ContractAcceptance) == 1

    mails = _mails(app, "customer_contract_accepted")
    assert len(mails) == 1, "one acceptance, one mail -- not one per press of Accept"
    mail = mails[0]
    assert mail.to_address == world["email"]
    (acceptance,) = rows(app, ContractAcceptance)
    amman = acceptance.accepted_at.astimezone(ZoneInfo("Asia/Amman")).strftime("%Y-%m-%d %H:%M")
    for needed in ("عقد الاستخدام", "v1", sha(CONTRACT_BODY), amman, "local-inst-7", CONTRACT_BODY,
                   f"reply to this email or write to {INFO} within 7 days", "Amman time"):
        assert needed in mail.body_text, needed
    assert mail.dedupe_key == f"customer_contract_accepted:{acceptance.id}"


def test_the_copy_follows_the_customers_language_and_is_always_both(app, client, world):
    _mail_on(app)
    key = make_device_keypair()
    ticket = ready_ticket(client, key, world)  # the world's customer was made with language ar
    (mail,) = _mails(app, "customer_contract_accepted")
    assert mail.subject == "نسختك من عقد Action Aura الذي وافقت عليه"
    assert mail.body_text.index("لقد وافقت") < mail.body_text.index("You accepted"), "Arabic block first, English below"
    assert "<pre" in mail.body_html and "dir=\"rtl\"" in mail.body_html
    assert "--" not in "".join(re.findall(r"[؀-ۿ][^\n]*", mail.body_text))


def test_an_english_customer_gets_english_first(app, client, world):
    _mail_on(app)
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount

        db_session.execute(select(CustomerAccount)).scalars().one().language = "en"
        db_session.commit()
    ready_ticket(client, make_device_keypair(), world)
    (mail,) = _mails(app, "customer_contract_accepted")
    assert mail.subject == "Your copy of the Action Aura contract you accepted"
    assert mail.body_text.index("You accepted") < mail.body_text.index("لقد وافقت")


def test_with_email_off_the_acceptance_still_works_and_nothing_is_queued(app, client, world):
    from app.models.contracts import ContractAcceptance

    app.config.update(SMTP_HOST="")
    ready_ticket(client, make_device_keypair(), world)
    assert count(app, ContractAcceptance) == 1 and _mails(app) == []


def test_the_same_acceptance_is_never_mailed_twice_even_if_asked_to(app, client, world):
    from app.mail import notices
    from app.models.contracts import ContractAcceptance, ContractVersion

    _mail_on(app)
    ready_ticket(client, make_device_keypair(), world)
    assert len(_mails(app, "customer_contract_accepted")) == 1
    with app.app_context():
        from app.extensions import db_session

        acceptance = db_session.execute(select(ContractAcceptance)).scalars().one()
        version = db_session.get(ContractVersion, acceptance.contract_version_id)
        again = notices.queue_contract_copy(
            acceptance, version, audience="CUSTOMER", to_address=acceptance.email_snapshot, lang="ar")
        assert again is False
    assert len(_mails(app, "customer_contract_accepted")) == 1


def test_the_database_itself_refuses_a_second_mail_for_one_event(app):
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        from app.extensions import db_session
        from app.mail import outbox

        outbox.enqueue(kind="customer_contract_accepted", to_address="a@example.com", subject="s", body_text="b", dedupe_key="k:1")
        db_session.commit()
        with pytest.raises(IntegrityError):
            outbox.enqueue(kind="customer_contract_accepted", to_address="a@example.com", subject="s", body_text="b", dedupe_key="k:1")
            db_session.commit()
        db_session.rollback()
        # NULL keys (every older kind) are not constrained
        outbox.enqueue(kind="test", to_address="a@example.com", subject="s", body_text="b")
        outbox.enqueue(kind="test", to_address="a@example.com", subject="s", body_text="b")
        db_session.commit()


# =====================================================================  N1 seller

SELLER_BODY = "You agree to sell honestly."


def _seller_world(app, seeded):
    from tests.test_seller_agreement_gate import _publish_seller_version

    admin = make_staff(app, "cn-admin@example.com", super_admin=True, mfa=True)
    seller = make_staff(app, "cn-seller@example.com", role_codes=["SALES"])
    version_id = _publish_seller_version(app, admin, body=SELLER_BODY)
    client = app.test_client()
    force_login(client, app, seller)
    return {"admin": admin, "seller": seller, "version_id": version_id, "client": client}


def _seller_accept(client, version_id, body=SELLER_BODY):
    page = client.get("/contracts/seller")
    token = get_csrf(page.get_data(as_text=True))
    return client.post("/contracts/seller/accept", data={
        "csrf_token": token, "version_id": version_id, "agree": "on",
        "text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()})


def test_a_seller_acceptance_mails_the_seller_their_copy_with_a_download_link(app, seeded):
    _mail_on(app)
    w = _seller_world(app, seeded)
    # refused: the hash is of other words -> no evidence, no mail
    bad = _seller_accept(w["client"], w["version_id"], body="other words")
    assert bad.status_code in (302, 303) and _mails(app, "seller_contract_accepted") == []
    assert _seller_accept(w["client"], w["version_id"]).status_code in (302, 303)
    assert _seller_accept(w["client"], w["version_id"]).status_code in (302, 303)  # pressed twice
    mails = _mails(app, "seller_contract_accepted")
    assert len(mails) == 1
    mail = mails[0]
    assert mail.to_address == "cn-seller@example.com"
    for needed in (SELLER_BODY, hashlib.sha256(SELLER_BODY.encode()).hexdigest(), "Seller agreement",
                   f"/contracts/seller/copy/{w['version_id']}", f"write to {INFO} within 7 days", "Amman time"):
        assert needed in mail.body_text, needed


def test_a_seller_in_a_non_request_path_still_gets_the_mail_without_a_link(app, seeded):
    """record_seller_acceptance called with no request (a script) cannot build a link;
    the copy is in the body anyway."""
    from app.contracts import service

    _mail_on(app)
    w = _seller_world(app, seeded)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, w["seller"])
        version = service.get_version(w["version_id"])
        service.record_seller_acceptance(staff=staff, version=version, ip="10.0.0.9")
    (mail,) = _mails(app, "seller_contract_accepted")
    assert SELLER_BODY in mail.body_text and "/contracts/seller/copy/" not in mail.body_text
    assert "10.0.0.9" in mail.body_text


# =====================================================================  N2 consents

def _ready_for_acceptance(client, key, world):
    first = sign_in_ok(client, key, world)
    return change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]


def _live_documents(app, customer_id):
    with app.app_context():
        from app.customer_onboarding import documents
        from app.extensions import db_session

        db_session.expire_all()
        docs = documents.documents_of(customer_id)
        db_session.expunge_all()
        return docs


def _blob_exists(app, doc):
    import os

    with app.app_context():
        from app.customer_onboarding import documents

        return os.path.isfile(documents._path_for(doc))


def _audit_count(app, action_code):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        return db_session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.action_code == action_code)
        ).scalar_one()


def _customer_flag(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customers import Customer

        db_session.expire_all()
        return db_session.get(Customer, customer_id).manual_id_verification_required_at


def _consent_rows(app):
    from app.models.contracts import ContractConsent

    return rows(app, ContractConsent)


BOTH = {"id_documents": True, "data_outside_jordan": True}


@pytest.mark.parametrize("consents", [
    {"id_documents": True},                                # the key is missing
    {"id_documents": True, "data_outside_jordan": False},  # explicitly refused
    {},                                                    # present but empty
])
def test_without_consent_to_host_outside_jordan_the_acceptance_is_refused_and_nothing_is_written(app, client, world, consents):
    from app.models.contracts import ContractAcceptance

    _mail_on(app)
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    docs_before = {d.id for d in _live_documents(app, world["customer_id"])}
    assert len(docs_before) == 2, "the seller's form stored the ID and the registration"

    refused = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents=consents)
    body = refused.get_json()
    assert refused.status_code == 409 and body["reason_code"] == "CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED"
    assert body["result"] == "FAILURE" and body["decision"] == "REJECTED"
    # the check RAN and stopped everything: no acceptance, no consent, no mail, no deleted photo, no flag
    assert count(app, ContractAcceptance) == 0 and _consent_rows(app) == []
    assert _mails(app, "customer_contract_accepted") == []
    assert {d.id for d in _live_documents(app, world["customer_id"])} == docs_before
    assert _customer_flag(app, world["customer_id"]) is None
    assert _audit_count(app, "CONTRACT_ACCEPTED_BY_CUSTOMER") == 0
    # and the gate still holds: the shop cannot activate on a refused acceptance
    from tests.test_account_activation import activate, reason

    assert reason(activate(client, key, ticket=ticket, license_public_id=world["licence_id"])) == "CONTRACT_ACCEPTANCE_REQUIRED"


def test_with_both_consents_the_acceptance_stands_and_both_answers_are_stored(app, client, world):
    from app.models.contracts import ContractAcceptance

    _mail_on(app)
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    ok = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents=BOTH, installation_id="inst-c1")
    assert ok.status_code == 201 and ok.get_json()["accepted"] is True
    (acceptance,) = rows(app, ContractAcceptance)
    stored = {c.kind: c for c in _consent_rows(app)}
    assert set(stored) == {"id_documents", "data_outside_jordan"}
    assert all(c.given is True and c.acceptance_id == acceptance.id and c.installation_id == "inst-c1"
               and str(c.customer_id) == str(world["customer_id"]) and c.given_at == acceptance.accepted_at
               for c in stored.values())
    docs = _live_documents(app, world["customer_id"])
    assert len(docs) == 2 and all(_blob_exists(app, d) for d in docs), "agreeing deletes nothing"
    assert _customer_flag(app, world["customer_id"]) is None
    assert _audit_count(app, "CONTRACT_CONSENTS_RECORDED") == 1
    assert _audit_count(app, "CUSTOMER_ID_DOCUMENTS_DELETED_ON_REFUSAL") == 0
    (mail,) = _mails(app, "customer_contract_accepted")
    assert "keep a photo of your ID and commercial registration: yes" in mail.body_text


def test_refusing_to_keep_the_id_photos_keeps_the_acceptance_deletes_the_photos_and_flags_the_customer(app, client, world):
    from app.models.contracts import ContractAcceptance
    from tests.test_account_activation import activate

    _mail_on(app)
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    before = _live_documents(app, world["customer_id"])
    assert {d.kind for d in before} == {"NATIONAL_ID", "COMMERCIAL_REGISTRATION"}
    assert all(_blob_exists(app, d) for d in before)

    ok = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY),
                consents={"id_documents": False, "data_outside_jordan": True})
    assert ok.status_code == 201, "the acceptance stands"
    assert count(app, ContractAcceptance) == 1
    stored = {c.kind: c.given for c in _consent_rows(app)}
    assert stored == {"id_documents": False, "data_outside_jordan": True}
    # the photos are really gone: the rows say purged, the files are off the disk
    assert _live_documents(app, world["customer_id"]) == []
    assert not any(_blob_exists(app, d) for d in before), "secure delete: the encrypted files are removed"
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_onboarding import CustomerDocument

        purged = db_session.execute(select(CustomerDocument)).scalars().all()
        assert len(purged) == 2 and all(d.purged_at is not None for d in purged)
    assert _customer_flag(app, world["customer_id"]) is not None, "flagged: ID verification by hand needed"
    assert _audit_count(app, "CUSTOMER_ID_DOCUMENTS_DELETED_ON_REFUSAL") == 1
    assert _audit_count(app, "CONTRACT_CONSENTS_RECORDED") == 1
    (mail,) = _mails(app, "customer_contract_accepted")
    assert "keep a photo of your ID and commercial registration: no" in mail.body_text
    # allow-half: refusing the photos does NOT stop the service
    assert activate(client, key, ticket=ticket, license_public_id=world["licence_id"]).status_code == 200


def test_without_a_consents_object_nothing_is_recorded_and_nothing_changes(app, client, world):
    from app.models.contracts import ContractAcceptance

    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY)).status_code == 201
    assert count(app, ContractAcceptance) == 1 and _consent_rows(app) == []
    assert len(_live_documents(app, world["customer_id"])) == 2
    assert _customer_flag(app, world["customer_id"]) is None
    assert _audit_count(app, "CONTRACT_CONSENTS_RECORDED") == 0
    evidence = rows(app, ContractAcceptance)
    with app.app_context():
        from app.contracts import service as contracts

        (entry,) = contracts.evidence_for_customer(world["customer_id"])["acceptances"]
        assert entry["consents"] == [], "recorded nothing is visible as an empty list, not as 'refused'"
    assert len(evidence) == 1


def test_consents_null_counts_as_absent_and_a_malformed_value_is_a_400_that_writes_nothing(app, client, world):
    from app.models.contracts import ContractAcceptance

    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    for bad in ("yes", ["data_outside_jordan"], {"data_outside_jordan": "true"}, {"data_outside_jordan": 1},
                {"data_outside_jordan": True, "id_documents": "false"}):
        resp = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents=bad)
        assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_REQUEST", bad
    assert count(app, ContractAcceptance) == 0 and _consent_rows(app) == []
    assert len(_live_documents(app, world["customer_id"])) == 2
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents=None).status_code == 201
    assert _consent_rows(app) == []


def test_a_repeat_cannot_change_what_was_recorded_or_delete_photos_later(app, client, world):
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents=BOTH).status_code == 201
    # a second press that now says "do not keep my photos" is an idempotent repeat: it records
    # nothing new and deletes nothing (the standing answer is the first one)
    again = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY),
                   consents={"id_documents": False, "data_outside_jordan": True})
    assert again.status_code == 201
    assert len(_consent_rows(app)) == 2 and all(c.given for c in _consent_rows(app))
    assert len(_live_documents(app, world["customer_id"])) == 2
    assert _customer_flag(app, world["customer_id"]) is None
    # but a repeat that withdraws the Jordan consent is still refused, and changes nothing
    refused = accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents={"data_outside_jordan": False})
    assert refused.status_code == 409 and refused.get_json()["reason_code"] == "CONSENT_DATA_OUTSIDE_JORDAN_REQUIRED"
    assert len(_consent_rows(app)) == 2 and all(c.given for c in _consent_rows(app))


def test_consents_appear_in_the_contract_evidence_json_and_the_database_keeps_them_append_only(app, client, world):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY),
           consents={"id_documents": False, "data_outside_jordan": True})
    with app.app_context():
        from app.contracts import service as contracts
        from app.extensions import db_session

        (entry,) = contracts.evidence_for_customer(world["customer_id"])["acceptances"]
        by_kind = {c["kind"]: c for c in entry["consents"]}
        assert by_kind["id_documents"]["given"] is False and by_kind["data_outside_jordan"]["given"] is True
        assert by_kind["id_documents"]["given_at"] == entry["accepted_at"]
        with pytest.raises(DBAPIError):
            db_session.execute(text("UPDATE owner_contract_consents SET given = true"))
            db_session.commit()
        db_session.rollback()
        with pytest.raises(DBAPIError):
            db_session.execute(text("DELETE FROM owner_contract_consents"))
            db_session.commit()
        db_session.rollback()


def test_staff_see_the_consents_and_the_flag_and_only_the_right_people_can_clear_it(app, client, world):
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY),
           consents={"id_documents": False, "data_outside_jordan": True})
    # deny-half first (the flag is still up): a plain seller has no customer_documents.view
    seller = make_staff(app, "cn-plain-seller@example.com", role_codes=["SALES"])
    other = app.test_client()
    force_login(other, app, seller)
    token = get_csrf(other.get("/customers").get_data(as_text=True))
    denied = other.post(f"/customers/{world['customer_id']}/id-verified", data={"csrf_token": token})
    assert denied.status_code == 403
    assert _customer_flag(app, world["customer_id"]) is not None and _audit_count(app, "CUSTOMER_ID_VERIFIED_BY_HAND") == 0

    staff = app.test_client()
    force_login(staff, app, world["admin"])
    page = staff.get(f"/customers/{world['customer_id']}")
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert 'data-id-verification-flag="1"' in html and 'data-consent-kind="id_documents"' in html
    done = staff.post(f"/customers/{world['customer_id']}/id-verified", data={"csrf_token": get_csrf(html)})
    assert done.status_code in (302, 303)
    assert _customer_flag(app, world["customer_id"]) is None
    assert _audit_count(app, "CUSTOMER_ID_VERIFIED_BY_HAND") == 1
    html = staff.get(f"/customers/{world['customer_id']}").get_data(as_text=True)
    assert 'data-id-verification-flag="1"' not in html and 'data-consent-kind="id_documents"' in html,         "the flag is gone, the recorded answers stay"


# =====================================================================  N3 payment receipts

def _record_payment(app, w, *, customer_id=None, amount="125.50", method="CLIQ", reference="CLQ-9981", status="PENDING"):
    from decimal import Decimal

    from app.subscriptions.services import record_payment

    with app.app_context():
        payment = record_payment(
            {"customer_id": customer_id or w["customer_id"], "amount": Decimal(amount), "currency": "JOD",
             "method": method, "payment_date": jordan_today(), "status": status, "reference": reference},
            w["seller"])
        return payment.id


def _correct(app, payment_id, status, actor, note="checked"):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord
    from app.subscriptions.services import correct_payment

    with app.app_context():
        correct_payment(db_session.get(PaymentRecord, payment_id), status, note, actor)


TAX_EN = "Prices include 16% general sales tax"
TAX_AR = "الأسعار شاملة ضريبة المبيعات العامة 16%"


def test_a_recorded_payment_is_mailed_to_the_shop_as_received_and_waiting(app, client, world):
    _mail_on(app)
    pid = _record_payment(app, world)
    (mail,) = _mails(app, "customer_payment_pending")
    assert mail.to_address == world["email"] and mail.dedupe_key == f"customer_payment_pending:{pid}"
    for needed in ("125.50", "JOD", "CLIQ", "CLQ-9981", f"PAY-{str(pid)[:8].upper()}", jordan_today().isoformat(),
                   "received by our seller, waiting for confirmation", TAX_EN, TAX_AR, "info@actionaura.me"):
        assert needed in mail.body_text, needed
    assert _mails(app, "customer_payment_confirmed") == [] and _mails(app, "customer_payment_rejected") == []


def test_confirming_and_rejecting_each_mail_the_shop_once(app, client, world):
    _mail_on(app)
    pid = _record_payment(app, world)
    _correct(app, pid, "CONFIRMED", world["manager"])
    (confirmed,) = _mails(app, "customer_payment_confirmed")
    assert "Confirmed" in confirmed.body_text and TAX_EN in confirmed.body_text and "125.50" in confirmed.body_text
    # re-saving the same status (a note fix) is the same event: no second mail
    _correct(app, pid, "CONFIRMED", world["manager"], note="note fix")
    assert len(_mails(app, "customer_payment_confirmed")) == 1

    other = _record_payment(app, world, reference="CLQ-2")
    _correct(app, other, "FAILED", world["manager"], note="bounced")
    (rejected,) = _mails(app, "customer_payment_rejected")
    assert "contact us" in rejected.body_text and rejected.dedupe_key == f"customer_payment_rejected:{other}"
    assert len(_mails(app, "customer_payment_pending")) == 2


def test_a_refused_correction_sends_nothing(app, client, world):
    """The recorder may not confirm their own payment: the refusal must not mail 'confirmed'."""
    from app.subscriptions.services import PaymentSelfVerificationError

    _mail_on(app)
    pid = _record_payment(app, world)
    with pytest.raises(PaymentSelfVerificationError):
        _correct(app, pid, "CONFIRMED", world["seller"])
    assert _mails(app, "customer_payment_confirmed") == []


def test_a_payment_for_a_customer_with_no_account_sends_nothing_and_does_not_fail(app, client, world):
    """Decision: the notice goes to the customer ACCOUNT's email. A customer record with no
    account (an internal / legacy payment) has nobody to write to: no mail, no error."""
    from app.extensions import db_session
    from app.models.customers import Customer

    _mail_on(app)
    with app.app_context():
        bare = Customer(legal_name="No Account Co")
        db_session.add(bare)
        db_session.commit()
        bare_id = bare.id
    pid = _record_payment(app, world, customer_id=bare_id)
    _correct(app, pid, "CONFIRMED", world["manager"])
    assert _mails(app) == []


def test_with_email_off_payments_still_record_and_confirm(app, client, world):
    app.config.update(SMTP_HOST="")
    pid = _record_payment(app, world)
    _correct(app, pid, "CONFIRMED", world["manager"])
    assert _mails(app) == []


def test_voided_and_refunded_payments_are_not_mailed(app, client, world):
    """Only recorded / confirmed / rejected are the shop's receipts (decision, stated in the plan)."""
    _mail_on(app)
    pid = _record_payment(app, world)
    _correct(app, pid, "VOIDED", world["manager"], note="duplicate entry")
    assert [m.kind for m in _mails(app)] == ["customer_payment_pending"]


# =====================================================================  N4 retention

def _paid_customer(app, w, *, status, end_offset_days, **onboard_overrides):
    """A trial customer turned into a PAID one by hand: pilot CONVERTED, documents kept for
    life (purge_after cleared, exactly what fulfilment does), subscription in `status` with
    its paid-until `end_offset_days` from Amman today. Returns (customer_id, subscription_id)."""
    from datetime import timedelta

    from app.customer_onboarding import documents, trial
    from app.extensions import db_session
    from app.models.subscriptions import Subscription
    from tests.test_customer_onboarding import onboard

    result = onboard(app, w, **onboard_overrides)
    with app.app_context():
        pilot = trial.trial_pilot_for_customer(result.customer_id)
        pilot.status = "CONVERTED"
        sub = db_session.get(Subscription, pilot.subscription_id)
        sub.status = status
        sub.end_date = jordan_today() + timedelta(days=end_offset_days)
        documents.keep_for_customer_lifetime(result.customer_id)
        db_session.commit()
        return result.customer_id, sub.id


def _doc_dates(app, customer_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_onboarding import CustomerDocument

        db_session.expire_all()
        return {
            d.kind: (d.purge_after, d.purged_at)
            for d in db_session.execute(select(CustomerDocument).where(CustomerDocument.customer_id == customer_id)).scalars()
        }


def _set_sub(app, sub_id, **fields):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.get(Subscription, sub_id)
        for k, v in fields.items():
            setattr(sub, k, v)
        db_session.commit()


def _sweep(app):
    with app.app_context():
        from app.customer_onboarding import retention

        return retention.apply_post_customer_retention()


def _purge_due(app):
    with app.app_context():
        from app.customer_onboarding import retention

        return retention.purge_due()


@pytest.fixture()
def shop(app, seeded, kyc):
    from tests.test_customer_onboarding import _world

    return _world(app)


def _expected_purge(end_date):
    from datetime import datetime, time, timedelta, timezone

    return datetime.combine(end_date, time.min, tzinfo=timezone.utc) + timedelta(days=365)


def test_a_lapsed_customers_photos_get_a_date_of_last_paid_day_plus_one_year(app, shop):
    from datetime import timedelta

    cid, sub_id = _paid_customer(app, shop, status="EXPIRED", end_offset_days=-30)
    assert all(p is None for p, _ in _doc_dates(app, cid).values()), "kept for life while a customer: no date at all"
    assert _sweep(app) == {"set": 1, "cleared": 0}
    expected = _expected_purge(jordan_today() - timedelta(days=30))
    assert {k: v[0] for k, v in _doc_dates(app, cid).items()} == {"NATIONAL_ID": expected, "COMMERCIAL_REGISTRATION": expected}
    assert _audit_count(app, "CUSTOMER_DOCUMENT_RETENTION_SET") == 1
    assert _sweep(app) == {"set": 0, "cleared": 0}, "idempotent: nothing moves on a second run"
    assert _audit_count(app, "CUSTOMER_DOCUMENT_RETENTION_SET") == 1


def test_cancelled_and_refunded_customers_start_the_clock_too(app, shop):
    from datetime import timedelta

    cancelled, c_sub = _paid_customer(app, shop, status="CANCELLED", end_offset_days=-10)
    refunded, r_sub = _paid_customer(app, shop, status="ACTIVE", end_offset_days=200)
    # a refund that stops the licence SUSPENDS the subscription (refunds.py): history row, status SUSPENDED
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import transition_subscription

        transition_subscription(db_session.get(Subscription, r_sub), "SUSPENDED", shop["admin"], reason="refund")
    _sweep(app)
    assert _doc_dates(app, cancelled)["NATIONAL_ID"][0] == _expected_purge(jordan_today() - timedelta(days=10))
    from datetime import datetime, timezone

    suspended_day = datetime.now(timezone.utc).date()
    assert _doc_dates(app, refunded)["NATIONAL_ID"][0] == _expected_purge(suspended_day)


def test_paying_or_renewing_clears_the_date_again(app, shop):
    cid, sub_id = _paid_customer(app, shop, status="EXPIRED", end_offset_days=-30)
    _sweep(app)
    assert _doc_dates(app, cid)["NATIONAL_ID"][0] is not None
    from datetime import timedelta

    _set_sub(app, sub_id, status="ACTIVE", end_date=jordan_today() + timedelta(days=365))  # renewed
    assert _sweep(app) == {"set": 0, "cleared": 1}
    assert all(p is None for p, _ in _doc_dates(app, cid).values())
    assert _audit_count(app, "CUSTOMER_DOCUMENT_RETENTION_CLEARED") == 1


def test_inside_the_grace_period_nothing_is_dated_and_an_unpaid_trial_is_left_to_the_trial_rules(app, shop):
    from datetime import timedelta

    from tests.test_customer_onboarding import onboard

    past_due, _ = _paid_customer(app, shop, status="PAST_DUE", end_offset_days=-3)
    trial_only = onboard(app, shop)
    before = _doc_dates(app, trial_only.customer_id)
    assert _sweep(app) == {"set": 0, "cleared": 0}
    assert all(p is None for p, _ in _doc_dates(app, past_due).values()), "PAST_DUE is still a customer"
    assert _doc_dates(app, trial_only.customer_id) == before, "an unpaid trial keeps the trial's own 90-day date"
    assert all(p is not None for p, _ in before.values())
    assert jordan_today() - timedelta(days=3)  # (PAST_DUE end date was in the past on purpose)


def test_purge_due_deletes_only_the_due_documents_and_unlinks_only_their_marks(app, shop):
    import os
    from datetime import timedelta

    from app.customer_onboarding import documents
    from app.models.customer_onboarding import TrialDeviceMark

    gone, g_sub = _paid_customer(app, shop, status="EXPIRED", end_offset_days=-400)       # +365 passed 35 days ago
    recent, r_sub = _paid_customer(app, shop, status="EXPIRED", end_offset_days=-30)      # due in ~335 days
    live, l_sub = _paid_customer(app, shop, status="ACTIVE", end_offset_days=100)
    with app.app_context():
        from app.extensions import db_session

        for n, cid in enumerate((gone, recent, live)):
            db_session.add(TrialDeviceMark(device_key_fingerprint=f"{n}" * 64, customer_id=cid))
        db_session.commit()
        files = {cid: [documents._path_for(d) for d in documents.documents_of(cid)] for cid in (gone, recent, live)}
        assert all(os.path.isfile(p) for paths in files.values() for p in paths)

    summary = _purge_due(app)
    assert summary["customer_documents_purged"] == 2 and summary["trial_marks_unlinked"] == 1
    assert not any(os.path.exists(p) for p in files[gone]), "the due customer's encrypted files are gone"
    assert all(os.path.isfile(p) for p in files[recent] + files[live]), "nobody else's files were touched"
    assert all(pa is not None for pa, _ in _doc_dates(app, gone).values())
    assert all(purged is not None for _, purged in _doc_dates(app, gone).values())
    assert all(purged is None for c in (recent, live) for _, purged in _doc_dates(app, c).values())
    with app.app_context():
        from app.extensions import db_session

        marks = {m.device_key_fingerprint: m.customer_id for m in db_session.execute(select(TrialDeviceMark)).scalars()}
        assert len(marks) == 3, "the device fingerprints are KEPT (fraud prevention)"
        assert marks["0" * 64] is None and marks["1" * 64] == recent and marks["2" * 64] == live
    assert _audit_count(app, "TRIAL_DEVICE_MARKS_UNLINKED") == 1
    again = _purge_due(app)
    assert again["customer_documents_purged"] == 0 and again["trial_marks_unlinked"] == 0, "idempotent"
    assert jordan_today() - timedelta(days=1)


def test_a_device_whose_customer_was_purged_still_blocks_a_new_trial_on_it(app, shop):
    """Fail-closed half of the unlink: a NULL-customer mark must refuse every customer, or
    unlinking would quietly re-open one-trial-per-device."""
    from app.customer_onboarding import trial
    from app.models.customer_onboarding import TrialDeviceMark
    from tests.test_customer_onboarding import onboard

    stranger = onboard(app, shop)
    with app.app_context():
        from app.extensions import db_session

        db_session.add(TrialDeviceMark(device_key_fingerprint="a" * 64, customer_id=None))
        db_session.commit()
        with pytest.raises(trial.TrialError) as refused:
            trial._mark_device(fingerprint="a" * 64, customer_id=stranger.customer_id, registry_device_id=None, installation_id=None)
        assert refused.value.code == "TRIAL_DEVICE_ALREADY_USED"
        db_session.rollback()
        # allow-half: an unrelated device is fine
        trial._mark_device(fingerprint="b" * 64, customer_id=stranger.customer_id, registry_device_id=None, installation_id=None)


def test_a_customer_who_only_refused_the_photos_keeps_the_device_link(app, client, world):
    """The refusal path purges the documents of a customer who is STILL a customer: the mark
    must stay linked (this is the guard on 'purged AND no longer a customer')."""
    from app.models.customer_onboarding import TrialDeviceMark

    _mail_on(app)
    key = make_device_keypair()
    ticket = _ready_for_acceptance(client, key, world)
    accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY), consents={"id_documents": False, "data_outside_jordan": True})
    with app.app_context():
        from app.extensions import db_session

        db_session.add(TrialDeviceMark(device_key_fingerprint="c" * 64, customer_id=world["customer_id"]))
        db_session.commit()
    # the world's customer is in a running trial (live), photos already refused-and-deleted
    assert _purge_due(app)["trial_marks_unlinked"] == 0
    with app.app_context():
        from app.extensions import db_session

        assert db_session.execute(select(TrialDeviceMark.customer_id)).scalar_one() == world["customer_id"]


def test_the_cli_runs_the_whole_nightly_purge(app, shop):
    import json

    cid, _ = _paid_customer(app, shop, status="EXPIRED", end_offset_days=-400)
    result = app.test_cli_runner().invoke(args=["documents", "purge-due"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["customer_documents_purged"] == 2
    assert all(purged is not None for _, purged in _doc_dates(app, cid).values())


# ---- the seller's own ID photo

def _seller_with_photo(app, email, *, disabled_days_ago=None):
    from datetime import datetime, timedelta, timezone

    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.seller_id import documents as seller_docs
    from tests.test_customer_onboarding import _jpeg

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        photo = seller_docs.store_identity_photo(content=_jpeg(), uploaded_by_staff_user_id=staff_id, staff_user_id=staff_id)
        if disabled_days_ago is not None:
            staff = db_session.get(StaffUser, staff_id)
            staff.is_active = False
            staff.disabled_at = datetime.now(timezone.utc) - timedelta(days=disabled_days_ago)
        db_session.commit()
        return staff_id, photo.id


def test_a_sellers_id_photo_goes_a_year_after_they_leave_and_only_then(app, seeded, kyc):
    import os
    from datetime import datetime, timedelta, timezone

    from app.models.staff_identity import StaffIdentityDocument
    from app.seller_id import documents as seller_docs

    active, active_photo = _seller_with_photo(app, "keeps-working@example.com")
    recent, recent_photo = _seller_with_photo(app, "left-last-month@example.com", disabled_days_ago=30)
    old, old_photo = _seller_with_photo(app, "left-long-ago@example.com", disabled_days_ago=400)
    with app.app_context():
        from app.extensions import db_session

        path_of = {pid: seller_docs.kyc.path_for_key(db_session.get(StaffIdentityDocument, pid).storage_key)
                   for pid in (active_photo, recent_photo, old_photo)}
    summary = _purge_due(app)
    assert summary["seller_photos_purged"] == 1
    assert not os.path.exists(path_of[old_photo]) and os.path.isfile(path_of[recent_photo]) and os.path.isfile(path_of[active_photo])
    with app.app_context():
        from app.extensions import db_session

        db_session.expire_all()
        rows = {r.id: r for r in db_session.execute(select(StaffIdentityDocument)).scalars()}
        assert rows[active_photo].purge_after is None and rows[active_photo].purged_at is None
        assert abs((rows[recent_photo].purge_after - (datetime.now(timezone.utc) - timedelta(days=30) + timedelta(days=365)))
                   .total_seconds()) < 3600 and rows[recent_photo].purged_at is None
        assert rows[old_photo].purged_at is not None
        assert seller_docs.current_photo(old) is None, "a purged photo no longer satisfies the ID-photo gate"
        assert seller_docs.current_photo(active) is not None
    assert _audit_count(app, "STAFF_ID_PHOTO_PURGED") == 1
    assert _purge_due(app)["seller_photos_purged"] == 0, "idempotent"


def test_re_enabling_a_seller_takes_the_date_away_again(app, seeded, kyc):
    from app.models.staff import StaffUser
    from app.models.staff_identity import StaffIdentityDocument

    staff_id, photo_id = _seller_with_photo(app, "came-back@example.com", disabled_days_ago=30)
    _purge_due(app)
    with app.app_context():
        from app.extensions import db_session

        assert db_session.get(StaffIdentityDocument, photo_id).purge_after is not None
        staff = db_session.get(StaffUser, staff_id)
        staff.is_active, staff.disabled_at = True, None
        db_session.commit()
    _purge_due(app)
    with app.app_context():
        from app.extensions import db_session

        db_session.expire_all()
        assert db_session.get(StaffIdentityDocument, photo_id).purge_after is None


# =====================================================================  N6 scheduled ops

def _live_script_lines():
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "deploy" / "oracle-free" / "run_scheduled_ops.sh"
    return [l.strip() for l in script.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


def test_the_nightly_script_runs_the_trial_fallback_and_the_document_purge(app):
    lines = _live_script_lines()
    assert any(l.startswith("run_job") and l.endswith("commercial start-overdue-trials") for l in lines), \
        "contract cl. 4 promises the 14-day trial fallback; it must be scheduled"
    assert any(l.startswith("run_job") and l.endswith("documents purge-due") for l in lines), \
        "the retention promise needs the purge to run"
    # the SYNC pruning decision is untouched: still a dry run
    assert not any("prune-events" in l and "--apply" in l for l in lines)


def test_the_two_new_commands_are_registered_and_take_no_required_arguments(app):
    for argv in (["commercial", "start-overdue-trials"], ["documents", "purge-due"]):
        group = app.cli
        for word in argv:
            group = group.commands[word]
        assert not [p for p in group.params if getattr(p, "required", False)]

# =====================================================================  N5 images only

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


@pytest.mark.parametrize("which", ["id_photo", "cr_photo"])
def test_a_pdf_is_refused_for_the_id_and_the_registration_and_nothing_is_created(app, shop, which):
    from app.customer_onboarding.service import OnboardingError
    from tests.test_customer_onboarding import counts, onboard

    before = counts(app)
    with pytest.raises(OnboardingError) as refused:
        onboard(app, shop, **{which: PDF_BYTES})
    assert refused.value.code == "DOCUMENT_TYPE_NOT_ALLOWED"
    assert counts(app) == before, "a refused form writes nothing at all"
    ok = onboard(app, shop)  # allow-half: the same form with two pictures works
    assert ok.customer_id is not None


def test_the_store_itself_refuses_a_pdf_for_an_id_but_the_payment_slip_may_still_be_a_pdf(app, seeded, kyc):
    from app.customer_onboarding import documents

    with app.app_context():
        with pytest.raises(documents.DocumentError) as refused:
            documents.clean_identity_file(PDF_BYTES)
        assert refused.value.code == "DOCUMENT_TYPE_NOT_ALLOWED"
        # the slip path uses the general cleaner: a PDF is still a valid slip
        assert documents.clean_file(PDF_BYTES)[1] == "application/pdf"
        with pytest.raises(documents.DocumentError):
            documents.store_document(customer_id=uuid_for_tests(), kind="NATIONAL_ID", content=PDF_BYTES, uploaded_by_staff_user_id=None)


def test_a_payment_slip_may_still_be_stored_as_a_pdf(app, shop):
    """Allow-half of N5: only the ID and the registration are picture-only."""
    from decimal import Decimal

    from app.customer_onboarding import documents
    from app.extensions import db_session
    from app.subscriptions.services import record_payment
    from tests.test_customer_onboarding import onboard

    customer = onboard(app, shop)
    with app.app_context():
        payment = record_payment(
            {"customer_id": customer.customer_id, "amount": Decimal("10.00"), "currency": "JOD", "method": "CLIQ",
             "payment_date": jordan_today(), "status": "PENDING"}, shop["seller"])
        slip = documents.store_document(
            customer_id=customer.customer_id, kind="PAYMENT_SLIP", content=PDF_BYTES, uploaded_by_staff_user_id=shop["seller"],
            payment_record_id=payment.id)
        db_session.commit()
        assert slip.content_type == "application/pdf"


def uuid_for_tests():
    import uuid

    return uuid.uuid4()


def test_the_seller_form_opens_the_camera_and_offers_only_pictures_for_the_id_and_registration(app, shop):
    app.config.update(CUSTOMER_ONBOARDING_ENABLED=True)
    client = app.test_client()
    force_login(client, app, shop["seller"])
    html = client.get("/customers/new-trial").get_data(as_text=True)
    inputs = {m.group(1): m.group(0) for m in re.finditer(r'<input[^>]*name="(id_photo|cr_photo)"[^>]*>', html)}
    assert set(inputs) == {"id_photo", "cr_photo"}
    for tag in inputs.values():
        assert 'accept="image/*"' in tag and 'capture="environment"' in tag and "pdf" not in tag, tag


def test_the_refusal_is_worded_in_english_and_arabic_without_a_pdf_promise(app):
    from app.i18n_labels import localize_onboarding_error

    from flask_babel import force_locale

    with app.test_request_context("/"):
        with force_locale("en"):
            english = localize_onboarding_error("DOCUMENT_TYPE_NOT_ALLOWED")
        with force_locale("ar"):
            arabic = localize_onboarding_error("DOCUMENT_TYPE_NOT_ALLOWED")
    assert "PDF" not in english and "JPEG" in english
    assert arabic != english and "--" not in arabic and "JPEG" in arabic and "PDF" not in arabic


