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
    # Seller agreement clause 7: a seller's cash is more than 24 hours old and has not been handed in.
    # Goes to the seller and to everyone who may confirm payments; the facts ride in `lines`.
    "seller_cash_overdue": ("Cash not handed in within 24 hours - Aura Owner", "مبلغ نقدي لم يُسلَّم خلال 24 ساعة - Aura Owner"),
    # Clause 8: a customer a seller registered has been released (15 days with no subscription and no activity).
    "seller_customer_released": ("A customer is no longer yours - Aura Owner", "عميل لم يعد لك - Aura Owner"),
    # Seller agreement clauses 16-18 (sanctions and the end of the agreement). Owner's own code only.
    "seller_sanction_notice": ("A notice about your seller agreement - please reply in Aura Owner",
                               "إشعار بخصوص اتفاقية البائع - يرجى الرد في Aura Owner"),
    "seller_sanction_decision": ("A decision about your seller agreement", "قرار بخصوص اتفاقية البائع"),
    "seller_agreement_ending": ("Your seller agreement with Action Aura is ending",
                                "اتفاقية البائع الخاصة بك مع Action Aura ستنتهي"),
    # Step 2 CA1: customer website account mail. NOT in the product relay's KINDS
    # (app/mail/relay.py): only Owner's own code can queue these.
    "customer_invitation": ("Set up your Action Aura account", "إعداد حسابك في Action Aura"),
    "customer_welcome": ("Welcome to Action Aura - confirm your email", "مرحباً بك في Action Aura - أكّد بريدك الإلكتروني"),
    "customer_password_reset": ("Reset your Action Aura password", "إعادة تعيين كلمة مرور Action Aura"),
    "customer_password_changed": ("Your Action Aura password was changed", "تم تغيير كلمة مرور Action Aura"),
    "customer_email_change": ("Confirm your new Action Aura email address", "تأكيد بريدك الإلكتروني الجديد في Action Aura"),
    "customer_email_changed_notice": ("The email address on your Action Aura account is changing",
                                      "يجري تغيير البريد الإلكتروني في حسابك على Action Aura"),
    # Customer / seller notices (feat/customer-notices). Built by app/mail/notices.py,
    # never reachable from the product relay's KINDS. Each is queued once per event.
    "customer_contract_accepted": ("Your copy of the Action Aura contract you accepted",
                                   "نسختك من عقد Action Aura الذي وافقت عليه"),
    "seller_contract_accepted": ("Your copy of the Action Aura seller agreement you accepted",
                                 "نسختك من اتفاقية البائع في Action Aura التي وافقت عليها"),
    "customer_payment_pending": ("Action Aura: payment recorded, waiting for confirmation",
                                 "Action Aura: تم تسجيل دفعة وهي بانتظار التأكيد"),
    "customer_payment_confirmed": ("Action Aura: payment confirmed", "Action Aura: تم تأكيد الدفعة"),
    "customer_payment_rejected": ("Action Aura: payment not accepted", "Action Aura: لم تُقبل الدفعة"),
}

# Messages that are a record the person may need to dispute: the footer invites a
# reply instead of saying "do not reply" (see _footer), and the mail carries its own
# per-language lines and, for a contract copy, the accepted text itself.
NOTICE_KINDS = frozenset({
    "customer_contract_accepted", "seller_contract_accepted",
    "customer_payment_pending", "customer_payment_confirmed", "customer_payment_rejected",
})
NOTICE_CONTACT = "info@actionaura.me"

# Kinds whose body carries a live one-time link. The sender clears the body the
# moment such a message is SENT (app/mail/outbox.py::mark_sent), so a finished
# mail row never keeps a usable token.
LINK_BEARING_KINDS = frozenset({"customer_invitation", "customer_welcome", "customer_password_reset", "customer_email_change"})
CUSTOMER_KIND_PREFIX = "customer_"

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
    "seller_cash_overdue": ("A cash payment collected by a seller is more than 24 hours old and has not been handed in to the company:",
                            "مبلغ نقدي قبضه مندوب مضى عليه أكثر من 24 ساعة ولم يُسلَّم إلى الشركة:"),
    "seller_customer_released": ("A customer you registered has been released to the company:",
                                 "عميل سجّلته أُعيد إلى الشركة:"),
    "seller_sanction_notice": ("The company has opened a case about your seller agreement. You have 2 working days to read it and reply in Aura Owner before any decision is made:",
                              "فتحت الشركة قضية بخصوص اتفاقية البائع الخاصة بك. لديك يومي عمل لقراءتها والرد عليها في Aura Owner قبل اتخاذ أي قرار:"),
    "seller_sanction_decision": ("The company has decided on the case about your seller agreement. Sign in to Aura Owner to read it:",
                                 "اتخذت الشركة قرارًا في القضية المتعلقة باتفاقية البائع الخاصة بك. سجّل الدخول إلى Aura Owner لقراءته:"),
    "seller_agreement_ending": ("Your seller agreement is ending. Until then you can keep working as usual:",
                                "اتفاقية البائع الخاصة بك ستنتهي. يمكنك الاستمرار في العمل كالمعتاد حتى ذلك الحين:"),
    "customer_invitation": ("Action Aura has set up an account for you. Open this one-time link to choose your password:",
                            "أنشأت Action Aura حساباً لك. افتح هذا الرابط لمرة واحدة لاختيار كلمة المرور:"),
    "customer_welcome": ("Your seller has set up your Action Aura account. Sign in on our website with this email address and the temporary password your seller gave you (we never send passwords by email); you will be asked to choose your own. Please also confirm this address with the link below:",
                         "قام البائع بإعداد حسابك في Action Aura. سجّل الدخول على موقعنا بهذا البريد وكلمة المرور المؤقتة التي أعطاك إياها البائع (لا نرسل كلمات المرور بالبريد)، وسيُطلب منك اختيار كلمة مرور خاصة بك. يرجى أيضاً تأكيد هذا البريد عبر الرابط التالي:"),
    "customer_password_reset": ("Someone asked to reset the password of your Action Aura account. Open this link to choose a new one:",
                                "طُلب إعادة تعيين كلمة مرور حسابك في Action Aura. افتح هذا الرابط لاختيار كلمة مرور جديدة:"),
    "customer_password_changed": ("The password of your Action Aura account was just changed. Your licences and devices are not affected.",
                                  "تم تغيير كلمة مرور حسابك في Action Aura للتو. لا يتأثر ترخيصك ولا أجهزتك."),
    "customer_email_change": ("Open this link to confirm this address as the new sign-in email of your Action Aura account:",
                              "افتح هذا الرابط لتأكيد هذا العنوان بريداً جديداً لتسجيل الدخول إلى حسابك في Action Aura:"),
    "customer_email_changed_notice": ("The sign-in email of your Action Aura account is being changed to the address below. "
                                      "If this was not you, contact Action Aura at once.",
                                      "يجري تغيير بريد تسجيل الدخول لحسابك في Action Aura إلى العنوان أدناه. "
                                      "إذا لم تكن أنت، تواصل مع Action Aura فوراً."),
    "customer_contract_accepted": ("You accepted the Action Aura contract below. This message is your copy: the details of your acceptance, then the exact text you accepted.",
                                   "لقد وافقت على عقد Action Aura أدناه. هذه الرسالة هي نسختك: تفاصيل موافقتك ثم النص الذي وافقت عليه حرفياً."),
    "seller_contract_accepted": ("You accepted the Action Aura seller agreement below. This message is your copy: the details of your acceptance, then the exact text you accepted.",
                                 "لقد وافقت على اتفاقية البائع في Action Aura أدناه. هذه الرسالة هي نسختك: تفاصيل موافقتك ثم النص الذي وافقت عليه حرفياً."),
    "customer_payment_pending": ("Our seller recorded a payment for your shop. It is received by our seller and waiting for confirmation by Action Aura:",
                                 "سجّل بائعنا دفعة عن متجرك. استلمها بائعنا وهي بانتظار التأكيد من Action Aura:"),
    "customer_payment_confirmed": ("Action Aura confirmed this payment for your shop:",
                                   "أكدت Action Aura هذه الدفعة الخاصة بمتجرك:"),
    "customer_payment_rejected": ("Action Aura could not accept this payment record for your shop. Please contact us:",
                                  "لم تتمكن Action Aura من قبول سجل هذه الدفعة الخاص بمتجرك. يرجى التواصل معنا:"),
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
    "customer_invitation": ("The link works once and expires in 3 days. If you were not expecting this, ignore this email.",
                            "يعمل الرابط مرة واحدة وتنتهي صلاحيته خلال 3 أيام. إذا لم تكن تتوقع هذه الرسالة فتجاهلها."),
    "customer_welcome": ("The link works once and expires in 14 days. If you were not expecting this, ignore this email.",
                         "يعمل الرابط مرة واحدة وتنتهي صلاحيته خلال 14 يوماً. إذا لم تكن تتوقع هذه الرسالة فتجاهلها."),
    "customer_password_reset": ("The link works for 30 minutes and only once. If you did not ask for this, ignore this email - your password will not change.",
                                "يعمل الرابط لمدة 30 دقيقة ولمرة واحدة. إذا لم تطلب ذلك فتجاهل هذه الرسالة - لن تتغير كلمة مرورك."),
    "customer_password_changed": ("If this was not you, reset your password now from the sign-in page and contact Action Aura.",
                                  "إذا لم تكن أنت، أعد تعيين كلمة المرور الآن من صفحة تسجيل الدخول وتواصل مع Action Aura."),
    "customer_email_change": ("The link works once and expires in 24 hours. Until you use it, the old address stays in force.",
                              "يعمل الرابط مرة واحدة وتنتهي صلاحيته خلال 24 ساعة. يبقى العنوان القديم ساري المفعول إلى أن تستخدمه."),
    # The 7-day "this was not me" window the contract promises (cl. 20) for an acceptance.
    "customer_contract_accepted": (f"If you did not accept this yourself, reply to this email or write to {NOTICE_CONTACT} within 7 days.",
                                   f"إذا لم تقم بالموافقة بنفسك، فردّ على هذه الرسالة أو راسل {NOTICE_CONTACT} خلال 7 أيام."),
    "seller_contract_accepted": (f"If you did not accept this yourself, reply to this email or write to {NOTICE_CONTACT} within 7 days.",
                                 f"إذا لم تقم بالموافقة بنفسك، فردّ على هذه الرسالة أو راسل {NOTICE_CONTACT} خلال 7 أيام."),
    "customer_payment_pending": (f"If this is not right, reply to this email or write to {NOTICE_CONTACT}.",
                                 f"إذا كان في هذا خطأ، فردّ على هذه الرسالة أو راسل {NOTICE_CONTACT}."),
    "customer_payment_confirmed": (f"If this is not right, reply to this email or write to {NOTICE_CONTACT}.",
                                   f"إذا كان في هذا خطأ، فردّ على هذه الرسالة أو راسل {NOTICE_CONTACT}."),
    "customer_payment_rejected": (f"Reply to this email or write to {NOTICE_CONTACT} so we can sort it out together.",
                                  f"ردّ على هذه الرسالة أو راسل {NOTICE_CONTACT} لنحلّ الأمر معاً."),
}


def _footer(kind: str = "") -> tuple:
    if kind in NOTICE_KINDS:
        return ("Sent by Action Aura. Questions: " + NOTICE_CONTACT,
                "أُرسلت من Action Aura. للاستفسار: " + NOTICE_CONTACT)
    if kind.startswith(CUSTOMER_KIND_PREFIX):
        # No support address: whether that mailbox exists is not known yet, and
        # a customer must not be told to write to one that bounces.
        return ("Sent by Action Aura - please do not reply to this message.",
                "أُرسلت من Action Aura - يرجى عدم الرد على هذه الرسالة.")
    # No caller-supplied shop name in the footer: it was free text from the
    # relay, placed in a mail sent from OUR address (phishing text with our
    # reputation; found by adversarial verification 2026-10-10).
    en = f"Sent by Aura Retail - do not reply. Questions: {SUPPORT_ADDRESS}"
    ar = f"أُرسلت من Aura Retail - يرجى عدم الرد. للاستفسار: {SUPPORT_ADDRESS}"
    return en, ar


def _lines_for(lines, idx: int) -> list:
    """`lines` is one list shared by both language blocks, or {"en": [...], "ar": [...]}
    when the lines themselves are words (a notice's amounts and labels)."""
    if isinstance(lines, dict):
        return list(lines.get(("en", "ar")[idx]) or [])
    return list(lines or [])


def _block_text(kind: str, idx: int, link: Optional[str], lines) -> str:
    lines = _lines_for(lines, idx)
    parts = [_INTROS[kind][idx]]
    if link:
        parts += ["", link]
    if lines:
        parts += [""] + [f"- {line}" for line in lines]
    note = _LINK_NOTES.get(kind)
    if note:
        parts += ["", note[idx]]
    parts += ["", _footer(kind)[idx]]
    return "\n".join(parts)


def _block_html(kind: str, idx: int, link: Optional[str], lines) -> str:
    lines = _lines_for(lines, idx)
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
    out.append(f'<p style="color:#666;font-size:12px">{escape(_footer(kind)[idx])}</p>')
    out.append("</div>")
    return "".join(out)


def render(kind: str, *, lang: str = "en", link: Optional[str] = None, lines=None,
           appendix: Optional[str] = None) -> tuple:
    """Returns (subject, body_text, body_html). `link` and `lines` must already
    have been validated by the caller; they are placed in the body only.
    `appendix` is a block of verbatim text after both language blocks (a contract
    copy: the exact words accepted); it is escaped for HTML and never in the subject."""
    if kind not in _SUBJECTS:
        raise KeyError(kind)
    if link is not None and len(link) > LINK_MAX:
        raise ValueError("link too long")
    if not isinstance(lines, dict):
        lines = list(lines or [])
    order = (1, 0) if lang == "ar" else (0, 1)
    subject = _SUBJECTS[kind][order[0]]
    divider = "\n\n" + "-" * 24 + "\n\n"
    body_text = divider.join(_block_text(kind, i, link, lines) for i in order)
    body_html = "".join(_block_html(kind, i, link, lines) for i in order)
    if appendix:
        body_text += divider + appendix
        body_html += f'<pre dir="auto" style="white-space:pre-wrap;font-family:inherit">{escape(appendix)}</pre>'
    return subject, body_text, body_html
