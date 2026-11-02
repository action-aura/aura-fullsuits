"""Personal-data requests (Alembic DR1): a customer asks for a copy of their data, or for it to be erased.

Every guard is proven by what it WROTE or did not write (request rows, audit rows, files on disk,
the customer's own fields), never by a status code alone, and each deny has its allow twin:

  permission     only privacy.requests opens any of it; a seller gets 403 and nothing is written
  log            due = received + 30 days exactly; a future date or a bad kind writes nothing
  overdue        due day itself is NOT late, the day after is (the dashboard tile counts the same way)
  export         the ZIP holds the records; the decrypted photos ONLY when the exporter may also open
                 ID photos (customer_documents.view); no secrets; audited; needs recent re-auth
  erase refused  ACTIVE / PAST_DUE / a running trial: nothing changes, request stays OPEN
  erase allowed  CANCELLED / an ended trial: the person goes, invoices + payments + slips + contract
                 acceptances + the device fingerprint stay, another customer is untouched
  close          an ERASE cannot be closed DONE by hand (service AND database), REFUSED needs a note
  resume         a file that cannot be deleted leaves the request OPEN; pressing again finishes it
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_customer_onboarding import _world, kyc, onboard  # noqa: F401 -- kyc is a fixture
from tests.test_customer_accounts_service import GOOD


# ------------------------------------------------------------------ helpers

def _world3(app):
    w = _world(app)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        role = Role(code="PRIV_ONLY", name="Privacy only", description="privacy.requests without ID photos", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in ("privacy.requests", "customers.view", "customers.view_all"):
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.commit()
    w["priv_only"] = make_staff(app, f"priv-{uuid.uuid4().hex[:6]}@example.com", role_codes=["PRIV_ONLY"])
    return w


def _grant_recent_auth(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        for s in db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars():
            s.mfa_verified_at = utcnow()
        db_session.commit()


def _client(app, staff_id, *, recent_auth=True):
    client = app.test_client()
    force_login(client, app, staff_id)
    if recent_auth:
        _grant_recent_auth(app, staff_id)
    return client


def _token(client, customer_id):
    return get_csrf(client.get(f"/customers/{customer_id}").get_data(as_text=True)) or get_csrf(client.get("/data-requests").get_data(as_text=True))


def _log(client, cid, kind="EXPORT", channel="EMAIL", received_on=None):
    data = {"csrf_token": _token(client, cid), "kind": kind, "channel": channel}
    if received_on is not None:
        data["received_on"] = received_on
    return client.post(f"/data-requests/customers/{cid}", data=data)


def _post(client, cid, url, **data):
    return client.post(url, data={"csrf_token": _token(client, cid), **data})


def _requests(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.data_requests import DataRequest

        return list(db_session.execute(select(DataRequest).order_by(DataRequest.request_number)).scalars())


def _audit_count(app, action, entity=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        stmt = select(func.count()).select_from(AuditLog).where(AuditLog.action_code == action)
        if entity is not None:
            stmt = stmt.where(AuditLog.entity_public_id == str(entity))
        return db_session.execute(stmt).scalar_one()


def _kyc_files(app) -> int:
    root = app.config["KYC_DIRECTORY"]
    return sum(len(f) for _, _, f in os.walk(root)) if os.path.isdir(root) else 0


def _shop(app, w):
    """A customer with an account, a contact, a location, 2 ID photos and a running trial."""
    return onboard(app, w).customer_id


def _set_sub_status(app, cid, status, *, trial_ended=False):
    from datetime import date

    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_ops import PilotRecord
        from app.models.subscriptions import Subscription

        sub = db_session.execute(select(Subscription).where(Subscription.customer_id == cid)).scalars().one()
        sub.status = status
        if trial_ended:
            pilot = db_session.execute(select(PilotRecord).where(PilotRecord.customer_id == cid)).scalars().one()
            pilot.pilot_start = date(2026, 1, 1)
            pilot.pilot_end = date(2026, 1, 8)
        db_session.commit()


def _money_records(app, w, cid):
    """An invoice, a payment, a payment slip file, a contract acceptance, a trial-device mark and two mails."""
    with app.app_context():
        from datetime import date, datetime, timezone

        from app.customer_onboarding import documents
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice
        from app.models.contracts import ContractAcceptance, ContractVersion
        from app.models.customer_accounts import CustomerAccount
        from app.models.customer_onboarding import TrialDeviceMark
        from app.models.email_outbox import EmailOutbox
        from app.models.employees import EmployeeProfile
        from app.models.subscriptions import PaymentRecord

        profile = db_session.execute(select(EmployeeProfile).where(EmployeeProfile.staff_user_id == w["seller"])).scalars().one()
        invoice = CommercialInvoice(customer_id=cid, created_by_employee_profile_id=profile.id,
                                    invoice_number=f"INV-{uuid.uuid4().hex[:8]}", currency="JOD", total=Decimal("100.00"))
        payment = PaymentRecord(customer_id=cid, amount=Decimal("100.00"), currency="JOD", payment_date=date(2026, 2, 1),
                                status="CONFIRMED")
        db_session.add_all([invoice, payment])
        db_session.flush()
        slip = documents.store_document(customer_id=cid, kind="PAYMENT_SLIP", content=b"%PDF-1.4 slip", payment_record_id=payment.id,
                                        uploaded_by_staff_user_id=w["seller"])
        body = "contract text"
        version = ContractVersion(audience="CUSTOMER", language="en", version_label="v-dr1", title="T", body=body,
                                  body_sha256=hashlib.sha256(body.encode()).hexdigest())
        db_session.add(version)
        db_session.flush()
        account = db_session.execute(select(CustomerAccount).where(CustomerAccount.customer_id == cid)).scalars().one()
        acceptance = ContractAcceptance(
            contract_version_id=version.id, audience="CUSTOMER", customer_id=cid, account_id=account.id,
            email_snapshot=account.email, language="en", text_sha256=version.body_sha256,
            accepted_at=datetime.now(timezone.utc))
        mark = TrialDeviceMark(device_key_fingerprint=f"fp-{uuid.uuid4().hex}", customer_id=cid)
        mine = EmailOutbox(kind="notice", to_address=account.email.upper(), subject="Your receipt", body_text="Hello Omar",
                           status="QUEUED")
        theirs = EmailOutbox(kind="notice", to_address="someone-else@example.com", subject="Other", body_text="Other body",
                             status="QUEUED")
        db_session.add_all([acceptance, mark, mine, theirs])
        db_session.commit()
        return {"invoice": invoice.id, "payment": payment.id, "slip": slip.id, "acceptance": acceptance.id, "mark": mark.id,
                "fingerprint": mark.device_key_fingerprint, "mine": mine.id, "theirs": theirs.id,
                "invoice_number": invoice.invoice_number, "email": account.email, "acceptance_hash": acceptance.text_sha256}


def _snapshot_person(app, cid):
    """Every personal field erase must change -- read in one place so a test can compare before/after."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount
        from app.models.customer_onboarding import CustomerDocument
        from app.models.customers import Customer, CustomerContact
        from app.models.leads import CustomerLocation

        customer = db_session.get(Customer, cid)
        contact = db_session.execute(select(CustomerContact).where(CustomerContact.customer_id == cid)).scalars().one()
        account = db_session.execute(select(CustomerAccount).where(CustomerAccount.customer_id == cid)).scalars().one()
        location = db_session.execute(select(CustomerLocation).where(CustomerLocation.customer_id == cid)).scalars().one()
        return {"name": customer.legal_name, "cr": customer.commercial_registration_reference, "contact": contact.name,
                "email": contact.business_email, "phone": contact.business_phone, "account_email": account.email,
                "account_status": account.status, "has_password": account.password_hash is not None,
                "lat": location.latitude, "docs_purged": [d.purged_at for d in db_session.execute(
                    select(CustomerDocument).where(CustomerDocument.customer_id == cid, CustomerDocument.kind != "PAYMENT_SLIP")).scalars()]}


def _log_direct(app, w, cid, kind, who="manager"):
    with app.app_context():
        from app.privacy_requests import service

        return service.log_request(customer_id=cid, kind=kind, channel="EMAIL", received_on=None, actor_staff_user_id=w[who]).id


# ------------------------------------------------------------- permission

def test_only_privacy_requests_holders_open_anything_and_a_refusal_writes_nothing(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    rid = _log_direct(app, w, cid, "ERASE")
    seller = _client(app, w["seller"])
    token = _token(seller, cid)
    attempts = [
        seller.get("/data-requests"),
        seller.post(f"/data-requests/customers/{cid}", data={"csrf_token": token, "kind": "EXPORT", "channel": "EMAIL"}),
        seller.get(f"/data-requests/{rid}/export"),
        seller.post(f"/data-requests/{rid}/erase", data={"csrf_token": token}),
        seller.post(f"/data-requests/{rid}/close", data={"csrf_token": token, "status": "REFUSED", "answer_note": "no"}),
    ]
    assert [r.status_code for r in attempts] == [403] * 5
    rows = _requests(app)
    assert len(rows) == 1 and rows[0].status == "OPEN" and rows[0].fulfilled_at is None
    assert _kyc_files(app) == 2 and _audit_count(app, "CUSTOMER_ERASED") == 0
    # allow twin: the manager reaches all of it
    manager = _client(app, w["manager"])
    assert manager.get("/data-requests").status_code == 200
    assert _log(manager, cid).status_code == 303 and len(_requests(app)) == 2


def test_the_customer_card_is_drawn_only_for_a_holder(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    seller_page = _client(app, w["seller"]).get(f"/customers/{cid}")
    assert seller_page.status_code == 200 and "data-requests-card" not in seller_page.get_data(as_text=True)
    manager_page = _client(app, w["manager"]).get(f"/customers/{cid}")
    assert 'id="data-requests-card"' in manager_page.get_data(as_text=True)


# --------------------------------------------------------------------- log

def test_a_logged_request_is_due_exactly_30_days_after_it_was_received(app, seeded, kyc):
    from tests.conftest import jordan_today

    w = _world3(app)
    cid = _shop(app, w)
    manager = _client(app, w["manager"])
    day = jordan_today() - timedelta(days=4)
    assert _log(manager, cid, kind="ERASE", channel="PHONE", received_on=day.isoformat()).status_code == 303
    row = _requests(app)[0]
    assert row.status == "OPEN" and row.kind == "ERASE" and row.channel == "PHONE" and row.logged_by_staff_user_id == w["manager"]
    assert row.due_at - row.received_at == timedelta(days=30)
    from app.operational_reports.periods import OWNER_TIMEZONE

    assert row.received_at.astimezone(OWNER_TIMEZONE).date() == day
    assert _audit_count(app, "DATA_REQUEST_LOGGED", row.id) == 1


@pytest.mark.parametrize("form", [
    {"kind": "DELETE_EVERYTHING", "channel": "EMAIL"},
    {"kind": "EXPORT", "channel": "CARRIER_PIGEON"},
    {"kind": "EXPORT", "channel": "EMAIL", "received_on": "2999-01-01"},
    {"kind": "EXPORT", "channel": "EMAIL", "received_on": "not-a-date"},
])
def test_a_bad_request_form_writes_no_request_and_no_audit(app, seeded, kyc, form):
    w = _world3(app)
    cid = _shop(app, w)
    manager = _client(app, w["manager"])
    response = manager.post(f"/data-requests/customers/{cid}", data={"csrf_token": _token(manager, cid), **form})
    assert response.status_code == 303
    assert _requests(app) == [] and _audit_count(app, "DATA_REQUEST_LOGGED") == 0


def test_a_request_for_a_customer_the_actor_cannot_see_is_404(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    # a privacy.requests holder WITHOUT customers.view_all, who is not the assigned seller
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        role = Role(code="PRIV_OWN", name="x", description="x", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in ("privacy.requests", "customers.view"):
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.commit()
    stranger = make_staff(app, f"stranger-{uuid.uuid4().hex[:5]}@example.com", role_codes=["PRIV_OWN"])
    client = _client(app, stranger)
    assert client.post(f"/data-requests/customers/{cid}", data={"csrf_token": _token(client, cid), "kind": "EXPORT", "channel": "EMAIL"}).status_code == 404
    assert _requests(app) == []


# ----------------------------------------------------------------- overdue

def test_a_request_is_late_the_day_after_its_due_day_not_on_it(app, seeded, kyc):
    from tests.conftest import jordan_today

    w = _world3(app)
    cid = _shop(app, w)
    manager = _client(app, w["manager"])
    today = jordan_today()
    _log(manager, cid, received_on=(today - timedelta(days=30)).isoformat())  # due TODAY: still on time
    assert _counts(app) == (1, 0)
    _log(manager, cid, received_on=(today - timedelta(days=31)).isoformat())  # due yesterday: late
    _log(manager, cid, received_on=today.isoformat())
    assert _counts(app) == (3, 1)
    tile = manager.get("/").get_data(as_text=True)
    assert 'data-data-requests-card="1"' in tile
    seller_tile = _client(app, w["seller"]).get("/").get_data(as_text=True)
    assert 'data-data-requests-card="1"' not in seller_tile
    # a closed late request stops counting
    late = [r for r in _requests(app) if r.received_at < _requests(app)[0].received_at][0]
    _post(manager, cid, f"/data-requests/{late.id}/close", status="REFUSED", answer_note="Identity not proven.")
    assert _counts(app) == (2, 0)


def _counts(app):
    with app.app_context():
        from app.privacy_requests import service

        return service.open_counts()


# ------------------------------------------------------------------ export

def _zip(response):
    assert response.status_code == 200 and response.mimetype == "application/zip", response.status_code
    return zipfile.ZipFile(io.BytesIO(response.data))


def test_the_export_holds_the_records_and_the_photos_for_a_holder_who_may_open_them(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    extra = _money_records(app, w, cid)
    rid = _log_direct(app, w, cid, "EXPORT")
    manager = _client(app, w["manager"])
    archive = _zip(manager.get(f"/data-requests/{rid}/export"))
    names = archive.namelist()
    photos = [n for n in names if n.startswith("documents/")]
    assert "data.json" in names and len(photos) == 3  # ID, registration, payment slip
    assert sorted(n.split("/")[1].split("-")[0] for n in photos) == ["commercial_registration", "national_id", "payment_slip"]
    assert any(archive.read(n).startswith(b"\xff\xd8\xff") for n in photos)  # a decrypted JPEG, not ciphertext
    assert not any(archive.read(n).startswith(b"AKYC1") for n in photos)
    raw = archive.read("data.json").decode("utf-8")
    data = json.loads(raw)["records"]
    assert data["customer"]["legal_name"].startswith("Haddad Market")
    assert data["contacts"][0]["business_email"] and data["website_account"][0]["email"] == extra["email"]
    assert data["invoices"][0]["invoice_number"] == extra["invoice_number"] and data["payments"] and data["contract_acceptances"]
    assert any(e["action"] == "DATA_REQUEST_LOGGED" for e in data["audit_entries"])
    # secrets and machinery are not "facts about the person"
    assert "password_hash" not in raw and "storage_key" not in raw and "enc_key_id" not in raw and "token_hash" not in raw
    # audited once as an export, and once per photo put in the ZIP
    assert _audit_count(app, "CUSTOMER_DATA_EXPORTED", cid) == 1
    assert _audit_count(app, "CUSTOMER_DOCUMENT_VIEWED") == 3
    row = _requests(app)[0]
    assert row.fulfilled_at is not None and row.status == "OPEN"  # downloading is not closing


def test_without_customer_documents_view_the_zip_has_the_records_but_no_photo(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    rid = _log_direct(app, w, cid, "EXPORT")
    archive = _zip(_client(app, w["priv_only"]).get(f"/data-requests/{rid}/export"))
    assert [n for n in archive.namelist() if n.startswith("documents/")] == []
    payload = json.loads(archive.read("data.json"))
    assert payload["records"]["customer"]["legal_name"].startswith("Haddad Market")
    assert "withheld" in json.dumps(payload["export_notes"])
    assert _audit_count(app, "CUSTOMER_DATA_EXPORTED", cid) == 1
    assert _audit_count(app, "CUSTOMER_DOCUMENT_VIEWED") == 0  # no photo was opened


def test_the_export_needs_recent_reauth_and_an_open_export_request(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    export_id = _log_direct(app, w, cid, "EXPORT")
    erase_id = _log_direct(app, w, cid, "ERASE")
    stale = _client(app, w["manager"], recent_auth=False)
    response = stale.get(f"/data-requests/{export_id}/export")
    assert response.status_code == 302 and "reauth" in response.headers["Location"]
    assert _audit_count(app, "CUSTOMER_DATA_EXPORTED") == 0 and _requests(app)[0].fulfilled_at is None
    fresh = _client(app, w["manager"])
    refused = fresh.get(f"/data-requests/{erase_id}/export")  # an ERASE request is not a copy request
    assert refused.status_code == 303 and refused.mimetype != "application/zip"
    assert _audit_count(app, "CUSTOMER_DATA_EXPORTED") == 0
    assert fresh.get(f"/data-requests/{export_id}/export").status_code == 200  # allow twin


# ----------------------------------------------------------- erase: refused

@pytest.mark.parametrize("status", ["ACTIVE", "PAST_DUE", "PILOT"])
def test_erase_is_refused_while_the_customer_is_current_and_nothing_changes(app, seeded, kyc, status):
    w = _world3(app)
    cid = _shop(app, w)
    _set_sub_status(app, cid, status)  # PILOT here is the onboarding trial, still running
    before = _snapshot_person(app, cid)
    rid = _log_direct(app, w, cid, "ERASE")
    manager = _client(app, w["manager"])
    response = _post(manager, cid, f"/data-requests/{rid}/erase")
    assert response.status_code == 303
    assert _snapshot_person(app, cid) == before and _kyc_files(app) == 2
    row = _requests(app)[0]
    assert row.status == "OPEN" and row.fulfilled_at is None
    assert _audit_count(app, "CUSTOMER_ERASED") == 0 and _audit_count(app, "DATA_REQUEST_DONE") == 0
    # the manager is told why (the card shows the reason beside the button too)
    assert b"current subscription" in manager.get(f"/customers/{cid}").data


@pytest.mark.parametrize("status, ended", [("CANCELLED", False), ("PILOT", True), ("EXPIRED", False)])
def test_erase_is_allowed_once_the_subscription_ended_or_the_trial_is_over(app, seeded, kyc, status, ended):
    w = _world3(app)
    cid = _shop(app, w)
    _set_sub_status(app, cid, status, trial_ended=ended)
    rid = _log_direct(app, w, cid, "ERASE")
    _post(_client(app, w["manager"]), cid, f"/data-requests/{rid}/erase")
    assert _requests(app)[0].status == "DONE"


# ------------------------------------------------------------ erase: effect

def test_erase_removes_the_person_and_keeps_invoices_payments_slips_evidence(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    other = _shop(app, w)  # a second shop that must not be touched
    keep = _money_records(app, w, cid)
    other_before = _snapshot_person(app, other)
    _set_sub_status(app, cid, "CANCELLED")
    assert _kyc_files(app) == 2 + 2 + 1  # both shops' ID + registration, this shop's slip
    rid = _log_direct(app, w, cid, "ERASE")
    manager = _client(app, w["manager"])
    _post(manager, cid, f"/data-requests/{rid}/erase")

    after = _snapshot_person(app, cid)
    assert after["name"] == f"Erased customer #{str(cid)[:8]}" and after["cr"] is None
    assert after["contact"] == "Erased contact" and after["email"] is None and after["phone"] is None and after["lat"] is None
    assert after["account_email"].endswith("@erased.invalid") and after["account_status"] == "DISABLED" and not after["has_password"]
    assert all(p is not None for p in after["docs_purged"])
    assert _kyc_files(app) == 2 + 1  # this shop's ID + registration files are GONE from disk; the other shop's and the slip stay
    assert _snapshot_person(app, other) == other_before  # allow twin: another customer is untouched

    with app.app_context():
        from app.customer_accounts import service as accounts
        from app.customer_onboarding import documents
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice
        from app.models.contracts import ContractAcceptance
        from app.models.customer_accounts import CustomerAccount, CustomerAccountToken
        from app.models.customer_onboarding import CustomerDocument, TrialDeviceMark
        from app.models.email_outbox import EmailOutbox
        from app.models.subscriptions import PaymentRecord

        invoice = db_session.get(CommercialInvoice, keep["invoice"])
        assert invoice.invoice_number == keep["invoice_number"] and invoice.total == Decimal("100.00")
        assert db_session.get(PaymentRecord, keep["payment"]).amount == Decimal("100.00")
        slip = db_session.get(CustomerDocument, keep["slip"])
        assert slip.purged_at is None and documents.read_document(slip)[0].startswith(b"%PDF")
        assert db_session.get(ContractAcceptance, keep["acceptance"]).text_sha256 == keep["acceptance_hash"]
        mark = db_session.get(TrialDeviceMark, keep["mark"])
        assert mark.customer_id is None and mark.device_key_fingerprint == keep["fingerprint"]
        account_id = db_session.execute(select(CustomerAccount.id).where(CustomerAccount.customer_id == cid)).scalar_one()
        assert db_session.execute(select(func.count()).select_from(CustomerAccountToken).where(
            CustomerAccountToken.account_id == account_id)).scalar_one() == 0
        mine, theirs = db_session.get(EmailOutbox, keep["mine"]), db_session.get(EmailOutbox, keep["theirs"])
        assert mine.status == "CANCELLED" and mine.to_address == "erased@erased.invalid" and mine.body_text == "[erased]"
        assert theirs.status == "QUEUED" and theirs.to_address == "someone-else@example.com"  # allow twin
        with pytest.raises(accounts.AccountError):
            accounts.authenticate(keep["email"], GOOD, "127.0.0.1")

    row = _requests(app)[0]
    assert row.status == "DONE" and row.fulfilled_at is not None and row.closed_by_staff_user_id == w["manager"] and row.answer_note
    assert _audit_count(app, "CUSTOMER_ERASED", cid) == 1 and _audit_count(app, "DATA_REQUEST_DONE", rid) == 1
    # the audit trail itself names no person: only counts
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        erased = db_session.execute(select(AuditLog).where(AuditLog.action_code == "CUSTOMER_ERASED")).scalars().one()
        assert "Haddad" not in json.dumps(erased.after_state_redacted) and "Omar" not in json.dumps(erased.after_state_redacted)


def test_erase_needs_recent_reauth_and_only_runs_on_an_erase_request(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    _set_sub_status(app, cid, "CANCELLED")
    erase_id = _log_direct(app, w, cid, "ERASE")
    export_id = _log_direct(app, w, cid, "EXPORT")
    before = _snapshot_person(app, cid)
    stale = _client(app, w["manager"], recent_auth=False)
    assert _post(stale, cid, f"/data-requests/{erase_id}/erase").status_code == 302
    assert _snapshot_person(app, cid) == before and _audit_count(app, "CUSTOMER_ERASED") == 0
    fresh = _client(app, w["manager"])
    _post(fresh, cid, f"/data-requests/{export_id}/erase")  # an EXPORT request must never erase
    assert _snapshot_person(app, cid) == before and _audit_count(app, "CUSTOMER_ERASED") == 0
    _post(fresh, cid, f"/data-requests/{erase_id}/erase")  # allow twin
    assert _snapshot_person(app, cid)["contact"] == "Erased contact"


def test_a_file_that_cannot_be_deleted_leaves_the_request_open_and_a_second_press_finishes(app, seeded, kyc, monkeypatch):
    from app.customer_onboarding import documents

    w = _world3(app)
    cid = _shop(app, w)
    _set_sub_status(app, cid, "CANCELLED")
    rid = _log_direct(app, w, cid, "ERASE")
    manager = _client(app, w["manager"])
    monkeypatch.setattr(documents, "remove_blob", lambda document: False)
    _post(manager, cid, f"/data-requests/{rid}/erase")
    row = _requests(app)[0]
    assert row.status == "OPEN" and row.fulfilled_at is None  # never "done" on files that are still there
    assert _snapshot_person(app, cid)["contact"] == "Erased contact" and _kyc_files(app) == 2
    monkeypatch.undo()
    _post(manager, cid, f"/data-requests/{rid}/erase")
    assert _requests(app)[0].status == "DONE" and _kyc_files(app) == 0


# ------------------------------------------------------------------- close

def test_an_erase_request_cannot_be_closed_done_by_hand_but_can_be_refused_with_a_note(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    _set_sub_status(app, cid, "CANCELLED")
    rid = _log_direct(app, w, cid, "ERASE")
    before = _snapshot_person(app, cid)
    manager = _client(app, w["manager"])
    _post(manager, cid, f"/data-requests/{rid}/close", status="DONE", answer_note="Erased, trust me.")
    assert _requests(app)[0].status == "OPEN" and _snapshot_person(app, cid) == before
    _post(manager, cid, f"/data-requests/{rid}/close", status="REFUSED", answer_note="  ")  # a note is required
    assert _requests(app)[0].status == "OPEN"
    _post(manager, cid, f"/data-requests/{rid}/close", status="REFUSED", answer_note="We could not confirm who asked.")
    row = _requests(app)[0]
    assert row.status == "REFUSED" and row.answer_note == "We could not confirm who asked." and row.closed_at is not None
    assert _snapshot_person(app, cid) == before and _audit_count(app, "DATA_REQUEST_REFUSED", rid) == 1
    # a closed request cannot be closed or erased again
    _post(manager, cid, f"/data-requests/{rid}/erase")
    assert _snapshot_person(app, cid) == before and _requests(app)[0].status == "REFUSED"


def test_an_export_request_is_closed_done_with_a_note(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    rid = _log_direct(app, w, cid, "EXPORT")
    _post(_client(app, w["manager"]), cid, f"/data-requests/{rid}/close", status="DONE", answer_note="Sent by email on the 1st.")
    row = _requests(app)[0]
    assert row.status == "DONE" and row.answer_note and _audit_count(app, "DATA_REQUEST_DONE", rid) == 1


def test_the_database_itself_refuses_a_done_erase_without_an_erasure_and_a_closed_request_without_a_note(app, seeded, kyc):
    w = _world3(app)
    cid = _shop(app, w)
    erase_id = _log_direct(app, w, cid, "ERASE")
    export_id = _log_direct(app, w, cid, "EXPORT")
    with app.app_context():
        from app.extensions import db_session

        for sql, params in (
            ("UPDATE owner_data_requests SET status='DONE', closed_at=now(), answer_note='x' WHERE id=:i", {"i": erase_id}),
            ("UPDATE owner_data_requests SET status='DONE', closed_at=now() WHERE id=:i", {"i": export_id}),
            ("UPDATE owner_data_requests SET status='DONE', answer_note='x' WHERE id=:i", {"i": export_id}),
        ):
            with pytest.raises(IntegrityError):
                db_session.execute(text(sql), params)
            db_session.rollback()
        # allow twin: a DONE export with a note and a time is fine
        db_session.execute(text("UPDATE owner_data_requests SET status='DONE', closed_at=now(), answer_note='x' WHERE id=:i"), {"i": export_id})
        db_session.commit()


# -------------------------------------------------------------------- i18n

def test_every_data_request_message_is_translated(app, seeded):
    from app.i18n_labels import (
        data_request_channel_label, data_request_kind_label, data_request_status_label, localize_data_request_error)

    codes = ["KIND_INVALID", "CHANNEL_INVALID", "CUSTOMER_NOT_FOUND", "RECEIVED_IN_FUTURE", "RECEIVED_DATE_INVALID", "REQUEST_NOT_FOUND",
             "REQUEST_NOT_OPEN", "ANSWER_REQUIRED", "TEXT_TOO_LONG", "STATUS_INVALID", "ERASE_NOT_CARRIED_OUT", "NOT_AN_ERASE_REQUEST",
             "NOT_AN_OPEN_EXPORT", "ERASE_BLOCKED_BY_SUBSCRIPTION", "ERASE_FILES_NOT_DELETED"]
    from flask_babel import force_locale

    with app.test_request_context("/"):
        with force_locale("en"):
            english = {c: localize_data_request_error(c, statuses="ACTIVE", count=1) for c in codes}
            labels_en = [f(c) for f, cs in ((data_request_kind_label, ("EXPORT", "ERASE")), (data_request_channel_label, ("EMAIL", "PHONE", "IN_PERSON")),
                                            (data_request_status_label, ("OPEN", "DONE", "REFUSED"))) for c in cs]
        with force_locale("ar"):
            for code in codes:
                arabic = localize_data_request_error(code, statuses="ACTIVE", count=1)
                assert arabic != code and arabic != english[code], code
                assert "--" not in arabic, code
            labels_ar = [f(c) for f, cs in ((data_request_kind_label, ("EXPORT", "ERASE")), (data_request_channel_label, ("EMAIL", "PHONE", "IN_PERSON")),
                                            (data_request_status_label, ("OPEN", "DONE", "REFUSED"))) for c in cs]
    assert all(a != e for a, e in zip(labels_ar, labels_en))
