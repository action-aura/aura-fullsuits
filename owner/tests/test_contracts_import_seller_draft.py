"""`flask contracts import-seller-draft --pdf <file>`: the owner's seller agreement as a DRAFT version.

The Arabic text extracted from the owner's PDF has reversed word order, so the body of this version is
only a short notice, and the PDF travels as the encrypted archive copy. Proven here:
  * the version is a SELLER, Arabic, NOT lawyer-approved, NOT published draft;
  * the body is the notice plus the PDF's SHA-256, so body_sha256 -- the hash a seller's acceptance
    records -- changes if the PDF changes (it cannot be silently swapped);
  * the PDF is stored encrypted and comes back byte for byte;
  * it is idempotent, refuses a non-PDF and a server with no encryption key (nothing written);
  * publishing a draft is refused on a real server and works on a practice one, and then a seller must
    read the PDF and accept before selling -- the acceptance pins the notice + PDF hash.
"""
from __future__ import annotations

import hashlib
import os

from sqlalchemy import func, select

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_customer_onboarding import kyc  # noqa: F401 -- fixture
from tests.test_seller_agreement_gate import _gated

PDF = b"%PDF-1.4\n% the owner's seller agreement\n" + b"y" * 300
NOTICE = "نص الاتفاقية الكامل مرفق بصيغة PDF أدناه — يرجى قراءته كاملًا قبل الموافقة"


def _pdf_file(tmp_path, content=PDF, name="agreement.pdf"):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def _run(app, *args):
    return app.test_cli_runner().invoke(args=["contracts", "import-seller-draft", *args])


def _versions(app):
    with app.app_context():
        from app.contracts import service

        return service.list_versions()


def test_the_pdf_becomes_a_draft_seller_version_with_the_notice_and_the_pdf_hash_in_its_body(app, seeded, kyc, tmp_path):
    from app.contracts import service

    result = _run(app, "--pdf", _pdf_file(tmp_path))
    assert result.exit_code == 0, result.output
    (version,) = _versions(app)
    digest = hashlib.sha256(PDF).hexdigest()
    with app.app_context():
        version = service.get_version(version.id)
        assert (version.audience, version.language, version.lawyer_approved) == ("SELLER", "ar", False)
        assert version.published_at is None and version.lawyer_approval_note is None
        assert version.body.startswith(NOTICE) and f"SHA-256 (PDF): {digest}" in version.body
        # the hash a seller's acceptance records covers the notice AND the PDF's hash
        assert version.body_sha256 == hashlib.sha256(version.body.encode("utf-8")).hexdigest()
        assert version.pdf_sha256 == digest and version.pdf_size_bytes == len(PDF)
        assert service.read_pdf(version) == PDF
        blob = open(os.path.join(app.config["KYC_DIRECTORY"], "contracts", version.pdf_storage_key), "rb").read()
        assert b"%PDF-" not in blob  # ciphertext on disk


def test_a_different_pdf_gives_a_different_text_hash_so_the_pdf_cannot_be_swapped_under_an_acceptance(app, seeded, kyc, tmp_path):
    assert _run(app, "--pdf", _pdf_file(tmp_path)).exit_code == 0
    other = PDF + b"one more clause"
    assert _run(app, "--pdf", _pdf_file(tmp_path, other, "v2.pdf")).exit_code == 0
    first, second = sorted(_versions(app), key=lambda v: v.created_at)
    assert first.body_sha256 != second.body_sha256 and first.pdf_sha256 != second.pdf_sha256


def test_importing_the_same_pdf_twice_creates_one_version(app, seeded, kyc, tmp_path):
    path = _pdf_file(tmp_path)
    assert _run(app, "--pdf", path).exit_code == 0
    again = _run(app, "--pdf", path)
    assert again.exit_code == 0 and "Already imported" in again.output and "Not published" in again.output
    assert len(_versions(app)) == 1


def test_a_non_pdf_and_a_server_without_the_key_write_nothing(app, seeded, kyc, tmp_path):
    bad = _run(app, "--pdf", _pdf_file(tmp_path, b"not a pdf at all", "notes.pdf"))
    assert bad.exit_code != 0 and "not a PDF" in bad.output and _versions(app) == []
    app.config.update(KYC_KEYS="")
    no_key = _run(app, "--pdf", _pdf_file(tmp_path))
    assert no_key.exit_code != 0 and "KYC_NOT_CONFIGURED" in no_key.output and "OWNER_KYC_KEYS" in no_key.output
    assert _versions(app) == []
    assert not os.path.isdir(os.path.join(str(tmp_path), "kyc", "contracts")) or not os.listdir(os.path.join(str(tmp_path), "kyc", "contracts"))


def test_publishing_the_draft_is_refused_on_a_real_server_and_allowed_on_a_practice_one(app, seeded, kyc, tmp_path):
    path = _pdf_file(tmp_path)
    assert app.config["CONTRACT_ALLOW_DRAFT"] is False
    refused = _run(app, "--pdf", path, "--publish")
    assert refused.exit_code != 0 and "NOT_LAWYER_APPROVED" in refused.output
    (version,) = _versions(app)
    assert version.published_at is None                          # created, but not live
    app.config.update(CONTRACT_ALLOW_DRAFT=True)
    done = _run(app, "--pdf", path, "--publish")
    assert done.exit_code == 0 and "Published" in done.output
    (version,) = _versions(app)
    assert version.published_at is not None and version.lawyer_approved is False
    # re-running without --publish tells the truth about a version that is already live
    again = _run(app, "--pdf", path)
    assert again.exit_code == 0 and "already live" in again.output and "Not published" not in again.output


def test_a_seller_must_read_the_pdf_and_accept_it_before_selling_and_the_acceptance_pins_the_pdf_hash(app, seeded, kyc, tmp_path):
    from app.contracts import service

    seller = make_staff(app, "pdf-seller@example.com", role_codes=["SALES"])
    app.config.update(CONTRACT_ALLOW_DRAFT=True)
    assert _run(app, "--pdf", _pdf_file(tmp_path), "--publish").exit_code == 0
    (version,) = _versions(app)
    client = app.test_client()
    force_login(client, app, seller)
    assert _gated(client.get("/leads"))                          # cannot sell yet
    page = client.get("/contracts/seller")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and NOTICE in html and 'data-contract-draft="1"' in html and 'data-contract-pdf="1"' in html
    pdf = client.get(f"/contracts/seller/pdf/{version.id}")      # the full agreement, readable BEFORE accepting
    assert pdf.status_code == 200 and pdf.data == PDF and pdf.mimetype == "application/pdf"
    accept = client.post("/contracts/seller/accept", data={
        "csrf_token": get_csrf(html), "version_id": str(version.id), "agree": "on", "text_sha256": version.body_sha256})
    assert accept.status_code == 302
    assert not _gated(client.get("/leads"))                      # now it works
    with app.app_context():
        (acceptance,) = [row for row, _ in service.seller_acceptances(seller)]
        assert acceptance.text_sha256 == version.body_sha256
        assert hashlib.sha256(PDF).hexdigest() in service.get_version(version.id).body  # the pinned text names the PDF
    copy = client.get(f"/contracts/seller/copy/{version.id}").get_data(as_text=True)
    assert version.body_sha256 in copy and hashlib.sha256(PDF).hexdigest() in copy
    with app.app_context():
        from app.extensions import db_session
        from app.models.contracts import ContractAcceptance

        assert db_session.execute(select(func.count()).select_from(ContractAcceptance)).scalar_one() == 1
