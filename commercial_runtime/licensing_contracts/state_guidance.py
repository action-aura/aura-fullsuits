"""Plain "how to fix it" text for the licence states a shopkeeper can fix
HIMSELF, in English and Arabic (r17, owner decision 10A, 2026-10-30).

WHY THIS EXISTS. CLOCK_REVIEW_REQUIRED is the state a till enters when its
date/time moves BACK past a moment it has already recorded -- typically an old
shop PC that lost its clock in a power cut and woke up in an earlier year. It
blocks new sales (it is not in ACTIVE_FAMILY), and before this module the only
thing the shopkeeper ever saw was a bare "This action is not available in the
current licensing state." plus a pill that said "Clock review required". Both
are true and neither says what to DO. A shop that cannot ring a sale at 9am
does not need a diagnosis, it needs the one thing to press.

The fix is almost always one of the shopkeeper's own hands: set the right date
and time. So the text says exactly that, says the data is safe (Part P keeps
read/backup/export/returns open in this state, and a scared owner who thinks
the data is gone does worse things), and says to restart afterwards (the
licence check re-evaluates on its next tick or on restart).

Only states a shopkeeper can fix on their own belong here. SUSPENDED / REVOKED /
EXPIRED are commercial decisions, LOCAL_STATE_CORRUPT needs support -- writing
"fix it yourself" text for those would be false advice.

Pure data + a lookup. No decision is made here: whether a request is allowed is
still `capability_guard.evaluate_capability` alone.
"""
from __future__ import annotations

from typing import Optional

from .state_machine import LicenseState

# Keep the wording in step with the Arabic below, and with the Android copy in
# ui/i18n/Strings.kt (the phone looks the English message up as a key).
CLOCK_REVIEW_MESSAGE_EN = (
    "This device's date or time looks wrong -- it is earlier than a time it has already "
    "recorded -- so new sales are paused to protect your records. To fix it: set the correct "
    "date and time in this device's settings (turn on automatic date and time), then restart "
    "the app. Your data is safe, and reports, returns and backups still work."
)

CLOCK_REVIEW_MESSAGE_AR = (
    "تاريخ هذا الجهاز أو وقته يبدو غير صحيح -- فهو أقدم من وقت سجّله الجهاز سابقًا -- "
    "لذلك أُوقفت المبيعات الجديدة لحماية سجلاتك. للحل: اضبط التاريخ والوقت الصحيحين من "
    "إعدادات هذا الجهاز (فعّل الضبط التلقائي للتاريخ والوقت)، ثم أعد تشغيل التطبيق. "
    "بياناتك بأمان، ويمكنك متابعة التقارير والمرتجعات والنسخ الاحتياطي."
)

_GUIDANCE = {
    LicenseState.CLOCK_REVIEW_REQUIRED: (CLOCK_REVIEW_MESSAGE_EN, CLOCK_REVIEW_MESSAGE_AR),
}


def guidance_for(state) -> Optional[dict]:
    """`{"en": ..., "ar": ...}` for a state the shopkeeper can fix, else None.
    Accepts a LicenseState or its string value; an unrecognised string is None
    (never raises -- the callers are error paths)."""
    if not isinstance(state, LicenseState):
        try:
            state = LicenseState(state)
        except ValueError:
            return None
    pair = _GUIDANCE.get(state)
    if pair is None:
        return None
    return {"en": pair[0], "ar": pair[1]}
