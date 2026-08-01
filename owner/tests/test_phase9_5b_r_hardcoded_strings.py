"""Phase 9.5B-R Milestone 16 -- hardcoded-string scanner for the gated
template set (Non-Negotiable: bounded, allowlisted, not brittle -- flags
real user-visible text nodes only, never Jinja syntax/attributes/CSS)."""
from __future__ import annotations

import os
import re

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_ROOT = os.path.join(OWNER_ROOT, "app", "templates")

# Gated per phase9-5b-r-scope-and-boundaries.md's own documented reduction --
# the 37 Phase 5-8 templates are a reviewed, directory-level allowlist entry,
# not scanned string-by-string this phase.
GATED_DIRS = ("layout", "auth", "employees", "profile")

# Attribute names whose value is never translatable prose (ids, urls, form
# field names, technical values) -- excluded from the text-node scan.
_TAG_RE = re.compile(r"<[^>]+>")
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
# CSS/JS block CONTENT is never a translatable text node -- stripped whole,
# not just their tags, per the governing spec's own "skip CSS" instruction.
_STYLE_OR_SCRIPT_BLOCK_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)

# A small, reviewed allowlist of literal text that is legitimately not
# translated: brand name, technical placeholders, punctuation-only content.
ALLOWLISTED_TEXT = {
    "Aura Owner",
    "-",
    "&nbsp;",
}


def _gated_template_files():
    for d in GATED_DIRS:
        dir_path = os.path.join(TEMPLATES_ROOT, d)
        if not os.path.isdir(dir_path):
            continue
        for name in os.listdir(dir_path):
            if name.endswith(".html"):
                yield os.path.join(dir_path, name)


def _extract_text_nodes(html: str) -> list[str]:
    """Strips tags and Jinja expressions, leaving only literal text nodes
    that would render as-is (a real, if simple, indicator of an untranslated
    hardcoded string)."""
    no_style_or_script = _STYLE_OR_SCRIPT_BLOCK_RE.sub("", html)
    no_jinja = _JINJA_RE.sub("", no_style_or_script)
    no_tags = _TAG_RE.sub("\n", no_jinja)
    nodes = []
    for line in no_tags.splitlines():
        stripped = line.strip()
        if stripped and stripped not in ALLOWLISTED_TEXT:
            nodes.append(stripped)
    return nodes


def test_gated_templates_directory_set_matches_the_documented_scope():
    found_dirs = {
        d for d in os.listdir(TEMPLATES_ROOT)
        if os.path.isdir(os.path.join(TEMPLATES_ROOT, d))
    }
    assert set(GATED_DIRS) <= found_dirs


def test_no_hardcoded_english_text_node_outside_jinja_in_gated_templates():
    """After full localization, every real text node in the gated set
    should be empty once Jinja {{ }}/{% %} content is stripped out -- any
    remaining non-empty, non-allowlisted line is a literal, hardcoded
    string that was missed."""
    violations = []
    for path in _gated_template_files():
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        for node in _extract_text_nodes(html):
            # A bare "-" or similar short punctuation/placeholder is fine;
            # anything with 2+ alphabetic characters not wrapped in {{ }}
            # is a real candidate hardcoded string.
            if re.search(r"[A-Za-z]{2,}", node):
                violations.append((os.path.relpath(path, OWNER_ROOT), node))
    assert violations == [], f"{len(violations)} hardcoded string(s) found: {violations[:20]}"


def test_scanner_allowlist_is_reviewed_and_bounded():
    """The allowlist itself must stay small and explicit -- a regression
    guard against someone silently growing it to hide real violations."""
    assert len(ALLOWLISTED_TEXT) <= 10


def test_non_gated_templates_are_not_scanned_this_phase_by_design():
    """Confirms the real, documented scope reduction is what the scanner
    actually implements -- not an accidental omission."""
    all_dirs = {
        d for d in os.listdir(TEMPLATES_ROOT)
        if os.path.isdir(os.path.join(TEMPLATES_ROOT, d))
    }
    non_gated = all_dirs - set(GATED_DIRS)
    assert len(non_gated) >= 10  # catalog/customers/subscriptions/licensing/etc. -- real, present, unscanned
