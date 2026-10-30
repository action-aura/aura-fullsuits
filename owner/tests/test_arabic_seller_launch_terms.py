"""2026-10-31 -- the verified Arabic fixes for the seller launch, pinned.

Reads the shipped catalogs (.po AND the compiled .mo the app really loads), so
a fix that only reached the .po -- or a .mo nobody recompiled -- is caught.
No database and no app needed: pure file checks.

What each group guards (and the wrong version it rejects):
  - the commission-entry error says "commission entry", not "lead";
  - Lost / Retired no longer read as "loser" / "pensioner";
  - P2 / P3 names carry the 12 / 24 month commitment (they were identical);
  - "ladder" is spelled one way (with the shadda) everywhere;
  - one term for Super Admin; commission strings say عمولات, not أرباح;
  - the page line says the COMMISSION is paid after 30 days, not the sale;
  - EVERY msgstr keeps its msgid's placeholders and has no bare % (the two
    suggestions that were NOT to be pasted put a bare % after %(percent)s
    and would crash the refund page).
"""
from __future__ import annotations

import gettext
import re
from pathlib import Path

from babel.messages.pofile import read_po

ROOT = Path(__file__).resolve().parent.parent / "translations"
ARABIC_LETTER = "ء-ي"

PLACEHOLDER = re.compile(r"%\([A-Za-z_]\w*\)[sdif]|%[sdif]|\{[A-Za-z_]\w*\}")


def _catalog(lang):
    with open(ROOT / lang / "LC_MESSAGES" / "messages.po", "rb") as fh:
        return read_po(fh, locale=lang)


def _compiled(lang):
    with open(ROOT / lang / "LC_MESSAGES" / "messages.mo", "rb") as fh:
        return gettext.GNUTranslations(fh)


def _both(msgid):
    """(.po msgstr, .mo translation) for one msgid -- they must agree."""
    po = _catalog("ar").get(msgid).string
    mo = _compiled("ar").gettext(msgid)
    assert po == mo, f"messages.mo is stale for {msgid!r}: po={po!r} mo={mo!r}"
    return po


def test_the_commission_entry_error_says_commission_not_lead():
    text = _both("Cannot change commission entry status from %(from_status)s to %(to_status)s.")
    assert "قيد العمولة" in text and "العميل المحتمل" not in text
    # the LEAD message keeps saying lead (allow-half: the fix did not touch it)
    assert "العميل المحتمل" in _both("Cannot change lead status from %(from_status)s to %(to_status)s.")


def test_lost_and_retired_are_not_a_loser_and_a_pensioner():
    assert _both("Lost") == "ضائع"
    assert _both("Retired") == "خارج الخدمة"
    assert "كعميل ضائع" in _both("A reason is required to mark a lead as lost.")


def test_the_two_monthly_packages_differ_by_their_commitment_in_arabic():
    p2 = _both("Aura Retail - Package 2 (monthly, 12-month contract)")
    p3 = _both("Aura Retail - Package 3 (monthly, 24-month contract)")
    assert p2 == "الباقة 2 (شهرية، التزام 12 شهرًا)"
    assert p3 == "الباقة 3 (شهرية، التزام 24 شهرًا)"
    assert p2 != p3


def test_ladder_is_spelled_with_the_shadda_everywhere():
    bare = re.compile(rf"(?<![{ARABIC_LETTER}])(?:ال)?سلم(?![{ARABIC_LETTER}ّ])|"
                      rf"(?<![{ARABIC_LETTER}])(?:ال)?سلم(?=ك)")
    offenders = [m.id for m in _catalog("ar") if m.id and isinstance(m.string, str) and bare.search(m.string)]
    assert offenders == []
    assert _both("Monthly commission ladder") == "سلّم العمولة الشهري"
    assert _both("Ladder total") == "مجموع السلّم"


def test_super_admin_has_one_arabic_name():
    strings = [m.string for m in _catalog("ar") if m.id and isinstance(m.string, str)]
    assert not any("المدير العام" in s for s in strings)
    assert "المشرف العام" in _both("Only a Super Admin can allow a refund after the 30 days.")


def test_commissions_are_not_called_profits():
    assert _both("My commission earnings") == "عمولاتي"
    assert _both("My commission earnings by currency") == "عمولاتي حسب العملة"
    assert "أرباح" not in _both("Only your own money is shown. Other sellers' earnings are private.")


def test_the_commission_page_pays_the_commission_after_30_days_and_counts_monthly_at_payment_2():
    pay = "A sale's commission is payable 30 days after the payment that counts it is confirmed. Extra devices never count."
    assert _both(pay).startswith("تُصرف عمولة كل بيعة بعد 30 يومًا")
    monthly = next(m.id for m in _catalog("en") if m.id.startswith("Monthly packages (2 and 3 together;"))
    assert "second payment" in monthly
    assert "دفعتها الثانية" in _both(monthly)
    # the English source no longer says a monthly package is charged once
    setup = "Setup fee (first payment; monthly packages: again each contract year)"
    assert _catalog("en").get(setup) is not None and _catalog("en").get("Setup fee (first sale only)") is None
    assert "تتكرر كل سنة" in _both(setup)


def test_every_msgstr_keeps_its_placeholders_and_has_no_bare_percent():
    problems = []
    for lang in ("ar", "en"):
        for message in _catalog(lang):
            if not message.id or not message.string:
                continue
            ids = message.id if isinstance(message.id, tuple) else (message.id,)
            strings = message.string if isinstance(message.string, tuple) else (message.string,)
            for msgid, msgstr in zip(ids, strings):
                if not msgstr:
                    continue
                if sorted(PLACEHOLDER.findall(msgid)) != sorted(PLACEHOLDER.findall(msgstr)):
                    problems.append((lang, "placeholders", msgid[:70]))
                if "python-format" in message.flags and "%" in PLACEHOLDER.sub("", msgstr.replace("%%", "")):
                    problems.append((lang, "bare %", msgid[:70]))
    assert problems == []
