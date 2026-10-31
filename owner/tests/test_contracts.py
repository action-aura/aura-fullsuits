"""New customer flow, slice S5 (CT1): the e-contract -- versions, publishing, and the
append-only acceptance evidence, for both audiences (CUSTOMER and SELLER).

Each guard is proven by what was or was not written (rows, files, the DB refusing a
direct statement), and each has its allow twin: a lawyer-approved version DOES
publish, a manager-less Super Admin DOES export, the seller of the right audience
DOES accept.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff
from tests.test_account_activation import (  # noqa: F401 -- world is a fixture
    CONTRACT_BODY, NEW_PASSWORD, accept, change, make_device_keypair, sha, sign_in_ok, world,
)
from tests.test_customer_onboarding import _world, kyc  # noqa: F401 -- kyc is a fixture

PDF = b"%PDF-1.4\n% the lawyer's signed copy\n" + b"x" * 200


def _create(app, admin, **over):
    from app.contracts import service

    args = dict(audience="CUSTOMER", language="en", version_label=f"v-{uuid.uuid4().hex[:5]}", title="Terms",
                body="Line one\nLine two", lawyer_approved=True, approval_note="Counsel, 2026-11-01",
                actor_staff_user_id=admin)
    args.update(over)
    with app.app_context():
        return str(service.create_version(**args).id)


def _admin(app, client, email="ctadmin@example.com"):
    admin = make_staff(app, email, super_admin=True, mfa=True)
    login_and_verify_mfa(client, email)
    return admin


def _version(app, version_id):
    from app.contracts import service

    with app.app_context():
        from app.extensions import db_session

        db_session.expire_all()
        return service.get_version(version_id)


# ------------------------------------------------------------------- versions

def test_a_version_hashes_its_text_and_the_database_re_checks_it(app, seeded):
    admin = make_staff(app, "a1@example.com", super_admin=True)
    version_id = _create(app, admin, body="Hello\r\nWorld\r\n")
    with app.app_context():
        from app.contracts import service
        from app.extensions import db_session

        version = service.get_version(version_id)
        assert version.body == "Hello\nWorld"  # one line-ending convention, so the app and Owner hash the same bytes
        assert version.body_sha256 == hashlib.sha256("Hello\nWorld".encode("utf-8")).hexdigest()
        # the DB CHECK: a row whose hash is not the hash of its text cannot exist
        with pytest.raises(IntegrityError):
            db_session.execute(text(
                "INSERT INTO owner_contract_versions (id, audience, language, version_label, title, body, body_sha256, lawyer_approved) "
                "VALUES (gen_random_uuid(), 'CUSTOMER', 'en', 'forged', 't', 'abc', :h, false)"), {"h": "0" * 64})
        db_session.rollback()


def test_the_text_of_a_version_can_never_change_but_its_state_can(app, seeded):
    admin = make_staff(app, "a2@example.com", super_admin=True)
    version_id = _create(app, admin, lawyer_approved=False, approval_note="")
    with app.app_context():
        from app.contracts import service
        from app.extensions import db_session

        for column, value in (("body", "edited text"), ("audience", "SELLER"), ("language", "ar")):
            with pytest.raises(DBAPIError):
                db_session.execute(text(f"UPDATE owner_contract_versions SET {column} = :v WHERE id = :i"),
                                   {"v": value, "i": version_id})
            db_session.rollback()
        service.approve(service.get_version(version_id), note="Counsel read it", actor_staff_user_id=admin)  # allow twin
        assert service.get_version(version_id).lawyer_approved is True


def test_only_a_lawyer_approved_version_can_be_published_unless_the_practice_switch_is_on(app, seeded):
    from app.contracts import service

    admin = make_staff(app, "a3@example.com", super_admin=True)
    draft_id = _create(app, admin, lawyer_approved=False, approval_note="")
    with app.app_context():
        draft = service.get_version(draft_id)
        assert app.config["CONTRACT_ALLOW_DRAFT"] is False  # the default of every server
        with pytest.raises(service.ContractError) as refused:
            service.publish(draft, actor_staff_user_id=admin)
        assert refused.value.code == "NOT_LAWYER_APPROVED"
        assert service.get_version(draft_id).published_at is None  # nothing was published
        assert service.live_version("CUSTOMER", "en") is None
        # allow twin 1: once a lawyer approved THIS text it publishes
        service.approve(draft, note="Counsel read it", actor_staff_user_id=admin)
        service.publish(draft, actor_staff_user_id=admin)
        assert service.live_version("CUSTOMER", "en").id == draft.id
    # allow twin 2: the practice switch lets an unapproved draft go live (and the sign-in answer says so)
    other_id = _create(app, admin, language="ar", lawyer_approved=False, approval_note="")
    app.config.update(CONTRACT_ALLOW_DRAFT=True)
    with app.app_context():
        service.publish(service.get_version(other_id), actor_staff_user_id=admin)
        live = service.live_version("CUSTOMER", "ar")
        assert live.id and live.lawyer_approved is False


def test_publishing_replaces_the_live_version_and_only_one_is_ever_live(app, seeded):
    from app.contracts import service

    admin = make_staff(app, "a4@example.com", super_admin=True)
    first, second, other_language = _create(app, admin), _create(app, admin), _create(app, admin, language="ar")
    with app.app_context():
        service.publish(service.get_version(first), actor_staff_user_id=admin)
        service.publish(service.get_version(other_language), actor_staff_user_id=admin)
        service.publish(service.get_version(second), actor_staff_user_id=admin)
        assert service.get_version(first).retired_at is not None
        assert service.live_version("CUSTOMER", "en").id == uuid.UUID(second)
        assert service.live_version("CUSTOMER", "ar").id == uuid.UUID(other_language)  # another language is untouched
        with pytest.raises(service.ContractError) as again:
            service.publish(service.get_version(second), actor_staff_user_id=admin)
        assert again.value.code == "ALREADY_PUBLISHED"
        # the database also refuses two live versions of one audience + language
        from app.extensions import db_session

        with pytest.raises(IntegrityError):
            db_session.execute(text("UPDATE owner_contract_versions SET retired_at = NULL WHERE id = :i"), {"i": first})
        db_session.rollback()


def test_a_pdf_is_stored_encrypted_and_comes_back_byte_for_byte(app, seeded, kyc):
    from app.contracts import service

    admin = make_staff(app, "a5@example.com", super_admin=True)
    version_id = _create(app, admin, pdf_bytes=PDF)
    with app.app_context():
        version = service.get_version(version_id)
        assert version.pdf_sha256 == hashlib.sha256(PDF).hexdigest() and version.pdf_size_bytes == len(PDF)
        assert service.read_pdf(version) == PDF
        blob = open(os.path.join(app.config["KYC_DIRECTORY"], "contracts", version.pdf_storage_key), "rb").read()
        assert b"%PDF-" not in blob and b"signed copy" not in blob  # ciphertext on disk
        with pytest.raises(service.ContractError) as bad:
            service.create_version(audience="CUSTOMER", language="en", version_label="x1", title="t", body="b",
                                   lawyer_approved=False, pdf_bytes=b"not a pdf", actor_staff_user_id=admin)
        assert bad.value.code == "PDF_REQUIRED"


# ---------------------------------------------------------------- acceptances

def test_acceptance_rows_are_append_only(app, client, world):
    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY)).status_code == 201
    with app.app_context():
        from app.extensions import db_session
        from app.models.contracts import ContractAcceptance

        assert db_session.execute(select(func.count()).select_from(ContractAcceptance)).scalar_one() == 1
        for statement in ("UPDATE owner_contract_acceptances SET email_snapshot = 'x@y.z'", "DELETE FROM owner_contract_acceptances"):
            with pytest.raises(DBAPIError):
                db_session.execute(text(statement))
            db_session.rollback()
        assert db_session.execute(select(func.count()).select_from(ContractAcceptance)).scalar_one() == 1


def test_an_acceptance_must_name_exactly_the_person_its_audience_says(app, client, world):
    """The database refuses a customer acceptance that names a staff member, a seller
    acceptance that names a customer, one that names both, and one that names nobody."""
    insert = text(
        "INSERT INTO owner_contract_acceptances (id, contract_version_id, audience, customer_id, staff_user_id, "
        "email_snapshot, language, text_sha256, accepted_at) VALUES (gen_random_uuid(), :v, :a, :c, :s, 'e@x.y', 'ar', 'h', now())")
    with app.app_context():
        from app.extensions import db_session

        base = {"v": world["version_id"], "c": world["customer_id"], "s": world["admin"]}
        bad = [("CUSTOMER", base["c"], base["s"]), ("SELLER", base["c"], base["s"]), ("CUSTOMER", None, base["s"]),
               ("SELLER", base["c"], None), ("CUSTOMER", None, None), ("SELLER", None, None)]
        for audience, customer_id, staff_id in bad:
            with pytest.raises(IntegrityError):
                db_session.execute(insert, {"v": base["v"], "a": audience, "c": customer_id, "s": staff_id})
            db_session.rollback()
        # allow twins: exactly the right person is accepted
        db_session.execute(insert, {"v": base["v"], "a": "CUSTOMER", "c": base["c"], "s": None})
        db_session.execute(insert, {"v": base["v"], "a": "SELLER", "c": None, "s": base["s"]})
        db_session.commit()


def test_audience_is_enforced_by_the_service_in_both_directions(app, seeded):
    from app.contracts import service

    admin = make_staff(app, "a6@example.com", super_admin=True)
    customer_v = _create(app, admin, audience="CUSTOMER")
    seller_v = _create(app, admin, audience="SELLER")
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        for version_id in (customer_v, seller_v):
            service.publish(service.get_version(version_id), actor_staff_user_id=admin)
        staff = db_session.get(StaffUser, admin)

        class _Account:  # only the attributes record_customer_acceptance reads
            customer_id, id, email = uuid.uuid4(), uuid.uuid4(), "c@x.example"

        with pytest.raises(service.ContractError) as wrong:
            service.record_customer_acceptance(
                account=_Account, version=service.get_version(seller_v), text_sha256=service.get_version(seller_v).body_sha256,
                language="en", device_key_fingerprint="f" * 64, device_signature="s", ip="1.2.3.4", app_version="1",
                installation_label=None)
        assert wrong.value.code == "WRONG_AUDIENCE"
        with pytest.raises(service.ContractError) as wrong2:
            service.record_seller_acceptance(staff=staff, version=service.get_version(customer_v), ip="1.2.3.4")
        assert wrong2.value.code == "WRONG_AUDIENCE"
        from app.models.contracts import ContractAcceptance

        assert db_session.execute(select(func.count()).select_from(ContractAcceptance)).scalar_one() == 0
        service.record_seller_acceptance(staff=staff, version=service.get_version(seller_v), ip="1.2.3.4")  # allow twin
        assert db_session.execute(select(func.count()).select_from(ContractAcceptance)).scalar_one() == 1


# ---------------------------------------------------------------- the screens

def test_the_admin_page_is_super_admin_only_and_creates_a_draft(app, client, seeded, kyc):
    seller = make_staff(app, "ctseller@example.com", role_codes=["SALES"])
    force_login(client, app, seller)
    assert client.get("/contracts").status_code == 403
    assert client.get("/contracts/new").status_code in (403, 405)
    admin_client = app.test_client()
    _admin(app, admin_client)
    page = admin_client.get("/contracts")
    assert page.status_code == 200
    resp = admin_client.post("/contracts/new", data={
        "csrf_token": get_csrf(page.get_data(as_text=True)), "audience": "CUSTOMER", "language": "ar", "version_label": "2026-11 v1",
        "title": "عقد", "body": "نص العقد\r\nالبند الثاني", "approval_note": "", "pdf": (io.BytesIO(PDF), "c.pdf", "application/pdf"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    from app.contracts import service

    with app.app_context():
        (version,) = service.list_versions()
        assert version.published_at is None and version.lawyer_approved is False and version.pdf_storage_key
        assert version.body == "نص العقد\nالبند الثاني"
        vid = str(version.id)
    pdf = admin_client.get(f"/contracts/{vid}/pdf")
    assert pdf.status_code == 200 and pdf.data == PDF and pdf.headers["Cache-Control"] == "no-store"
    # publishing an unapproved version from the screen is refused and changes nothing
    detail = admin_client.get(f"/contracts/{vid}")
    token = get_csrf(detail.get_data(as_text=True))
    refused = admin_client.post(f"/contracts/{vid}/publish", data={"csrf_token": token})
    assert refused.status_code == 302
    assert _version(app, vid).published_at is None


def test_the_seller_reads_and_accepts_the_seller_agreement_and_it_is_exportable(app, client, seeded):
    from app.contracts import service

    admin = make_staff(app, "ctadmin2@example.com", super_admin=True, mfa=True)
    seller = make_staff(app, "ctseller2@example.com", role_codes=["SALES"])
    version_id = _create(app, admin, audience="SELLER", language="en", body="You agree to sell honestly.")
    with app.app_context():
        service.publish(service.get_version(version_id), actor_staff_user_id=admin)
    force_login(client, app, seller)
    page = client.get("/contracts/seller")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "You agree to sell honestly." in html
    token = get_csrf(html)
    digest = hashlib.sha256(b"You agree to sell honestly.").hexdigest()
    no_tick = client.post("/contracts/seller/accept", data={"csrf_token": token, "version_id": version_id, "text_sha256": digest})
    wrong_text = client.post("/contracts/seller/accept", data={"csrf_token": token, "version_id": version_id, "agree": "on",
                                                              "text_sha256": "0" * 64})
    with app.app_context():
        assert service.evidence_for_seller(seller)["acceptances"] == []  # neither refusal wrote anything
    ok = client.post("/contracts/seller/accept", data={"csrf_token": token, "version_id": version_id, "agree": "on", "text_sha256": digest})
    assert (no_tick.status_code, wrong_text.status_code, ok.status_code) == (302, 302, 302)
    again = client.post("/contracts/seller/accept", data={"csrf_token": token, "version_id": version_id, "agree": "on", "text_sha256": digest})
    assert again.status_code == 302
    with app.app_context():
        evidence = service.evidence_for_seller(seller)
    (row,) = evidence["acceptances"]
    assert row["audience"] == "SELLER" and row["staff_user_id"] == str(seller) and row["text_sha256"] == digest
    assert row["contract"]["version_id"] == version_id and evidence["format"] == "aura-contract-evidence-v1"
    # the export route: Super Admin only, JSON, audited
    assert client.get(f"/contracts/evidence/seller/{seller}.json").status_code == 403
    admin_client = app.test_client()
    login_and_verify_mfa(admin_client, "ctadmin2@example.com")
    export = admin_client.get(f"/contracts/evidence/seller/{seller}.json")
    assert export.status_code == 200 and export.mimetype == "application/json" and export.headers["Cache-Control"] == "no-store"
    assert json.loads(export.data)["acceptances"][0]["text_sha256"] == digest


def test_the_customer_evidence_export_names_the_text_the_device_and_the_signature(app, client, world):
    key = make_device_keypair()
    first = sign_in_ok(client, key, world)
    ticket = change(client, key, first["ticket"], NEW_PASSWORD).get_json()["ticket"]
    assert accept(client, key, ticket, world["version_id"], sha(CONTRACT_BODY)).status_code == 201
    admin_client = app.test_client()
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, world["admin"]).email
    login_and_verify_mfa(admin_client, email)
    export = admin_client.get(f"/contracts/evidence/customer/{world['customer_id']}.json")
    assert export.status_code == 200
    body = json.loads(export.data)
    (row,) = body["acceptances"]
    assert row["email"] == world["email"] and row["customer_id"] == str(world["customer_id"])
    assert row["text_sha256"] == sha(CONTRACT_BODY) == row["contract"]["sha256"]
    assert row["device_key_fingerprint"] and row["device_signature"] and row["ip"] == "127.0.0.1"
    assert row["contract"]["lawyer_approved"] is True
    page = admin_client.get(f"/contracts/{world['version_id']}")  # the page links to each person's evidence (the doorway)
    assert page.status_code == 200 and f"/contracts/evidence/customer/{world['customer_id']}.json" in page.get_data(as_text=True)
    per_version = admin_client.get(f"/contracts/{world['version_id']}/acceptances.json")
    assert len(json.loads(per_version.data)["acceptances"]) == 1
    other = admin_client.get(f"/contracts/evidence/customer/{uuid.uuid4()}.json")
    assert other.status_code == 200 and json.loads(other.data)["acceptances"] == []  # nobody else's evidence leaks in
