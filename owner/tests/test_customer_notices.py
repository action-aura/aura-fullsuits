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

from tests.conftest import force_login, get_csrf, make_device_keypair, make_staff
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
