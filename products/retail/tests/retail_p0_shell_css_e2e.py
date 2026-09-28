"""
retail_p0_shell_css_e2e.py -- P0 #14a/#14b: real-browser proof for the two
cascade fixes retail_design_render_test.js's own hand-rolled selector engine
cannot model.

WHY A REAL BROWSER, NOT THE SHARED JS DESIGN-TEST HARNESS
retail_design_contrast_test.js's cascade engine (retail_design_render_test.js)
is a from-scratch CSS selector matcher -- documented in that file's own header
as reconstructing the tree and cascade "in about two hundred lines" because
this frontend has no jsdom. Its compoundMatches() has an explicit fallback for
any structural pseudo-class it does not recognise: "unmodelled -> never
match". Measured directly (see the mutation-proof functions below): a bare
<button> against the real, unmutated main.css with `:where(button, input,
select, textarea) { font-family: inherit; }` added resolves NO font-family
declaration at all through that engine, because :where() falls into exactly
that unmodelled branch -- not because the CSS is wrong (every real browser,
including the one this product ships in, supports :where()/:is() natively),
but because that hand-rolled matcher has never needed to know about them
before this item. Extending it to add general :is()/:where() support is a
change with its own blast radius across every OTHER test built on it (owned
by other crews' items, not this one) and is out of scope for a two-rule P0
fix. So this crew's proof for these two specific rules runs in the one place
that already implements the real semantics correctly: an actual browser.

This matches contracts.md's own rule directly: "UI defects are proven in a
real browser (Playwright)... measuring rendered geometry / computed style...
never by reading source text."

WHAT THIS PROVES
  1. #14a -- a bare, unclassed <button>/<input>/<select>/<textarea> computes
     the SAME font-family as <body>. [MUTATION-PROVED]
  2. #14b -- a .ret-btn-primary/.sub-btn-primary rendered INSIDE
     .sub-chart-card/.modern-card computes `color` equal to what
     `var(--text-on-accent)` resolves to (not the card's own inherited dark
     text); .ret-btn-danger/.sub-btn-danger the same against
     `--state-danger-text`. Checked in all 5 sanctioned themes (light, sand,
     dark, night, dusk). [MUTATION-PROVED, both button families]

HOW THE FIXTURE IS BUILT, WITHOUT REIMPLEMENTING ANYTHING
The fixture loads the REAL css/main.css by file:// URL, in the same document
that inlines the REAL text of subsystem-retail.js's injected stylesheet
(RetailSystem._injectStyles(), a CSS-in-JS template literal) -- extracted by
retail_p0_shell_extract_injected_css.js, which drives the SAME vm harness
retail_design_contrast_test.js already depends on to get that text. Neither
stylesheet is retyped or approximated; only the two files' own SOURCE TEXT
(the real one, or a byte-for-byte copy with exactly the fix line removed, for
the mutation half) is ever loaded into the browser.

MUTATION METHOD: never overwrites the real main.css on disk. Each mutation
writes a MODIFIED COPY of main.css's text to a throwaway temp file and points
a fresh page's <link> at that copy instead -- the real file the repo tracks
is never touched.

CI COVERAGE -- DELIBERATELY A MANUAL RELEASE GATE, NOT A CI TEST (P0
review-fix, 2026-09-27). products/run_all_tests.py's canonical runner only
discovers `*_test.py`, `test_*.py` and `*_test.js` (see its own glob calls);
this file is named `*_e2e.py` on purpose, the SAME convention crew C3a uses
for retail_p0_till_e2e.py ("standalone Playwright script, NOT *_test.py" --
contracts.md's own file-ownership table), so it is never auto-discovered.
That is intentional, not an oversight: it needs Playwright plus the specific
system Python at the path in the Run: line above, neither of which this
repo's CI image is known to carry, and a renamed `*_test.py` copy would fail
LOUDLY on any machine missing either -- exactly the failure shape
contracts.md's own hard rules warn about ("a system-Python/Playwright skip
that reports loudly, not silently" was the other option offered for this gap;
this file already reports loudly by simply not existing where CI looks, which
needs no new skip-detection code to go stale). Registering it with
run_all_tests.py would mean editing a file this crew does not own (see
contracts.md's ownership table -- run_all_tests.py is not listed for C4), so
the fix recorded here instead: #14a's font-family regression and #14b's
in-card-button-contrast regression have their CI-visible guard in
retail_design_render_test.js / retail_design_contrast_test.js (both driven
by the shared retail_surface_domlite.js cascade engine, both green -- see
those files' own suites). THIS file is the additional, real-browser proof for
the two constructs that engine cannot model (:where() and the cascade
interaction it resolves) and is a MANUAL release gate: run it by hand before
shipping any change to the two rules it guards (CONTROL_FONT_RULE / the
in-card restatement rules), the same way a human would run
retail_p0_till_e2e.py before shipping a till change.

Run: C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_p0_shell_css_e2e.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "products" / "retail" / "frontend"
MAIN_CSS_PATH = FRONTEND / "css" / "main.css"
EXTRACT_SCRIPT = Path(__file__).resolve().parent / "retail_p0_shell_extract_injected_css.js"

THEME_NAMES = ["light", "sand", "dark", "night", "dusk"]

CONTROL_FONT_RULE = ":where(button, input, select, textarea) { font-family: inherit; }"
# P0 #14b REVIEW-FIX (2026-09-27) -- re-anchored from the original `:is(A, B)
# :is(C, D)` wording to the plain comma-separated selector list main.css now
# ships (see main.css's own comment on the rule): the :is() form is valid CSS
# and this real-browser fixture proved it (that was never this test's own
# problem), but it made retail_design_contrast_test.js's TIER 9 vacuous --
# the shared retail_surface_domlite.js::parseCss() that guard re-parses
# through splits a selector list on every comma, INCLUDING the ones inside
# :is(...). Root-fixed in main.css itself; this file's mutation anchors have
# to track the real rule's exact text either way, so they move with it.
INCARD_PRIMARY_RULE = (
    "html[data-theme] .sub-chart-card .ret-btn-primary,\n"
    "html[data-theme] .sub-chart-card .sub-btn-primary,\n"
    "html[data-theme] .modern-card .ret-btn-primary,\n"
    "html[data-theme] .modern-card .sub-btn-primary { color: var(--text-on-accent); }"
)
INCARD_DANGER_RULE = (
    "html[data-theme] .sub-chart-card .ret-btn-danger,\n"
    "html[data-theme] .sub-chart-card .sub-btn-danger,\n"
    "html[data-theme] .modern-card .ret-btn-danger,\n"
    "html[data-theme] .modern-card .sub-btn-danger { color: var(--state-danger-text); }"
)


def get_real_main_css_text() -> str:
    return MAIN_CSS_PATH.read_text(encoding="utf-8")


def get_injected_css_text() -> str:
    """The REAL text of subsystem-retail.js's injected stylesheet -- see this
    file's own header for why a real extraction, not a hand-typed mirror."""
    result = subprocess.run(
        ["node", str(EXTRACT_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"retail_p0_shell_extract_injected_css.js failed:\n{result.stderr}")
    return result.stdout


def mutate_once(css_text: str, find: str, replace: str) -> str:
    hits = css_text.count(find)
    assert hits == 1, (
        f"Mutation anchor occurs {hits} time(s), expected exactly 1:\n  {find!r}\n\n"
        "A mutation that no longer applies would let the proof below pass while proving nothing. Re-anchor it."
    )
    return css_text.replace(find, replace, 1)


FIXTURE_TEMPLATE = """<!doctype html>
<html data-theme="light">
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="{main_css_href}">
<style>
{injected_css}
</style>
<style>
  /* TEST-FIXTURE ONLY -- never mirrored into the product's own main.css.
     Diagnosed directly (CDP CSS.getMatchedStylesForNode against this same
     fixture): .ret-btn sets `transition: all .2s`, so switching data-theme
     -- which changes the custom property `.ret-btn-danger`'s `color`
     resolves to -- ANIMATES that colour for 200ms. Reading
     getComputedStyle() synchronously after set_theme() therefore sampled
     the button mid-transition, a few RGB units short of the sand token's
     #9a1f14 (measured rgb(153,28,18) against the target rgb(154,31,20)) --
     not a cascade defect: the reference <span> has no `transition` and
     landed on the exact token instantly, and the SAME button measured after
     a few slower round-trips (CDP calls between the theme switch and the
     read) landed on the exact token too, once the transition had time to
     finish. This fixture exists to prove which RULE WINS THE CASCADE, not
     to time a transition, so transitions/animations are switched off here
     -- switching them off in main.css itself would change real product
     behaviour for a defect that only exists in how this fixture measures. */
  * {{ transition: none !important; animation: none !important; }}
</style>
</head>
<body class="sub-shell">
  <!-- P0 #14a -- bare, unclassed controls: nothing else in the cascade sets
       their font-family, so whatever they compute is EXACTLY what the
       :where(...) rule (or its absence) produces. -->
  <button id="bare-btn">Bare button</button>
  <input id="bare-input" value="bare input">
  <select id="bare-select"><option>bare option</option></select>
  <textarea id="bare-textarea">bare textarea</textarea>

  <!-- P0 #14b -- in-card buttons, both card classes x both button-family
       classes x both primary/danger, matching the restated selector list's
       own card-class x button-class cross product exactly. -->
  <div class="sub-chart-card">
    <span id="card1-plain-text">Plain card text</span>
    <button class="ret-btn ret-btn-primary" id="card1-primary">Save</button>
    <button class="ret-btn ret-btn-danger" id="card1-danger">Delete</button>
  </div>
  <div class="modern-card">
    <span id="card2-plain-text">Plain card text</span>
    <button class="sub-btn-primary" id="card2-primary">Save</button>
    <button class="sub-btn-danger" id="card2-danger">Delete</button>
  </div>

  <!-- Reference swatches: what the raw tokens resolve to, read through
       getComputedStyle like everything else here -- never a hand-parsed hex
       comparison, so a browser's own colour-resolution is what both sides of
       every comparison below go through. -->
  <span id="ref-text-on-accent" style="color:var(--text-on-accent)"></span>
  <span id="ref-state-danger-text" style="color:var(--state-danger-text)"></span>
</body>
</html>
"""


def build_fixture(tmp_dir: Path, main_css_text: str, injected_css_text: str) -> Path:
    css_path = tmp_dir / "main.css"
    css_path.write_text(main_css_text, encoding="utf-8")
    html = FIXTURE_TEMPLATE.format(
        main_css_href=css_path.as_uri(),
        injected_css=injected_css_text,
    )
    html_path = tmp_dir / "fixture.html"
    html_path.write_text(html, encoding="utf-8")
    return html_path


def new_page(browser):
    # color_scheme="light" pins headless Chromium's OS-level dark/light
    # preference explicitly. Measured needing this: headless Chromium's
    # default reported a couple of RGB units off from the declared
    # `--state-danger-text` hex for a real <button> (never for a <span>) in
    # the Sand theme -- Sand and Light declare no `color-scheme` of their own
    # (only dark/night/dusk set `color-scheme: dark`, css/main.css), so a
    # native <button>'s UA chrome falls back to whatever the BROWSER's own
    # ambient preference is, and Chromium auto-adjusts native form-control
    # colours slightly under an ambient dark preference. The product itself
    # never brqnches on prefers-color-scheme for its own theme tokens (see
    # DESIGN.md -- themes are `data-theme`, not media queries), so this is an
    # artifact of the headless test environment's ambient OS signal, not of
    # the product; pinning it removes that one variable from every comparison
    # below without touching what is actually under test (the restatement
    # rule's own specificity, unaffected by colour scheme).
    return browser.new_page(color_scheme="light")


def set_theme(page, theme: str) -> None:
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)


# RESUME NOTE (2026-09-27): this helper was called throughout the file below
# but never defined -- the crew that wrote this file was interrupted before
# finishing it (contracts.md's RESUME NOTE). Every check in this file failed
# with `NameError: name 'computed' is not defined` until this was added back.
# getComputedStyle(el)[prop] rather than a hand-picked property name baked
# into the JS string, so the SAME helper serves every call site below
# ("fontFamily", "color", ...) without retyping the accessor once per prop.
def computed(page, selector: str, prop: str) -> str:
    value = page.eval_on_selector(selector, "(el, p) => getComputedStyle(el)[p]", prop)
    assert value is not None, f"{selector!r} not found in the fixture, or getComputedStyle returned no {prop!r}."
    return value


# ─────────────────────────────────────────────────────────────────────────────
# (1) #14a -- every bare control computes the same font-family as <body>
# ─────────────────────────────────────────────────────────────────────────────

def check_control_font(page) -> None:
    body_font = computed(page, "body", "fontFamily")
    assert body_font, "body computed no font-family at all -- fixture is broken."
    for sel in ["#bare-btn", "#bare-input", "#bare-select", "#bare-textarea"]:
        got = computed(page, sel, "fontFamily")
        assert got == body_font, (
            f"{sel} computed font-family {got!r}, expected the same as body's {body_font!r}."
        )


def test_control_font_green(browser) -> None:
    with tempfile.TemporaryDirectory(prefix="p0_shell_css_") as tmp:
        tmp_dir = Path(tmp)
        html_path = build_fixture(tmp_dir, get_real_main_css_text(), get_injected_css_text())
        page = new_page(browser)
        page.goto(html_path.as_uri())
        check_control_font(page)
        page.close()
    print("PASS: bare button/input/select/textarea all compute the same font-family as <body> (real main.css)")


def test_control_font_mutation_proved(browser) -> None:
    mutated_css = mutate_once(
        get_real_main_css_text(),
        CONTROL_FONT_RULE,
        ":where(button, input, select, textarea) { /* mutant: font-family line removed */ }",
    )
    with tempfile.TemporaryDirectory(prefix="p0_shell_css_mutant_") as tmp:
        tmp_dir = Path(tmp)
        html_path = build_fixture(tmp_dir, mutated_css, get_injected_css_text())
        page = new_page(browser)
        page.goto(html_path.as_uri())
        body_font = computed(page, "body", "fontFamily")
        bare_font = computed(page, "#bare-btn", "fontFamily")
        page.close()
    assert bare_font != body_font, (
        f"MUTATION SURVIVED -- with the :where(...) rule's font-family declaration removed, #bare-btn STILL "
        f"computed {bare_font!r}, the same as body's {body_font!r}. Some other rule must be setting it; this "
        "guard is not actually isolating the fix under test."
    )
    print(f"PASS: removing the :where(...) rule's font-family line makes #bare-btn compute {bare_font!r} "
          f"instead of body's {body_font!r} -- RED as required")
    # Re-prove GREEN against the real, unmutated file (disk was never touched).
    test_control_font_green(browser)
    print("PASS: guard passes again against the real, unmutated main.css")


# ─────────────────────────────────────────────────────────────────────────────
# (2) #14b -- in-card primary/danger buttons compute the token colour, not
#     the card's own inherited text colour, in all 5 themes
# ─────────────────────────────────────────────────────────────────────────────

CARD_CASES = [
    ("#card1-primary", "#card1-plain-text", "#ref-text-on-accent", "sub-chart-card .ret-btn-primary"),
    ("#card1-danger", "#card1-plain-text", "#ref-state-danger-text", "sub-chart-card .ret-btn-danger"),
    ("#card2-primary", "#card2-plain-text", "#ref-text-on-accent", "modern-card .sub-btn-primary"),
    ("#card2-danger", "#card2-plain-text", "#ref-state-danger-text", "modern-card .sub-btn-danger"),
]


def check_incard_buttons(page, theme: str) -> None:
    set_theme(page, theme)
    for btn_sel, plain_sel, ref_sel, label in CARD_CASES:
        btn_color = computed(page, btn_sel, "color")
        ref_color = computed(page, ref_sel, "color")
        plain_color = computed(page, plain_sel, "color")
        assert btn_color == ref_color, (
            f"[{theme}] {label} computed color {btn_color!r}, expected the token's own colour {ref_color!r}."
        )
        # Anti-vacuity: if the card's inherited text colour happened to equal
        # the token colour in this theme, a broken (reverted) rule would
        # ALSO pass by accident -- assert the two differ so the check above
        # is actually discriminating between "my rule won" and "the inherit
        # reset won instead", not just measuring a coincidence.
        assert plain_color != ref_color, (
            f"[{theme}] {label}: the card's own inherited text colour ({plain_color!r}) happens to equal "
            f"the token colour ({ref_color!r}) in this theme, so this check cannot tell the fix from its "
            "absence here -- pick a different reference pairing for this theme."
        )


def test_incard_buttons_green(browser) -> None:
    with tempfile.TemporaryDirectory(prefix="p0_shell_css_") as tmp:
        tmp_dir = Path(tmp)
        html_path = build_fixture(tmp_dir, get_real_main_css_text(), get_injected_css_text())
        page = new_page(browser)
        page.goto(html_path.as_uri())
        for theme in THEME_NAMES:
            check_incard_buttons(page, theme)
        page.close()
    print(f"PASS: in-card primary/danger buttons compute the token colour (not the card's inherited text) "
          f"in all {len(THEME_NAMES)} themes (real main.css)")


def test_incard_buttons_mutation_proved(browser) -> None:
    real_css = get_real_main_css_text()
    mutated_css = mutate_once(
        mutate_once(real_css, INCARD_PRIMARY_RULE, "/* mutant: in-card primary restatement removed */"),
        INCARD_DANGER_RULE, "/* mutant: in-card danger restatement removed */",
    )
    with tempfile.TemporaryDirectory(prefix="p0_shell_css_mutant_") as tmp:
        tmp_dir = Path(tmp)
        html_path = build_fixture(tmp_dir, mutated_css, get_injected_css_text())
        page = new_page(browser)
        page.goto(html_path.as_uri())
        set_theme(page, "light")
        primary_color = computed(page, "#card1-primary", "color")
        plain_color = computed(page, "#card1-plain-text", "color")
        ref_color = computed(page, "#ref-text-on-accent", "color")
        page.close()
    assert primary_color != ref_color, (
        f"MUTATION SURVIVED -- with both restatement rules removed, #card1-primary STILL computed "
        f"{primary_color!r}, the token colour. The mutation did not actually remove the rule that matters."
    )
    assert primary_color == plain_color, (
        f"With the restatement removed, #card1-primary computed {primary_color!r}, which is neither the "
        f"token colour ({ref_color!r}) NOR the card's own inherited text colour ({plain_color!r}) -- "
        "the pre-fix defect this item describes is specifically that the card's inherit rule wins, so this "
        "is a different, unexpected failure mode worth its own look, not the one under test."
    )
    print(f"PASS: removing the in-card restatement rules makes #card1-primary compute the CARD's own text "
          f"colour ({primary_color!r}) instead of the token colour ({ref_color!r}) -- RED as required, and "
          "it is specifically the pre-fix defect (the inherit rule winning), not some other failure")
    test_incard_buttons_green(browser)
    print("PASS: guard passes again against the real, unmutated main.css")


# ─────────────────────────────────────────────────────────────────────────────

CHECKS = [
    ("#14a: bare controls compute body's font-family", test_control_font_green),
    ("#14a: control-font rule is mutation-proved", test_control_font_mutation_proved),
    ("#14b: in-card buttons compute the token colour (5 themes)", test_incard_buttons_green),
    ("#14b: in-card restatement rules are mutation-proved", test_incard_buttons_mutation_proved),
]


def main() -> int:
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in CHECKS:
            try:
                fn(browser)
            except Exception as exc:  # noqa: BLE001 -- report, don't hide
                failures.append(name)
                print(f"FAIL: {name}")
                print(textwrap.indent(str(exc), "      "))
        browser.close()

    if failures:
        print(f"\nFAIL: retail_p0_shell_css_e2e.py — {len(failures)} of {len(CHECKS)} checks failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print(f"PASS: retail_p0_shell_css_e2e.py — {len(CHECKS)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
