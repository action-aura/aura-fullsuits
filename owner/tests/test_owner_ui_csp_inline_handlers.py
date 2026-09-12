"""Durable CSP guard -- no template may reintroduce an inline event handler.

Owner CC ships under `script-src 'self'` with no 'unsafe-inline'
(app/security/headers.py). That means an `onclick=` / `onchange=` /
`onsubmit=` attribute, a `javascript:` URL, or a `<script>` block with a
body does not error, does not warn in any server-side test, and does not
show up in a code review diff as "broken" -- the browser simply refuses to
run it and the control silently does nothing. That failure mode is why a
`onsubmit="return confirm(...)"` guard on the Void-expense form went
unnoticed while the form submitted instantly with no confirmation at all,
and why six status filters across Commissions, Cash Closings, Renewals,
Pilots, Activation Reviews and Emergency Extensions were completely dead.

A static scan is the right guard here precisely *because* the failure is
silent: a rendering test cannot observe it (the attribute renders fine --
it just never executes), and only a real browser would. This scan is the
only place in the suite that can catch a regression at author time.

The sanctioned replacements, both already used across the app:
  * a one-off confirmation      -> `data-confirm="..."` + static/js/confirm.js
  * auto-submit a filter/select -> `data-auto-submit` + static/js/auto-submit.js
  * anything richer             -> a new file under app/static/js/ wired by
                                   data-* attributes and included from
                                   layout/base.html
"""
from __future__ import annotations

import os
import re

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "templates"
)
STATIC_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "js"
)

# Jinja comments are stripped before scanning: several templates legitimately
# *discuss* the CSP rule in prose (layout/base.html's own header comment
# explains why its <script> tags are external), and prose about a banned
# pattern is not the banned pattern.
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)

# `on<event>=` as a real HTML attribute: preceded by something that is not a
# word char or a hyphen, so `data-label-comfortable=` and friends can never
# match, but ` onclick=` / `\n  onchange=` always do.
INLINE_HANDLER_RE = re.compile(r"(?<![\w-])(on[a-z][a-z0-9]*)\s*=", re.IGNORECASE)

JS_URL_RE = re.compile(r"""["'(=]\s*javascript:""", re.IGNORECASE)

SCRIPT_BLOCK_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)

# `<script type="application/json">` is a *data* block, not an executable
# script: the browser never evaluates it, so CSP's script-src does not apply
# and it is a legitimate way to hand server data to an external .js file
# (layout/_command_palette.html does exactly this).
NON_EXECUTABLE_SCRIPT_TYPES = ("application/json", "application/ld+json", "text/template")

# The two attributes that only *look* like inline handlers to the regex above
# but are ordinary content attributes. Kept explicit rather than loosening the
# regex, so a real handler can never slip through a broadened pattern.
ALLOWED_ON_ATTRIBUTES = frozenset()


def _iter_templates():
    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for name in sorted(files):
            if name.endswith(".html"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as fh:
                    source = fh.read()
                rel = os.path.relpath(path, TEMPLATES_DIR).replace(os.sep, "/")
                yield rel, JINJA_COMMENT_RE.sub("", source)


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def test_no_template_contains_an_inline_event_handler_attribute():
    offenders = []
    for rel, source in _iter_templates():
        for match in INLINE_HANDLER_RE.finditer(source):
            if match.group(1).lower() in ALLOWED_ON_ATTRIBUTES:
                continue
            offenders.append(f"{rel}:{_line_of(source, match.start())}: {match.group(1)}=")
    assert not offenders, (
        "Inline event handlers are blocked by Owner CC's script-src 'self' CSP and "
        "fail SILENTLY in the browser. Use data-confirm (static/js/confirm.js), "
        "data-auto-submit (static/js/auto-submit.js), or a new external file wired "
        "by data-* attributes. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_no_template_contains_a_javascript_url():
    offenders = []
    for rel, source in _iter_templates():
        for match in JS_URL_RE.finditer(source):
            offenders.append(f"{rel}:{_line_of(source, match.start())}")
    assert not offenders, (
        "javascript: URLs are blocked by script-src 'self'. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_no_template_contains_an_executable_inline_script_block():
    offenders = []
    for rel, source in _iter_templates():
        for match in SCRIPT_BLOCK_RE.finditer(source):
            attrs, body = match.group(1), match.group(2)
            attrs_lower = attrs.lower()
            if any(t in attrs_lower for t in NON_EXECUTABLE_SCRIPT_TYPES):
                continue
            if "src=" in attrs_lower and not body.strip():
                continue
            offenders.append(f"{rel}:{_line_of(source, match.start())}")
    assert not offenders, (
        "Inline <script> bodies are blocked by script-src 'self'. Move the code to "
        "a file under app/static/js/ and include it from layout/base.html. "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


def test_auto_submit_helper_exists_and_is_included_from_the_base_layout():
    """The delegated auto-submit listener is the only sanctioned replacement
    for `onchange="this.form.submit()"`. If the file or its <script src> tag
    disappears, every data-auto-submit control in the app goes dead silently
    -- exactly the failure this whole test module exists to prevent."""
    assert os.path.isfile(os.path.join(STATIC_JS_DIR, "auto-submit.js"))
    with open(os.path.join(TEMPLATES_DIR, "layout", "base.html"), encoding="utf-8") as fh:
        base = fh.read()
    assert "js/auto-submit.js" in base


def test_shared_filter_bar_always_renders_a_submit_button():
    """table.filter_bar() used to render its Search button only when the
    caller passed search_label, so a status-only filter bar (Commissions,
    Cash Closings, and now the four commercial_ops queues) had no way at all
    to submit once the select's blocked onchange stopped working."""
    with open(os.path.join(TEMPLATES_DIR, "components", "table.html"), encoding="utf-8") as fh:
        source = JINJA_COMMENT_RE.sub("", fh.read())
    macro_start = source.index("{% macro filter_bar(")
    macro_end = source.index("{% endmacro %}", macro_start)
    body = source[macro_start:macro_end]
    submit_count = body.count('type="submit"')
    assert submit_count == 1, (
        "filter_bar() must render exactly one unconditional submit button; "
        f"found {submit_count} (a conditional button means status-only filter "
        "bars are unsubmittable)."
    )
    # The button must not sit behind the search_label conditional.
    button_index = body.index('type="submit"')
    tail = body[button_index:]
    assert "{% endif %}" not in tail, "filter_bar()'s submit button is still inside a conditional block"


def test_every_status_filter_screen_uses_the_shared_filter_bar():
    """The six dead status filters are fixed at the component level, not by
    six copies of the same markup. If a screen re-grows its own hand-rolled
    status <form>, it re-grows the bug too."""
    screens = [
        "commercial_sales/commissions_list.html",
        "operations_ui/cash_closings_list.html",
        "commercial_ops/renewals_list.html",
        "commercial_ops/pilots_list.html",
        "commercial_ops/pending_activations_list.html",
        "commercial_ops/emergency_extensions_list.html",
    ]
    missing = []
    for rel in screens:
        with open(os.path.join(TEMPLATES_DIR, *rel.split("/")), encoding="utf-8") as fh:
            source = fh.read()
        if "filter_bar(" not in source:
            missing.append(rel)
    assert not missing, "Screens still hand-rolling a status filter form: " + ", ".join(missing)
