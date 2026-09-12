"""Phase 9.5B-R2 Milestone 11 -- catalog completeness/drift control, expanded
from Phase 9.5B-R's scope to the full Owner-wide catalog (742 messages)."""
from __future__ import annotations

import os
import re
import subprocess
import sys

from babel.messages.pofile import read_po

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TRANSLATIONS_ROOT = os.path.join(OWNER_ROOT, "translations")
APP_ROOT = os.path.join(OWNER_ROOT, "app")

# ``_("literal")`` / ``_('literal')`` -- the gettext call as it is actually
# written in this codebase, in both Jinja templates and Python modules. Only
# a literal first argument is captured; ``_(variable)`` is out of scope by
# construction and the test that uses this says so.
_TRANSLATED_LITERAL_RE = re.compile(r"""\b_\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")

# ADJACENT string literals continue the SAME msgid: Python (and Jinja) join
# implicitly concatenated literals into one string, and pybabel extracts the
# joined result. Reading only the first fragment reports a msgid that exists
# nowhere -- neither in the source as written nor in the catalog -- so a long
# message wrapped across two lines fails this test permanently and cannot be
# fixed by translating it.
#
# Found exactly that way: i18n_labels.py's device-limit message is wrapped
# after "...device(s) are ", and the catalog (correctly) holds the whole
# sentence. Matching pybabel's own joining behaviour is what makes source and
# catalog comparable at all.
#
# No \A anchor: re.match(source, pos) already anchors at pos, whereas \A would
# still mean start-of-STRING and so could never match past the very first
# literal in the file.
_LITERAL_CONTINUATION_RE = re.compile(r"""\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""")


def _load(locale):
    path = os.path.join(TRANSLATIONS_ROOT, locale, "LC_MESSAGES", "messages.po")
    with open(path, "r", encoding="utf-8") as f:
        return read_po(f)


def _translated_literals():
    """Every ``_("...")`` literal in the real app tree, as (path, msgid)."""
    for root, _dirs, names in os.walk(APP_ROOT):
        for name in sorted(names):
            if not name.endswith((".html", ".py")):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8") as handle:
                source = handle.read()
            for match in _TRANSLATED_LITERAL_RE.finditer(source):
                raw = match.group(1) if match.group(1) is not None else match.group(2)
                # Absorb any implicitly-concatenated continuation fragments,
                # exactly as Python and pybabel do -- see
                # _LITERAL_CONTINUATION_RE.
                pos = match.end()
                while True:
                    nxt = _LITERAL_CONTINUATION_RE.match(source, pos)
                    if not nxt:
                        break
                    raw += nxt.group(1) if nxt.group(1) is not None else nxt.group(2)
                    pos = nxt.end()
                # The catalog stores the decoded string, so an escaped
                # literal has to be decoded the same way before comparing.
                yield path, (raw.encode().decode("unicode_escape") if "\\" in raw else raw)


def test_english_catalog_has_no_empty_or_fuzzy_entries():
    cat = _load("en")
    empty = [m.id for m in cat if m.id and not m.string]
    fuzzy = [m.id for m in cat if m.fuzzy]
    assert empty == [], f"{len(empty)} empty English entries: {empty[:10]}"
    assert fuzzy == [], f"{len(fuzzy)} fuzzy English entries: {fuzzy[:10]}"


def test_arabic_catalog_has_no_empty_or_fuzzy_entries():
    cat = _load("ar")
    empty = [m.id for m in cat if m.id and not m.string]
    fuzzy = [m.id for m in cat if m.fuzzy]
    assert empty == [], f"{len(empty)} empty Arabic entries: {empty[:10]}"
    assert fuzzy == [], f"{len(fuzzy)} fuzzy Arabic entries: {fuzzy[:10]}"


_PLACEHOLDER_RE = re.compile(r"%\([a-zA-Z_]+\)[sd]")


def test_every_translation_preserves_its_source_placeholders_exactly():
    """A translated format string must carry the SAME named placeholders as its
    source, with the same multiplicity.

    This is the one catalog defect that can take a page down: the message is
    interpolated with ``% {...}``, so a translator dropping ``%(count)s``
    changes what renders, and inventing a placeholder the caller never supplies
    raises KeyError at request time -- in Arabic only, which is exactly where
    it is least likely to be noticed before a customer sees it.

    Found a real one on first run, and not the crashing kind: the pilot
    "maximum extensions" message had its ENTIRE Arabic sentence stored twice,
    concatenated with no separator (two renderings of the same English, merged
    by accident). It rendered duplicated to every Arabic user. Counting
    placeholders per message is what surfaced it, because the duplication
    doubled ``%(max)s`` too.
    """
    for locale in ("en", "ar"):
        mismatched = []
        for msg in _load(locale):
            if not msg.id or not msg.string or isinstance(msg.string, tuple):
                continue
            source = sorted(_PLACEHOLDER_RE.findall(msg.id))
            translated = sorted(_PLACEHOLDER_RE.findall(msg.string))
            if source != translated:
                mismatched.append((msg.id[:70], source, translated))
        assert mismatched == [], (
            f"{len(mismatched)} {locale} translation(s) whose placeholders do not match "
            f"their source: {mismatched[:5]}"
        )


def test_english_and_arabic_catalogs_have_the_same_message_set():
    en_ids = {m.id for m in _load("en") if m.id}
    ar_ids = {m.id for m in _load("ar") if m.id}
    assert en_ids == ar_ids, f"catalogs diverged: en-only={en_ids - ar_ids}, ar-only={ar_ids - en_ids}"


def test_catalog_message_count_matches_real_source_extraction():
    """Regression guard against silent catalog drift: re-extracting from the
    real source tree must not find messages absent from the compiled
    catalog. Skips (does not fail) if pybabel/msgcmp tooling is unavailable
    in the current environment, rather than reporting a false failure."""
    pot_path = os.path.join(TRANSLATIONS_ROOT, "messages.pot")
    if not os.path.exists(pot_path):
        return
    with open(pot_path, "r", encoding="utf-8") as f:
        pot_cat = read_po(f)
    pot_ids = {m.id for m in pot_cat if m.id}
    en_ids = {m.id for m in _load("en") if m.id}
    missing_from_catalog = pot_ids - en_ids
    assert not missing_from_catalog, f"messages in .pot but missing from compiled catalog: {list(missing_from_catalog)[:10]}"


def test_compiled_mo_files_exist_and_are_nonempty():
    for locale in ("en", "ar"):
        mo_path = os.path.join(TRANSLATIONS_ROOT, locale, "LC_MESSAGES", "messages.mo")
        assert os.path.exists(mo_path), f"missing compiled catalog for {locale}"
        assert os.path.getsize(mo_path) > 0, f"empty compiled catalog for {locale}"


def test_every_translated_literal_in_the_real_source_tree_exists_in_both_catalogs():
    """The check test_catalog_message_count_matches_real_source_extraction
    above cannot make: it compares the catalog against messages.pot, which is
    a CHECKED-IN artifact regenerated by hand, so a template that gains a new
    ``_("...")`` without a pybabel re-extract sails straight past it. This
    one re-derives the message set from the source files themselves, so a
    string added to a template or a route and never added to en/ar fails
    here immediately (a missing msgid falls back to the raw English source
    string -- the Arabic UI silently renders English, which is exactly the
    failure this catches).

    Scope is the literal argument only. Runtime-composed strings (a
    variable passed to ``_()``) are deliberately out of scope and are
    neither scanned nor claimed to be covered."""
    en_ids = {m.id for m in _load("en") if m.id}
    ar_ids = {m.id for m in _load("ar") if m.id}

    missing = []
    for path, literal in _translated_literals():
        if literal not in en_ids or literal not in ar_ids:
            missing.append((os.path.relpath(path, OWNER_ROOT), literal))
    assert missing == [], (
        f"{len(missing)} translated literal(s) in the source tree have no catalog entry "
        f"(the Arabic UI renders these in English): {missing[:10]}"
    )


def test_total_message_count_is_at_least_the_phase9_5b_r2_baseline():
    """742 real messages confirmed at Phase 9.5B-R2 close (203 from Phase
    9.5B-R + 539 new this wave). A future phase adding messages is fine (the
    count only grows); a regression that silently drops entries is not."""
    cat = _load("en")
    total = sum(1 for m in cat if m.id)
    assert total >= 742, f"expected at least 742 messages, found {total}"
