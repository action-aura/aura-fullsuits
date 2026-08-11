"""Phase 9.5B-R Milestone 16 -- catalog completeness tests.

Real .po/.mo files, not mocked -- reads the actual compiled catalogs this
phase built.
"""
from __future__ import annotations

import os

from babel.messages.pofile import read_po

TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations")


def _read_catalog(locale: str):
    path = os.path.join(TRANSLATIONS_DIR, locale, "LC_MESSAGES", "messages.po")
    with open(path, "rb") as f:
        return read_po(f, locale=locale)


def test_english_catalog_exists_and_compiles():
    mo_path = os.path.join(TRANSLATIONS_DIR, "en", "LC_MESSAGES", "messages.mo")
    assert os.path.isfile(mo_path)
    assert os.path.getsize(mo_path) > 0


def test_arabic_catalog_exists_and_compiles():
    mo_path = os.path.join(TRANSLATIONS_DIR, "ar", "LC_MESSAGES", "messages.mo")
    assert os.path.isfile(mo_path)
    assert os.path.getsize(mo_path) > 0


def test_no_missing_arabic_translation_in_current_scope():
    catalog = _read_catalog("ar")
    empty = [m.id for m in catalog if m.id and not m.string]
    assert empty == [], f"{len(empty)} Arabic msgid(s) have an empty translation: {empty[:10]}"


def test_no_empty_english_translation():
    catalog = _read_catalog("en")
    empty = [m.id for m in catalog if m.id and not m.string]
    assert empty == []


def test_english_source_locale_is_the_real_ui_string():
    """Non-Negotiable Principle 2: English is the source/reference locale --
    every English msgstr equals its own msgid (identity), never a paraphrase
    that would drift from what the template/route literally says."""
    catalog = _read_catalog("en")
    mismatched = [m.id for m in catalog if m.id and m.string != m.id]
    assert mismatched == []


def test_no_duplicate_msgid_in_either_catalog():
    for locale in ("en", "ar"):
        catalog = _read_catalog(locale)
        ids = [m.id for m in catalog if m.id]
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert duplicates == set(), f"{locale}: duplicate msgid(s): {duplicates}"


def test_arabic_translations_are_real_arabic_not_placeholder_text():
    """A cheap but real signal: every non-trivial Arabic translation should
    contain at least one Arabic-script character -- catches an accidentally
    left-empty-then-filled-with-English placeholder."""
    catalog = _read_catalog("ar")
    # Deliberate, real exceptions -- see translation-style-guide.md:
    # "Aura Owner" is the brand name; "Enter"/"Esc"/"Ctrl K" are literal
    # physical-keyboard key labels rendered inside real <kbd> elements
    # (layout/base.html, layout/_command_palette.html) -- every keyboard in
    # an Arabic-speaking market still prints these Latin key names, so
    # translating them would show a label that doesn't match the physical
    # key (the same "بحث (Ctrl+K)" precedent already established for
    # "Search (Ctrl+K)" -- the surrounding instructional text is real
    # Arabic, the literal key name stays Latin).
    ARABIC_SCRIPT_EXEMPT = {"Aura Owner", "Enter", "Esc", "Ctrl K"}
    ascii_only = []
    for m in catalog:
        if not m.id or not m.string:
            continue
        if m.id in ARABIC_SCRIPT_EXEMPT:
            continue
        if not any("؀" <= ch <= "ۿ" for ch in m.string):
            ascii_only.append(m.id)
    assert ascii_only == [], f"{len(ascii_only)} 'Arabic' translation(s) contain no Arabic script: {ascii_only[:10]}"
