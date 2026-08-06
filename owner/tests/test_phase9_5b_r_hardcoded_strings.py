"""Phase 9.5B-R Milestone 16 -- hardcoded-string scanner for the gated
template set (Non-Negotiable: bounded, allowlisted, not brittle -- flags
real user-visible text nodes only, never Jinja syntax/attributes/CSS)."""
from __future__ import annotations

import os
import re

OWNER_ROOT = os.path.dirname(os.path.dirname(__file__))
TEMPLATES_ROOT = os.path.join(OWNER_ROOT, "app", "templates")

# Phase 9.5B-R2: expanded to every real current Owner template directory
# (complete-owner-surface-inventory.md) -- the Phase 9.5B-R reduction to
# layout/auth/employees/profile is now closed.
GATED_DIRS = (
    "layout", "auth", "employees", "profile",
    "dashboard", "audit", "catalog", "customers", "installations",
    "licensing", "licensing_admin", "staff", "subscriptions", "system",
    "commercial_ops", "leads", "commercial_sales", "operations_ui",
    # UI modernization Stage C -- styled 401/403/CSRF pages
    # (errors.py's register_error_handlers), replacing the bare
    # Werkzeug default pages that existed before.
    "errors",
    # UI modernization Stage C -- the Attention Center (app/attention/).
    "attention",
)

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
    "&middot;",  # decorative separator, not prose
    "&mdash;",  # decorative separator, not prose (Phase 9.5C: interaction/followup rows)
    "&rarr;",  # decorative arrow between two already-translated date/status values
    "&ndash;",  # decorative separator between two already-translated date values (Phase 9.5D: payout batch period)
    "&times;",  # decorative close-icon glyph (UI modernization: tour/modal close buttons), aria-hidden and paired with a real translated aria-label on the button itself
    "flask import-release-manifest",  # literal CLI command name, not prose
    "flask seed-offline-policy",  # literal CLI command name, not prose
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


def test_all_real_template_directories_are_now_gated():
    """Phase 9.5B-R2: the scope reduction from Phase 9.5B-R is closed --
    every directory that actually contains a template must now be scanned.
    `licensing_service/` and `settings/` are real, empty scaffold
    directories (zero .html files -- confirmed no template, blueprint, or
    route exists for either; see phase9-5b-r2-scope-and-boundaries.md) and
    are correctly excluded, not silently omitted."""
    dirs_with_templates = set()
    for d in os.listdir(TEMPLATES_ROOT):
        dir_path = os.path.join(TEMPLATES_ROOT, d)
        if os.path.isdir(dir_path) and any(n.endswith(".html") for n in os.listdir(dir_path)):
            dirs_with_templates.add(d)
    assert dirs_with_templates == set(GATED_DIRS)
