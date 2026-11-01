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
