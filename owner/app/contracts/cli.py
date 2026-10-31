"""`flask contracts import-seller-draft --pdf <file>` -- seed the owner's seller agreement.

THE PROBLEM. The owner's agreement exists as an Arabic PDF. Extracting its text reverses the
Arabic word order, so that text must NOT become the legal body a seller accepts. Until the real
text is pasted from the owner's source document, the version carries:
  * the PDF itself, stored ENCRYPTED as the version's archive copy (the seller reads and
    downloads it from /contracts/seller before accepting), and
  * a short Arabic notice as its text body, which names the PDF's SHA-256.

WHAT THE HASH COVERS. A version's body_sha256 is the SHA-256 of its BODY text (the database
re-derives and CHECKs it) and a PDF cannot be part of that text. So the PDF's own SHA-256 is
written INTO the body as a line, and therefore body_sha256 covers "notice + PDF hash": the
seller's recorded acceptance (text_sha256) pins the exact PDF bytes. The PDF hash is also kept
in pdf_sha256 and is in the seller's downloaded copy. If the PDF is later replaced, that is a
new version (versions never change), and every seller is asked again.

NOT LAWYER-APPROVED. The version is created with lawyer_approved = false. Publishing it needs
the practice-only switch OWNER_CONTRACT_ALLOW_DRAFT=1 (the seller page then shows a DRAFT banner).
A real server cannot publish it until a lawyer's approval is recorded on it (or on its replacement).

TO REPLACE THE NOTICE WITH THE REAL TEXT LATER: Contracts -> New version (audience SELLER,
language ar) -> paste the text from the owner's source document into the text box, attach the same
PDF, record the lawyer's approval, publish. That is a new version, so every seller accepts again.

Needs OWNER_KYC_KEYS (the same key set the customer documents use) to encrypt the PDF.
"""
from __future__ import annotations

import hashlib
import os

import click
from flask.cli import AppGroup
from sqlalchemy import select

from app.contracts import service
from app.extensions import db_session
from app.models.contracts import ContractVersion

contracts_cli = AppGroup("contracts", help="E-contract commands.")

# Arabic: "The full text of the agreement is attached as a PDF below -- please read it fully before accepting".
NOTICE_AR = "نص الاتفاقية الكامل مرفق بصيغة PDF أدناه — يرجى قراءته كاملًا قبل الموافقة"
TITLE_AR = "عقد تعاون مبيعات بالعمولة — Action Aura"
PDF_HASH_LINE = "SHA-256 (PDF): {digest}"


def seller_draft_body(pdf_sha256: str) -> str:
    """The body text: the notice, a blank line, and the PDF's hash (so body_sha256 covers both)."""
    return f"{NOTICE_AR}\n\n{PDF_HASH_LINE.format(digest=pdf_sha256)}"


@contracts_cli.command("import-seller-draft")
@click.option("--pdf", "pdf_path", required=True, type=click.Path(exists=True, dir_okay=False, readable=True),
              help="The owner's seller agreement as a PDF (at most 10 MB).")
@click.option("--language", default="ar", type=click.Choice(["ar", "en"]), show_default=True)
@click.option("--label", default=None, help="Version label (default: owner-pdf-<first 8 of the PDF hash>).")
@click.option("--publish", is_flag=True, default=False,
              help="Also publish it. Refused unless OWNER_CONTRACT_ALLOW_DRAFT=1 (practice servers only).")
def import_seller_draft(pdf_path: str, language: str, label: str | None, publish: bool):
    """Store the owner's PDF as a DRAFT (not lawyer-approved) SELLER contract version.

    The text body is only a short notice naming the PDF; paste the real text from the owner's
    source document later (see this module's docstring). Idempotent: the same PDF imported twice
    reports the existing version instead of creating another."""
    with open(pdf_path, "rb") as handle:
        content = handle.read()
    if not content.startswith(b"%PDF-"):
        raise click.ClickException("That file is not a PDF.")
    digest = hashlib.sha256(content).hexdigest()
    body = seller_draft_body(digest)
    existing = db_session.execute(
        select(ContractVersion).where(ContractVersion.audience == "SELLER", ContractVersion.language == language,
                                      ContractVersion.pdf_sha256 == digest, ContractVersion.body_sha256 == service.body_sha256(body))
    ).scalars().first()
    if existing is not None:
        version = existing
        click.echo(f"Already imported: version {version.version_label} ({version.id}).")
    else:
        try:
            version = service.create_version(
                audience="SELLER", language=language, version_label=label or f"owner-pdf-{digest[:8]}", title=TITLE_AR,
                body=body, lawyer_approved=False, approval_note="", pdf_bytes=content, actor_staff_user_id=None,
            )
        except service.ContractError as exc:
            hint = {"KYC_NOT_CONFIGURED": " (set OWNER_KYC_KEYS: the PDF is stored encrypted)",
                    "LABEL_TAKEN": " (choose another --label)", "PDF_TOO_LARGE": " (10 MB at most)"}.get(exc.code, "")
            raise click.ClickException(f"Could not import: {exc.code}{hint}")
        click.echo(f"Created DRAFT seller contract {version.version_label} ({version.id}); lawyer_approved=false.")
    click.echo(f"PDF sha256: {digest}")
    click.echo(f"Text sha256 (covers the notice and the PDF hash): {version.body_sha256}")
    if publish:
        if version.published_at is not None and version.retired_at is None:
            click.echo("Already live.")
        else:
            try:
                service.publish(version, actor_staff_user_id=None)
            except service.ContractError as exc:
                raise click.ClickException(
                    f"Not published: {exc.code}. A draft can only go live on a practice server "
                    f"(OWNER_CONTRACT_ALLOW_DRAFT=1); {os.environ.get('OWNER_CONTRACT_ALLOW_DRAFT', '') or 'it is not set here'}.")
            click.echo("Published. Sellers must now accept it before they can sell.")
    elif version.published_at is not None and version.retired_at is None:
        click.echo("It is already live.")
    else:
        click.echo("Not published. Publish it from the Contracts page (or re-run with --publish on a practice server).")
