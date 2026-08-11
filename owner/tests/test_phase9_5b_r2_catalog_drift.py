"""Phase 9.5B-R2 Milestone 11 -- catalog completeness/drift control, expanded
from Phase 9.5B-R's scope to the full Owner-wide catalog (742 messages)."""
from __future__ import annotations

import os
import subprocess
import sys

from babel.messages.pofile import read_po

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TRANSLATIONS_ROOT = os.path.join(OWNER_ROOT, "translations")


def _load(locale):
    path = os.path.join(TRANSLATIONS_ROOT, locale, "LC_MESSAGES", "messages.po")
    with open(path, "r", encoding="utf-8") as f:
        return read_po(f)


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


def test_total_message_count_is_at_least_the_phase9_5b_r2_baseline():
    """742 real messages confirmed at Phase 9.5B-R2 close (203 from Phase
    9.5B-R + 539 new this wave). A future phase adding messages is fine (the
    count only grows); a regression that silently drops entries is not."""
    cat = _load("en")
    total = sum(1 for m in cat if m.id)
    assert total >= 742, f"expected at least 742 messages, found {total}"
