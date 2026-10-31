"""The seller's own ID photo (Alembic SID1): the gate, the storage, the viewing.

What each guard here is proven by (never a status code alone):
  gate          the REDIRECT, and that the same request is served once the photo exists;
                a non-seller, "gate off", and "no encryption key" are served untouched
  storage       the bytes on disk are not the picture, and decrypt back to it
  viewing       who got the bytes, and that an audit row says so; who was refused and
                that NO audit row was written for them
  validation    refused uploads leave no row and no file behind
  supersede     the old row is kept (superseded), the new one is current

Every deny has its allow twin in the same test or the one beside it.
"""
from __future__ import annotations

import io
import logging
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_customer_onboarding import _jpeg, _png, kyc  # noqa: F401 -- kyc is a fixture
from tests.test_seller_agreement_gate import SELLER_PAGE, _accept, _client, _publish_seller_version

UPLOAD_PAGE = "/seller-id"


# ------------------------------------------------------------------ helpers

@pytest.fixture()
def people(app, seeded, kyc):
    return {
        "admin": make_staff(app, "sid-admin@example.com", super_admin=True, mfa=True),
        "seller": make_staff(app, "sid-seller@example.com", role_codes=["SALES"]),
        "other": make_staff(app, "sid-other@example.com", role_codes=["SALES"]),
        "manager": make_staff(app, "sid-manager@example.com", role_codes=["SALES_MANAGER"], mfa=True),
        "both": make_staff(app, "sid-both@example.com", role_codes=["SALES", "SALES_MANAGER"], mfa=True),
        "finance": make_staff(app, "sid-finance@example.com", role_codes=["FINANCE"], mfa=True),
    }


def _ready_seller(app, people, who="seller"):
    """A live agreement, and `who` has accepted it: only the ID step is left."""
    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people[who])
    assert _accept(client, version_id).status_code == 302
    return client, version_id


def _send_id_gate(response) -> bool:
    """True when this response IS the ID-photo gate talking."""
    if response.status_code == 303 and response.headers.get("Location", "").startswith(UPLOAD_PAGE):
        return True
    return response.status_code == 403 and (response.get_json(silent=True) or {}).get("error") == "seller_id_photo_required"


def _agreement_gate(response) -> bool:
    return response.status_code == 303 and response.headers.get("Location", "").startswith(SELLER_PAGE)


def _upload(client, content, name="id.jpg"):
    page = client.get(UPLOAD_PAGE)
    token = get_csrf(page.get_data(as_text=True))
    return client.post("/seller-id/upload", data={"csrf_token": token, "id_photo": (io.BytesIO(content), name)},
                       content_type="multipart/form-data")


def _rows(app, **where):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff_identity import StaffIdentityDocument

        stmt = select(StaffIdentityDocument).order_by(StaffIdentityDocument.created_at)
        for key, value in where.items():
            stmt = stmt.where(getattr(StaffIdentityDocument, key) == value)
        rows = db_session.execute(stmt).scalars().all()
        for row in rows:
            db_session.expunge(row)
        return rows


def _count(app, **where):
    return len(_rows(app, **where))


def _files(app):
    root = app.config["KYC_DIRECTORY"]
    found = []
    for base, _dirs, names in os.walk(root):
        found += [os.path.join(base, n) for n in names]
    return found


def _audit(app, action, entity_id=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        stmt = select(AuditLog).where(AuditLog.action_code == action)
        if entity_id is not None:
            stmt = stmt.where(AuditLog.entity_public_id == str(entity_id))
        return list(db_session.execute(stmt).scalars().all())


def _recent_auth(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        for record in db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == staff_id)).scalars():
            record.mfa_verified_at = utcnow()
        db_session.commit()


def _add_photo_directly(app, staff_id, uploaded_by, content=None):
    with app.app_context():
        from app.seller_id import service

        document = service.save_photo(content=content or _jpeg(), uploaded_by_staff_user_id=uploaded_by, staff_user_id=staff_id)
        return document.id


# ------------------------------------------------------------------ the gate

def test_a_seller_without_an_id_photo_is_sent_to_the_upload_page_and_after_uploading_gets_in(app, people):
    client, _ = _ready_seller(app, people)
    for path in ("/", "/leads", "/payments/new"):
        response = client.get(path)
        assert _send_id_gate(response), (path, response.status_code, response.headers.get("Location"))
    api = client.get("/api/operations/v1/me", headers={"Accept": "application/json"})
    assert api.status_code == 403 and api.get_json()["error"] == "seller_id_photo_required"
    # the way out stays open: the upload page, sign-out / language / profile, the agreement page
    page = client.get(f"{UPLOAD_PAGE}?required=1")
    assert page.status_code == 200 and 'data-id-photo-required="1"' in page.get_data(as_text=True)
    assert not _send_id_gate(client.get("/locale/en")) and not _send_id_gate(client.get("/profile"))
    assert client.get(SELLER_PAGE).status_code == 200
    assert _count(app) == 0

    response = _upload(client, _jpeg())  # the real form: csrf token and all
    assert response.status_code == 303 and _count(app, staff_user_id=people["seller"]) == 1
    assert client.get("/").status_code == 200  # really served, not merely "not redirected"
    assert client.get("/leads").status_code == 200
    # one seller's photo does not open the door for another
    other = _client(app, people["other"])
    _accept(other, _publish_seller_version(app, people["admin"], label="v-other"))
    assert _send_id_gate(other.get("/leads"))


def test_the_agreement_comes_first_and_the_id_photo_second(app, people):
    version_id = _publish_seller_version(app, people["admin"])
    client = _client(app, people["seller"])
    # neither done: the agreement answers, for the upload page too
    assert _agreement_gate(client.get("/leads")) and _agreement_gate(client.get(UPLOAD_PAGE))
    assert _count(app) == 0
    assert _accept(client, version_id).status_code == 302
    # agreement done: now the ID step answers
    assert _send_id_gate(client.get("/leads"))
    assert client.get(UPLOAD_PAGE).status_code == 200


def test_a_photo_a_manager_attached_when_creating_the_seller_satisfies_the_gate_at_first_sign_in(app, people):
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.security.passwords import hash_password
    from app.staff.services import create_employee_invitation, create_staff_from_invitation

    version_id = _publish_seller_version(app, people["admin"])
    app.config.update(WTF_CSRF_ENABLED=False)
    admin = _client(app, people["admin"])
    _recent_auth(app, people["admin"])
    response = admin.post("/employees", data={
        "email": "new-seller@example.com", "employee_number": "EMP-SID-1", "full_name": "New Seller",
        "employment_start_date": "2026-01-01", "role_codes": "SALES",
        "id_photo": (io.BytesIO(_jpeg()), "id.jpg")}, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    (row,) = _rows(app)
    assert row.staff_user_id is None and row.invitation_id is not None  # the account does not exist yet
    assert _audit(app, "STAFF_ID_PHOTO_UPLOADED", row.id)
    with app.app_context():
        from app.models.staff import StaffInvitation

        invitation = db_session.get(StaffInvitation, row.invitation_id)
        staff, _profile = create_staff_from_invitation(invitation, "New Seller", hash_password("Sup3r-Str0ng-Pass!"))
        staff_id = staff.id
    (row,) = _rows(app)
    assert row.staff_user_id == staff_id  # bound at acceptance
    client = _client(app, staff_id)
    assert _accept(client, version_id).status_code == 302
    assert not _send_id_gate(client.get("/")) and client.get("/leads").status_code == 200  # no detour at first sign-in


def test_without_a_photo_attached_the_new_seller_is_asked_for_one(app, people):
    from app.models.staff import StaffInvitation
    from app.extensions import db_session
    from app.security.passwords import hash_password
    from app.staff.services import create_employee_invitation, create_staff_from_invitation

    version_id = _publish_seller_version(app, people["admin"])
    with app.app_context():
        invitation, _token = create_employee_invitation(
            "plain@example.com", ["SALES"],
            {"employee_number": "EMP-SID-2", "full_name": "Plain", "employment_start_date": date(2026, 1, 1)}, people["admin"])
        staff, _profile = create_staff_from_invitation(invitation, "Plain", hash_password("Sup3r-Str0ng-Pass!"))
        staff_id = staff.id
    client = _client(app, staff_id)
    _accept(client, version_id)
    assert _send_id_gate(client.get("/leads"))


def test_a_reissued_invitation_keeps_the_photo(app, people):
    from app.models.staff import StaffInvitation
    from app.extensions import db_session
    from app.seller_id import service
    from app.staff.services import create_employee_invitation, reissue_employee_invitation

    with app.app_context():
        invitation, _token = create_employee_invitation(
            "reissue@example.com", ["SALES"],
            {"employee_number": "EMP-SID-3", "full_name": "Reissue", "employment_start_date": date(2026, 1, 1)}, people["admin"])
        service.save_photo(content=_jpeg(), uploaded_by_staff_user_id=people["admin"], invitation_id=invitation.id)
        fresh, _raw = reissue_employee_invitation(invitation, people["admin"])
        fresh_id = fresh.id
    (row,) = _rows(app)
    assert row.invitation_id == fresh_id and row.superseded_at is None


def test_a_non_seller_is_never_sent_to_the_upload_page(app, people):
    _publish_seller_version(app, people["admin"])
    for who in ("manager", "admin", "finance"):
        client = _client(app, people[who])
        response = client.get("/")
        assert not _send_id_gate(response) and response.status_code == 200, who
    # a manager who ALSO sells (holds SALES) is bound like any seller, as for the agreement
    both = _client(app, people["both"])
    _accept(both, _publish_seller_version(app, people["admin"], label="v-both"))
    assert _send_id_gate(both.get("/leads"))
    # and a non-seller who types the upload URL is sent to the dashboard, not shown a form
    manager = _client(app, people["manager"])
    assert manager.get(UPLOAD_PAGE).status_code == 302
    assert _count(app) == 0


def test_the_id_gate_is_off_when_the_agreement_gate_is_off(app, people):
    client = _client(app, people["seller"])
    for path in ("/", "/leads"):
        assert not _send_id_gate(client.get(path)), path  # no SELLER contract live at all
    # live -> on; retired -> off again (the same switch, not a second one)
    from app.contracts import service

    version_id = _publish_seller_version(app, people["admin"])
    _accept(client, version_id)
    assert _send_id_gate(client.get("/leads"))
    with app.app_context():
        service.retire(service.get_version(version_id), actor_staff_user_id=people["admin"])
    assert not _send_id_gate(client.get("/leads"))


def test_with_no_encryption_key_the_id_step_is_skipped_with_a_warning_and_the_upload_is_refused(app, people):
    client, _ = _ready_seller(app, people)
    assert _send_id_gate(client.get("/leads"))  # key present: asked
    app.config["KYC_KEYS"] = ""
    # The app installs its own JSON log handling, so caplog sees nothing here: attach a handler directly.
    seen = []

    class Collect(logging.Handler):
        def emit(self, record):
            seen.append(record)

    gate_log = logging.getLogger("app.seller_id.gate")
    handler = Collect(level=logging.WARNING)
    gate_log.addHandler(handler)
    try:
        assert client.get("/leads").status_code == 200  # NOT locked out forever
        client.get("/leads")
    finally:
        gate_log.removeHandler(handler)
    warnings = [r for r in seen if "OWNER_KYC_KEYS" in r.getMessage()]
    assert len(warnings) == 1  # said once, not once per click
    # the customer flow's behaviour, matched: refuse clearly, store nothing
    page = client.get(UPLOAD_PAGE)
    assert "no encryption key is set" in page.get_data(as_text=True)
    response = _upload(client, _jpeg())
    assert response.status_code == 400 and "no encryption key is set" in response.get_data(as_text=True)
    assert _count(app) == 0 and _files(app) == []
    # malformed counts as missing; the key coming back switches the step on again
    app.config["KYC_KEYS"] = "k1:not-base64!!"
    assert client.get("/leads").status_code == 200
    app.config["KYC_KEYS"] = f"k1:{__import__('tests.test_customer_onboarding', fromlist=['KEY32']).KEY32}"
    assert _send_id_gate(client.get("/leads"))


def test_a_check_that_cannot_run_blocks_the_seller_instead_of_waving_them_through(app, people, monkeypatch):
    client, _ = _ready_seller(app, people)
    _upload(client, _jpeg())
    assert client.get("/leads").status_code == 200  # healthy: through

    from app.seller_id import documents

    def broken(_staff_id):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr(documents, "has_current_photo", broken)
    assert _send_id_gate(client.get("/leads"))  # fail closed
    monkeypatch.undo()
    assert client.get("/leads").status_code == 200


# ------------------------------------------------------------------ storage

def test_the_stored_file_is_not_the_picture_and_decrypts_back_to_it(app, people):
    from app.customer_onboarding import documents as kyc_documents
    from app.seller_id import documents

    secret = b"GPS 31.95N 35.91E Pixel-7"
    picture = _jpeg(secret)
    cleaned, _type = kyc_documents.clean_file(picture)
    assert secret not in cleaned  # the shared cleaner took the EXIF out before anything was stored
    doc_id = _add_photo_directly(app, people["seller"], people["admin"], picture)
    (path,) = _files(app)
    blob = open(path, "rb").read()
    assert blob != picture and blob != cleaned
    assert cleaned not in blob and b"JFIF" not in blob and secret not in blob  # no plaintext fragment survives
    assert blob.startswith(b"AKYC1")  # the same blob format the customer IDs use
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff_identity import StaffIdentityDocument

        row = db_session.get(StaffIdentityDocument, doc_id)
        plaintext, content_type = documents.read_photo(row)
        assert plaintext == cleaned and content_type == "image/jpeg"
        assert row.size_bytes == len(cleaned) and row.enc_key_id == "k1"


def test_a_blob_swapped_between_two_photos_does_not_open(app, people):
    from app.seller_id import documents

    first = _add_photo_directly(app, people["seller"], people["admin"], _jpeg(b"first"))
    second = _add_photo_directly(app, people["other"], people["admin"], _png(b"second"))
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff_identity import StaffIdentityDocument

        a, b = db_session.get(StaffIdentityDocument, first), db_session.get(StaffIdentityDocument, second)
        pa, pb = documents.kyc.path_for_key(a.storage_key), documents.kyc.path_for_key(b.storage_key)
        blob_a, blob_b = open(pa, "rb").read(), open(pb, "rb").read()
        assert documents.read_photo(a)[0] and documents.read_photo(b)[0]  # allow: each opens on its own
        open(pa, "wb").write(blob_b)
        open(pb, "wb").write(blob_a)
        with pytest.raises(documents.DocumentError):
            documents.read_photo(a)
        with pytest.raises(documents.DocumentError):
            documents.read_photo(b)


# ------------------------------------------------------------------ viewing

def test_only_staff_documents_view_opens_a_photo_and_every_open_is_audited(app, people):
    doc_id = _add_photo_directly(app, people["seller"], people["admin"], _jpeg())
    url = f"/seller-id/documents/{doc_id}"
    # a SALES seller: 403 for their own photo and another seller's; nothing audited for the refusals
    # Both sellers are fully through the gate (agreement accepted, own photo on file), so the
    # 403 below is the permission talking, not a redirect from either gate.
    version_id = _publish_seller_version(app, people["admin"])
    _add_photo_directly(app, people["other"], people["admin"], _png())
    seller, other = _client(app, people["seller"]), _client(app, people["other"])
    assert _accept(seller, version_id).status_code == 302 and _accept(other, version_id).status_code == 302
    assert seller.get("/leads").status_code == 200 and other.get("/leads").status_code == 200
    assert seller.get(url).status_code == 403
    assert other.get(url).status_code == 403
    assert _client(app, people["finance"]).get(url).status_code == 403
    assert _audit(app, "STAFF_ID_PHOTO_VIEWED") == []
    assert app.test_client().get(url).status_code == 302  # signed out: sent to sign in

    # a Sales Manager and a Super Admin hold the permission: they get the picture, and a row says so
    from app.customer_onboarding import documents as kyc_documents

    expected = kyc_documents.clean_file(_jpeg())[0]
    manager = _client(app, people["manager"])
    response = manager.get(url)
    assert response.status_code == 200 and response.data == expected
    assert response.headers["Cache-Control"] == "no-store" and response.headers["Content-Type"] == "image/jpeg"
    (row,) = _audit(app, "STAFF_ID_PHOTO_VIEWED", doc_id)
    assert str(row.actor_staff_user_id) == str(people["manager"]) and row.entity_type == "staff_identity_document"
    assert _client(app, people["admin"]).get(url).status_code == 200
    assert len(_audit(app, "STAFF_ID_PHOTO_VIEWED", doc_id)) == 2
    assert _client(app, people["manager"]).get(f"/seller-id/documents/{uuid.uuid4()}").status_code == 404


def test_the_permission_is_held_by_exactly_the_customer_document_viewers(app):
    from app.staff.seed_data import PERMISSIONS, ROLES

    assert "staff_documents.view" in {code for code, _cat, _desc in PERMISSIONS}
    holders = {role for role, d in ROLES.items() if d["permissions"] == "*" or "staff_documents.view" in d["permissions"]}
    customer_viewers = {role for role, d in ROLES.items()
                        if d["permissions"] == "*" or "customer_documents.view" in d["permissions"]}
    assert holders == customer_viewers == {"SUPER_ADMIN", "SALES_MANAGER"}
    assert "SALES" not in holders


def test_a_seller_sees_only_that_a_photo_is_on_file_never_the_photo(app, people):
    client, _ = _ready_seller(app, people)
    _upload(client, _jpeg())
    html = client.get(UPLOAD_PAGE).get_data(as_text=True)
    assert "data-id-photo-on-file" in html and "/seller-id/documents/" not in html and "image/jpeg" not in html
    # the manager's page for the same person has the link
    from tests.test_issuance_sale import _profile

    profile_id = _profile(app, people["seller"], "EMP-SID-9", "Photo Seller")
    for who in ("admin", "manager"):
        page = _client(app, people[who]).get(f"/employees/{profile_id}").get_data(as_text=True)
        assert 'data-id-photo-link="1"' in page and "ID photo on file since" in page, who
    staff_page = _client(app, people["admin"]).get(f"/staff/{people['seller']}").get_data(as_text=True)
    assert 'data-id-photo-link="1"' in staff_page


# ------------------------------------------------------------------ validation

def test_a_refused_upload_leaves_no_row_and_no_file(app, people):
    client, _ = _ready_seller(app, people)
    big = _jpeg() + b"\x00" * (10 * 1024 * 1024 + 1)
    pdf = b"%PDF-1.4\n%fake\n"
    cases = [
        (b"just some text, not a picture", "must be a JPEG or PNG"),
        (pdf, "must be a JPEG or PNG"),       # the customer cleaner accepts a PDF; an ID photo is a picture
        (big, "too large"),
        (b"\xff\xd8\xff\xe1\x00", "damaged"),  # a JPEG whose segment runs off the end
        (b"", "Choose a photo"),
    ]
    for content, words in cases:
        response = _upload(client, content)
        assert response.status_code == 400, (words, response.status_code)
        assert words in response.get_data(as_text=True), words
        assert _count(app) == 0 and _files(app) == [], words
        assert _send_id_gate(client.get("/leads"))  # and the seller is still asked
    # allow: both picture types are accepted
    assert _upload(client, _png()).status_code == 303
    assert _count(app, staff_user_id=people["seller"]) == 1 and len(_files(app)) == 1


# ------------------------------------------------------------------ superseding / audit

def test_a_second_photo_supersedes_the_first_and_keeps_it(app, people):
    from app.seller_id import documents

    first = _add_photo_directly(app, people["seller"], people["admin"], _jpeg(b"one"))
    second = _add_photo_directly(app, people["seller"], people["admin"], _png(b"two"))
    rows = {r.id: r for r in _rows(app, staff_user_id=people["seller"])}
    assert set(rows) == {first, second}
    assert rows[first].superseded_at is not None and rows[second].superseded_at is None
    with app.app_context():
        assert documents.current_photo(people["seller"]).id == second
        old = documents.read_photo(rows[first])  # the old one is KEPT and still opens
        assert old[1] == "image/jpeg"
    assert len(_files(app)) == 2


def test_the_edit_form_replaces_the_photo_and_the_seller_cannot_replace_it_themselves(app, people):
    from tests.test_issuance_sale import _profile

    version_id = _publish_seller_version(app, people["admin"])
    profile_id = _profile(app, people["seller"], "EMP-SID-8", "Edit Seller")
    app.config.update(WTF_CSRF_ENABLED=False)
    admin = _client(app, people["admin"])

    def edit(content):
        return admin.post(f"/employees/{profile_id}/edit", data={
            "full_name": "Edit Seller", "id_photo": (io.BytesIO(content), "id.png")}, content_type="multipart/form-data")

    assert edit(_jpeg()).status_code == 302 and edit(_png()).status_code == 302
    rows = _rows(app, staff_user_id=people["seller"])
    assert [r.superseded_at is not None for r in rows] == [True, False] and rows[1].content_type == "image/png"
    assert len(_audit(app, "STAFF_ID_PHOTO_UPLOADED")) == 2
    # a refused edit photo changes nothing about the profile either
    refused = admin.post(f"/employees/{profile_id}/edit", data={
        "full_name": "Should Not Apply", "id_photo": (io.BytesIO(b"not a picture"), "x.txt")}, content_type="multipart/form-data")
    assert refused.status_code == 302 and len(_rows(app, staff_user_id=people["seller"])) == 2
    assert "Should Not Apply" not in _client(app, people["admin"]).get(f"/employees/{profile_id}").get_data(as_text=True)
    # the seller, with a photo on file, cannot swap it
    seller = _client(app, people["seller"])
    _accept(seller, version_id)
    before = _rows(app, staff_user_id=people["seller"])
    response = _upload(seller, _jpeg(b"swap"))
    assert response.status_code == 303 and response.headers["Location"].endswith(UPLOAD_PAGE)
    assert [r.id for r in _rows(app, staff_user_id=people["seller"])] == [r.id for r in before]


def test_a_seller_upload_is_audited(app, people):
    client, _ = _ready_seller(app, people)
    _upload(client, _jpeg())
    (row,) = _rows(app)
    (audit,) = _audit(app, "STAFF_ID_PHOTO_UPLOADED", row.id)
    assert str(audit.actor_staff_user_id) == str(people["seller"])


def test_two_current_photos_for_one_seller_are_refused_by_the_database(app, people):
    from sqlalchemy.exc import IntegrityError

    with app.app_context():
        from app.extensions import db_session
        from app.models.staff_identity import StaffIdentityDocument

        def make():
            return StaffIdentityDocument(
                staff_user_id=people["seller"], storage_key=f"staff-id/{uuid.uuid4()}.bin", content_type="image/jpeg",
                size_bytes=5, sha256="a" * 64, enc_key_id="k1")

        db_session.add(make())
        db_session.commit()
        db_session.add(make())
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
        # allow: a superseded twin is fine
        old = make()
        old.superseded_at = func.now()
        db_session.add(old)
        db_session.commit()


# ------------------------------------------------------------------ the migration

def test_sid1_migration_creates_guards_and_refuses_to_lose_evidence():
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import TEST_DB_URL
    from tests.test_sauth_registry_pin_migration import _alembic, _seed

    prev, new_rev, scratch = "a8f2c5d9e317", "b5a9c2d7e418", "aura_owner_sid1_scratch"
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{scratch}", f"{base}/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {scratch} OWNER aura_owner"))
    admin.dispose()
    try:
        assert _alembic(owner_dir, scratch_url, "upgrade", prev).returncode == 0
        engine = create_engine(scratch_url)
        assert "owner_staff_identity_documents" not in inspect(engine).get_table_names()
        with engine.begin() as conn:
            staff_id = _seed(conn, "owner_staff_users", {})
            other_id = _seed(conn, "owner_staff_users", {})
        engine.dispose()
        up = _alembic(owner_dir, scratch_url, "upgrade", new_rev)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)
        inspector = inspect(engine)
        assert "owner_staff_identity_documents" in inspector.get_table_names()
        assert {"staff_user_id", "invitation_id", "storage_key", "sha256", "enc_key_id", "uploaded_by_staff_user_id",
                "uploaded_at", "superseded_at"} <= {c["name"] for c in inspector.get_columns("owner_staff_identity_documents")}

        def doc(conn, **over):
            values = {"id": str(uuid.uuid4()), "storage_key": f"staff-id/{uuid.uuid4()}.bin", "content_type": "image/jpeg",
                      "size_bytes": 10, "sha256": "a" * 64, "enc_key_id": "k1", **over}
            conn.execute(text(f"INSERT INTO owner_staff_identity_documents ({', '.join(values)}) "
                              f"VALUES ({', '.join(':' + k for k in values)})"), values)
            return values["id"]

        with engine.begin() as conn:
            first = doc(conn, staff_user_id=staff_id)
            doc(conn, staff_user_id=other_id)  # allow: a different seller has their own current photo
        for over in ({"staff_user_id": staff_id},                      # a second CURRENT photo for the same seller
                     {"staff_user_id": other_id, "size_bytes": 0},     # an empty file
                     {}):                                              # no owner at all
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    doc(conn, **over)
        with engine.begin() as conn:  # allow: the old one superseded, then a new current one
            conn.execute(text("UPDATE owner_staff_identity_documents SET superseded_at = now() WHERE id = :i"), {"i": first})
            doc(conn, staff_user_id=staff_id)
        engine.dispose()

        refused = _alembic(owner_dir, scratch_url, "downgrade", prev)
        assert refused.returncode != 0 and "Refusing to downgrade b5a9c2d7e418" in refused.stderr
        engine = create_engine(scratch_url)
        assert "owner_staff_identity_documents" in inspect(engine).get_table_names()  # nothing dropped
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM owner_staff_identity_documents"))
        engine.dispose()
        down = _alembic(owner_dir, scratch_url, "downgrade", prev)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        assert "owner_staff_identity_documents" not in inspect(engine).get_table_names()
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        admin.dispose()
