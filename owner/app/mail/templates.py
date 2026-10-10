"""The message texts the mail relay renders.

A product install NEVER supplies a subject or a body. It names a template kind
plus a few validated parameters (app/mail/relay.py) and Owner renders the
message from the strings in this file. That is the whole reason the relay is
not an open relay: nobody who holds a device key can make Action Aura's
mailbox send arbitrary text to arbitrary people. Parameters only ever land in
the BODY (escaped for HTML), never in a header or the subject.

Every message is English plus Arabic in one mail; `lang` ('ar') only decides
which block comes first. The footer says who sent it and that replies are not
read, and the worker sets Reply-To to support@.
"""
from __future__ import annotations

from html import escape
from typing import Optional

from app.mail.relay_limits import LINK_MAX

SUPPORT_ADDRESS = "support@actionaura.me"

_SUBJECTS = {
    "verify_email": ("Verify your Aura Retail email address", "تأكيد بريدك الإلكتروني في Aura Retail"),
    "password_reset": ("Reset your Aura Retail password", "إعادة تعيين كلمة مرور Aura Retail"),
    "low_stock": ("Low stock alert from Aura Retail", "تنبيه نقص مخزون من Aura Retail"),
    "shift_close": ("Shift closed - Aura Retail", "إغلاق الوردية - Aura Retail"),
    "report_summary": ("Your Aura Retail report", "تقريرك من Aura Retail"),
    "staff_invitation": ("You have been invited to Aura Owner", "تمت دعوتك إلى Aura Owner"),
    "staff_password_reset": ("Reset your Aura Owner password", "إعادة تعيين كلمة مرور Aura Owner"),
    "test": ("Aura Owner test message", "رسالة اختبار من Aura Owner"),
}

# (english intro, arabic intro) per kind -- the link / list is added below.
_INTROS = {
    "verify_email": ("Confirm this is your email address to finish setting up Aura Retail:",
                     "أكّد أن هذا هو بريدك الإلكتروني لإكمال إعداد Aura Retail:"),
    "password_reset": ("Reset your Aura Retail password:",
                       "أعد تعيين كلمة مرور Aura Retail الخاصة بك:"),
    "low_stock": ("These products are running low:", "هذه المنتجات على وشك النفاد:"),
    "shift_close": ("A shift was closed. Summary:", "تم إغلاق وردية. الملخص:"),
    "report_summary": ("Your report:", "تقريرك:"),
    "staff_invitation": ("You have been invited to join Aura Owner. Open this one-time link to set up your account:",
                         "تمت دعوتك للانضمام إلى Aura Owner. افتح هذا الرابط لمرة واحدة لإعداد حسابك:"),
    "staff_password_reset": ("Someone asked to reset the password for your Aura Owner account. Open this link to choose a new one:",
                             "طُلب إعادة تعيين كلمة مرور حسابك في Aura Owner. افتح هذا الرابط لاختيار كلمة مرور جديدة:"),
    "test": ("This is a test message from Aura Owner. If you can read it, email sending works.",
             "هذه رسالة اختبار من Aura Owner. إذا استطعت قراءتها فإرسال البريد يعمل."),
}

_LINK_NOTES = {
    "verify_email": ("This link expires in 24 hours. If you didn't create this account, you can ignore this email.",
                     "تنتهي صلاحية هذا الرابط خلال 24 ساعة. إذا لم تنشئ هذا الحساب فتجاهل هذه الرسالة."),
    "password_reset": ("This link expires in 1 hour and can only be used once. If you didn't request this, ignore this email - your password will not change.",
                       "تنتهي صلاحية هذا الرابط خلال ساعة ويمكن استخدامه مرة واحدة. إذا لم تطلب ذلك فتجاهل هذه الرسالة - لن تتغير كلمة مرورك."),
    "staff_invitation": ("The link works once and expires in 3 days.",
                         "يعمل الرابط مرة واحدة وتنتهي صلاحيته خلال 3 أيام."),
    "staff_password_reset": ("The link works for 30 minutes and stops working once your password changes. If you did not ask for this, ignore this email - your password will not change.",
                             "يعمل الرابط لمدة 30 دقيقة ويتوقف عن العمل بعد تغيير كلمة المرور. إذا لم تطلب ذلك فتجاهل هذه الرسالة - لن تتغير كلمة مرورك."),
}


def _footer() -> tuple:
    # No caller-supplied shop name in the footer: it was free text from the
    # relay, placed in a mail sent from OUR address (phishing text with our
    # reputation; found by adversarial verification 2026-10-10).
    en = f"Sent by Aura Retail - do not reply. Questions: {SUPPORT_ADDRESS}"
    ar = f"أُرسلت من Aura Retail - يرجى عدم الرد. للاستفسار: {SUPPORT_ADDRESS}"
    return en, ar


def _block_text(kind: str, idx: int, link: Optional[str], lines: list) -> str:
    parts = [_INTROS[kind][idx]]
    if link:
        parts += ["", link]
    if lines:
        parts += [""] + [f"- {line}" for line in lines]
    note = _LINK_NOTES.get(kind)
    if note:
        parts += ["", note[idx]]
    parts += ["", _footer()[idx]]
    return "\n".join(parts)


def _block_html(kind: str, idx: int, link: Optional[str], lines: list) -> str:
    rtl = idx == 1
    out = [f'<div dir="{"rtl" if rtl else "ltr"}" style="margin:0 0 24px 0">',
           f"<p>{escape(_INTROS[kind][idx])}</p>"]
    if link:
        out.append(f'<p><a href="{escape(link, quote=True)}">{escape(link)}</a></p>')
    if lines:
        out.append("<ul>" + "".join(f"<li>{escape(line)}</li>" for line in lines) + "</ul>")
    note = _LINK_NOTES.get(kind)
    if note:
        out.append(f"<p>{escape(note[idx])}</p>")
    out.append(f'<p style="color:#666;font-size:12px">{escape(_footer()[idx])}</p>')
    out.append("</div>")
    return "".join(out)


def render(kind: str, *, lang: str = "en", link: Optional[str] = None, lines: Optional[list] = None) -> tuple:
    """Returns (subject, body_text, body_html). `link` and `lines` must already
    have been validated by the caller; they are placed in the body only."""
    if kind not in _SUBJECTS:
        raise KeyError(kind)
    if link is not None and len(link) > LINK_MAX:
        raise ValueError("link too long")
    lines = list(lines or [])
    order = (1, 0) if lang == "ar" else (0, 1)
    subject = _SUBJECTS[kind][order[0]]
    body_text = ("\n\n" + "-" * 24 + "\n\n").join(_block_text(kind, i, link, lines) for i in order)
    body_html = "".join(_block_html(kind, i, link, lines) for i in order)
    return subject, body_text, body_html
