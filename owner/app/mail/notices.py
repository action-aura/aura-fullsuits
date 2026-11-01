"""Customer and seller notices: a record of something the person agreed to or paid.

Why these exist (legal review v2, section (d), items 1 and 3): the shop agreement
promises the shop a copy of what it accepted and a receipt for what it paid, and the
"this was not me" window only means something if the person is TOLD. Each notice
is therefore queued the moment the event is committed, from the one place that writes
it, so no caller can forget.

RULES THAT HOLD FOR EVERY NOTICE
  * Once per EVENT. The key names the event ("customer_contract_accepted:<acceptance
    id>", "customer_payment_confirmed:<payment id>"); owner_email_outbox refuses a
    second row for it (unique index), so a retried request, a double-pressed button or
    a re-run job cannot mail the same person twice. Every kind is deduped this way.
  * Never raises, never blocks. The event is already committed; whether the mail could
    be queued (email off here, database hiccup) must not change what the person sees.
    A failure is logged (kind only, never an address or a body) and returns False.
  * Both languages in one mail, the recipient's language first (the customer account's
    stored language; a seller's locale), Arabic first when nothing is stored.
  * Parameters only ever land in the BODY (templates.py), never in a header or subject.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.mail import outbox, templates
from app.mail.invitations import email_is_set_up
from app.models.email_outbox import EmailOutbox

logger = logging.getLogger(__name__)

AMMAN = ZoneInfo("Asia/Amman")
_DEFAULT_LANG = "ar"  # the product is Arabic-first; the English block still follows


def notice_language(stored: str | None) -> str:
    value = (stored or "").strip().lower()[:2]
    return value if value in ("ar", "en") else _DEFAULT_LANG


def amman_text(moment: datetime) -> str:
    """'2026-11-01 17:05' in Asia/Amman: the business clock the shop reads."""
    return moment.astimezone(AMMAN).strftime("%Y-%m-%d %H:%M")


def queue_notice(
    kind: str, to_address: str | None, *, lang: str | None, dedupe_key: str, lines=None, link: str | None = None,
    appendix: str | None = None,
) -> bool:
    """True when a message was queued by THIS call. False when email is off, there is
    no address, this event was already mailed, or queueing failed (logged)."""
    if not to_address or not email_is_set_up():
        return False
    try:
        already = db_session.execute(
            select(EmailOutbox.id).where(EmailOutbox.dedupe_key == dedupe_key)
        ).first()
        if already is not None:
            return False
        subject, body_text, body_html = templates.render(
            kind, lang=notice_language(lang), link=link, lines=lines, appendix=appendix)
        outbox.enqueue(kind=kind, to_address=to_address, subject=subject, body_text=body_text,
                       body_html=body_html, source="owner", dedupe_key=dedupe_key)
        db_session.commit()
        return True
    except IntegrityError:
        db_session.rollback()  # two requests raced for the same event: the other one queued it
        return False
    except Exception:  # noqa: BLE001 -- the event is committed; a notice must never undo or fail it
        db_session.rollback()
        logger.warning("notice mail (%s) could not be queued", kind, exc_info=True)
        return False


# ------------------------------------------------------------ contract copy

def queue_contract_copy(acceptance, version, *, audience: str, to_address: str | None, lang: str | None,
                        link: str | None = None, consents: dict | None = None) -> bool:
    """The acceptance evidence (title, version, SHA-256, time in Amman, device) plus the
    exact text accepted, mailed to the person who accepted. `audience` is CUSTOMER or
    SELLER; the SELLER copy also carries a link to the signed-in copy download."""
    kind = "customer_contract_accepted" if audience == "CUSTOMER" else "seller_contract_accepted"
    where_en, where_ar = _where(acceptance, audience)
    when = amman_text(acceptance.accepted_at)
    lines = {
        "en": [
            f"Contract: {version.title}",
            f"Version: {version.version_label}",
            f"SHA-256 of the text you accepted: {acceptance.text_sha256}",
            f"Accepted at (Amman time): {when}",
            where_en,
        ],
        "ar": [
            f"العقد: {version.title}",
            f"الإصدار: {version.version_label}",
            f"بصمة SHA-256 للنص الذي وافقت عليه: {acceptance.text_sha256}",
            f"وقت الموافقة (بتوقيت عمّان): {when}",
            where_ar,
        ],
    }
    if consents:
        yes_no = {True: ("yes", "نعم"), False: ("no", "لا")}
        if "id_documents" in consents:
            en, ar = yes_no[consents["id_documents"] is True]
            lines["en"].append(f"Your choice, keep a photo of your ID and commercial registration: {en}")
            lines["ar"].append(f"اختيارك، الاحتفاظ بصورة هويتك وسجلك التجاري: {ar}")
        if "data_outside_jordan" in consents:
            en, ar = yes_no[consents["data_outside_jordan"] is True]
            lines["en"].append(f"Your choice, host your shop's data outside Jordan: {en}")
            lines["ar"].append(f"اختيارك، استضافة بيانات متجرك خارج الأردن: {ar}")
    appendix = f"{version.title}\n{'=' * min(max(len(version.title), 8), 72)}\n\n{version.body}\n"
    return queue_notice(
        kind, to_address, lang=lang, dedupe_key=f"{kind}:{acceptance.id}", lines=lines, link=link, appendix=appendix)


def _where(acceptance, audience: str) -> tuple[str, str]:
    if audience == "CUSTOMER":
        device = acceptance.installation_id or (acceptance.device_key_fingerprint or "-")[:12]
        return f"Device / installation: {device}", f"الجهاز / التثبيت: {device}"
    address = acceptance.ip or "-"
    return f"From address: {address}", f"من العنوان: {address}"


# ----------------------------------------------------------- payment receipts

# Only these three are the shop's receipts (recorded by our seller / confirmed by the
# company / rejected). VOIDED and REFUNDED are corrections and refunds with their own
# paperwork; they are not mailed here.
_PAYMENT_KIND = {
    "PENDING": "customer_payment_pending",
    "CONFIRMED": "customer_payment_confirmed",
    "FAILED": "customer_payment_rejected",
}
_PAYMENT_STATUS_TEXT = {
    "customer_payment_pending": ("Status: received by our seller, waiting for confirmation",
                                 "الحالة: استلمها بائعنا وهي بانتظار التأكيد"),
    "customer_payment_confirmed": ("Status: Confirmed", "الحالة: مؤكدة"),
    "customer_payment_rejected": ("Status: Rejected - please contact us", "الحالة: مرفوضة، يرجى التواصل معنا"),
}
# The tax sentence the shop agreement promises on every receipt. No tax is computed
# here (invoices are another module); this is only the statement.
TAX_INCLUSIVE_LINE = ("Prices include 16% general sales tax", "الأسعار شاملة ضريبة المبيعات العامة 16%")


def queue_payment_notice(payment, status: str) -> bool:
    """Mails the customer ACCOUNT's email a receipt line for `payment` reaching `status`
    (PENDING / CONFIRMED / FAILED; anything else is ignored). One mail per payment per
    status. A customer with no account has nobody to write to: nothing is sent. Never
    raises (see the module docstring)."""
    kind = _PAYMENT_KIND.get(status)
    if kind is None:
        return False
    try:
        from app.models.customer_accounts import CustomerAccount

        account = db_session.execute(
            select(CustomerAccount).where(CustomerAccount.customer_id == payment.customer_id)
        ).scalars().first()
        if account is None:
            return False
        number = f"PAY-{str(payment.id)[:8].upper()}"
        status_en, status_ar = _PAYMENT_STATUS_TEXT[kind]
        en = [f"Amount: {payment.amount:.2f} {payment.currency}", f"Method: {payment.method or '-'}",
              f"Payment record number: {number}"]
        ar = [f"المبلغ: {payment.amount:.2f} {payment.currency}", f"طريقة الدفع: {payment.method or '-'}",
              f"رقم سجل الدفعة: {number}"]
        if payment.reference:
            en.append(f"Reference: {payment.reference}")
            ar.append(f"المرجع: {payment.reference}")
        en += [f"Date: {payment.payment_date.isoformat()}", status_en, TAX_INCLUSIVE_LINE[0]]
        ar += [f"التاريخ: {payment.payment_date.isoformat()}", status_ar, TAX_INCLUSIVE_LINE[1]]
        return queue_notice(
            kind, account.email, lang=account.language, dedupe_key=f"{kind}:{payment.id}", lines={"en": en, "ar": ar})
    except Exception:  # noqa: BLE001 -- the payment is committed; its receipt must never undo or fail it
        db_session.rollback()
        logger.warning("payment notice (%s) could not be prepared", kind, exc_info=True)
        return False
