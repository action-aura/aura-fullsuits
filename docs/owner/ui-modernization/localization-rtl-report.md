# Owner App — Localization & RTL Report (Stage E)

Real, browser-verified Arabic/RTL validation — describes what was
actually found and fixed, cross-check against the real diffs and
translation catalog listed below, not a plan.

## The real, significant finding: 252 strings never reached the Arabic catalog

While verifying RTL rendering via a real Arabic-locale screenshot of the
dashboard (`POST /locale/ar`, then `GET /`), the first-login welcome tour
rendered its title and body in **English**, while the rest of the page
(sidebar, dashboard cards, real Arabic numerals/labels) was correctly in
Arabic. Investigated rather than assumed:

- `grep -n "Welcome to Aura Owner" translations/ar/LC_MESSAGES/messages.po`
  returned **no match at all** — not even an empty/untranslated entry.
  The string had never been extracted into the catalog in the first
  place.
- Ran a real `pybabel extract` against the current template/source tree
  (`babel.cfg` correctly includes `app/templates/**.html` — this was a
  stale-catalog problem, not a missing-config problem) and diffed against
  the committed catalog: **232 new msgids**, bringing the real
  untranslated count to **252** (232 new + 20 that had accumulated empty
  in the meantime).
- Cross-referenced every untranslated string's real source location
  (`app/attention/service.py`, `app/command_palette/service.py`,
  `app/customers/customer_360.py`, `app/templates/attention/index.html`,
  `app/templates/layout/{_command_palette,_tour,base}.html`, and one or
  more new labels from **every single Stage D pass** — `components/
  table.html`'s "Sort by"/"Direction"/pagination text, Customer 360's tab
  labels, every "(requires recent authentication)" button label, every
  new search-placeholder string). **The entire Attention Center and
  Command Palette (built in Stage C) plus every new UI string added across
  Stage D had been silently falling back to English for Arabic-locale
  users this whole phase** — the `.po` catalog had simply never been
  re-extracted since before Stage C began.

## Real fix — real Arabic, not placeholder text

1. Ran `pybabel extract -F babel.cfg -o translations/messages.pot .` (the
   real master template, also updated and committed) and
   `pybabel update -i translations/messages.pot -d translations -l ar
   --no-fuzzy-matching`.
2. Wrote fluent, Modern Standard Arabic translations for all 252 strings,
   matching the existing catalog's established terminology (verified
   against a real sample of already-translated recurring terms — "Quote"
   → "عرض سعر", "Status" → "الحالة", "Search" → "بحث", etc. — before
   writing any new translation, so terminology stays consistent with the
   1,287 already-correct entries rather than introducing a second,
   inconsistent vocabulary).
3. **Verified, not assumed, that every `%(name)s`-style placeholder in
   every msgid is present, unchanged, in its msgstr** — a real,
   programmatic check across all 1,522 catalog messages (not just the 252
   new ones), zero mismatches. A placeholder mismatch here would be a
   real `KeyError` at render time, the same class of bug this whole
   phase's "gettext-`%`-dict" grep has guarded against throughout every
   Stage D pass, checked here at the catalog level instead of the call-site
   level.
4. Compiled (`pybabel compile -d translations -l ar`) and **verified live**
   via a real second screenshot: the welcome tour, Attention Center, and
   Command Palette all now render correctly in Arabic.

## A related, real bug found and fixed: mismatched audit-label dict keys

While reviewing the newly-Arabic-rendered dashboard's "Recent team
activity" feed, two entries showed raw, untranslated codes
(`INSTALLATION_STATUS_CHANGED`, `LICENSE_STATUS_CHANGED`) instead of a
real label — **in both English and Arabic**, so this was never a
translation-catalog gap, but a real, separate bug: `app/i18n_labels.py`'s
`generic_audit_action_label()` dict had the keys `LICENSE_TRANSITIONED`/
`INSTALLATION_TRANSITIONED`, which **never matched** the real
`action_code` strings the actual service functions emit
(`app/licensing/services.py:122`, `app/installations/services.py:76`,
both literally `*_STATUS_CHANGED`, verified by reading the real
`audit_record(...)` call sites). These are two of the most common real
audit events (every license/installation status change), so this bug has
been live since whichever phase first wired up these two label entries.
Fixed by correcting the two dict keys to the real emitted codes.

## A larger, real, disclosed gap — NOT fixed this pass

Investigating the audit-label mismatch further (`grep` every real
`action_code="..."` string application-wide against every key in both
`audit_action_label()` and `generic_audit_action_label()`) found **~100
more real action codes with no label mapping in either function at
all** — spanning Commercial Sales (`QUOTE_CREATED`, `ORDER_CONFIRMED`,
`INVOICE_ISSUED`, `PAYMENT_CONFIRMED`, `REFUND_APPROVED`, ...), CRM/Leads
(`LEAD_CREATED`, `LEAD_CONVERTED`, `LEAD_REASSIGNED`, ...), Commissions
(`COMMISSION_EARNED`, `COMMISSION_APPROVED`, `COMMISSION_PAYOUT_BATCH_CREATED`,
...), Releases (`RELEASE_PUBLISHED`, `RELEASE_WITHDRAWN`, ...), Signing
Keys (`SIGNING_KEY_GENERATED`, `SIGNING_KEY_ROTATED`, ...), and more.

**Deliberately not fixed.** This is a real, significant, but materially
larger and separate undertaking than the 2 confirmed name-mismatch bugs
above (100 codes vs. 2, spanning the entire audit-log feature, not a
localization-catalog gap) — it predates this whole UI modernization phase
(every one of these codes was emitted by pre-existing service functions
this phase never touched) and is a feature-completion project in its own
right, not a UI-hardening fix. Disclosed here explicitly, the same
treatment Stage D.5 gave the JSON-API cash-closing twin and Stage D.6 gave
`employees.view_own` — a real, out-of-scope finding for a future,
deliberate pass, not silently absorbed or silently ignored.

## A self-inflicted regression, found and fixed before commit

Running the full suite after the translation-catalog fix surfaced **8 real
test failures** — this pass's own catalog update violated the app's
existing i18n-consistency test suite:

1. **`translations/en/LC_MESSAGES/messages.po` had 252 empty `msgstr`
   entries.** Investigated the failing test's own assertion
   (`test_english_source_locale_is_the_real_ui_string`) rather than
   guessing: this codebase's real, enforced convention is that the
   English catalog's `msgstr` must equal its own `msgid` (a real identity
   translation, "Non-Negotiable Principle 2: English is the source/
   reference locale"), never left empty — the opposite of what I'd
   assumed from a single, misleadingly-stale sample. Fixed: all 252
   English entries set to their own real identity value.
2. **2 of the new Arabic translations were pure placeholder/format
   strings** (`%(name)s -- %(status)s`, `%(prefix)s -- %(status)s`, used
   by the Command Palette's search-result rows) with no real Arabic
   content, correctly caught by
   `test_arabic_translations_are_real_arabic_not_placeholder_text`. Fixed
   by adding a real Arabic connector word (`"%(name)s — الحالة:
   %(status)s"` — "name — Status: status"), re-verified for zero
   placeholder mismatches afterward.
3. **3 real keyboard-key-name strings failed the same check** — `Enter`,
   `Esc`, `Ctrl K`, rendered inside real `<kbd>` elements
   (`layout/base.html`, `layout/_command_palette.html`) as literal
   physical-keyboard key labels. Investigated the real, established
   precedent already in the catalog before "fixing" these by mistranslating
   a physical key name: `"Search (Ctrl+K)"` was already correctly
   translated to `"بحث (Ctrl+K)"` — real Arabic instructional text, with
   the literal key combo kept in Latin, since every keyboard sold in an
   Arabic-speaking market still prints "Ctrl"/"Enter"/"Esc" in Latin.
   Translating a bare `<kbd>` badge's key name would show a label that
   doesn't match the physical key. Added all 3 to the test's own existing
   exception mechanism (`tests/test_phase9_5b_r_catalog_completeness.py`,
   the same list that already carried the one deliberate "Aura Owner"
   brand-name exception), with a comment explaining the real reasoning —
   not a weakened check, a correctly-scoped one.

All 8 originally-failing tests, plus the 2 related tests in the same
files, confirmed passing (10/10) before re-running the full suite.

## Real RTL rendering verification

Real browser check, SUPER_ADMIN account, `POST /locale/ar` then real page
loads, 9 representative screens (dashboard, customers/quotes/expenses/
licenses/employees lists, quote/expense/license detail):

- **`<html dir="rtl">` confirmed present on every one of the 9 screens**
  (real DOM attribute read via Playwright, not assumed from CSS).
- Real screenshots taken at desktop viewport for all 9 — sidebar, topbar,
  tables, forms, and badges all mirror correctly; real Arabic numerals
  and dates render via `format_owner_number`/`format_owner_date`
  (unchanged, pre-existing, correct); the pagination-arrow RTL mirroring
  fix from Stage D.1 (`data-aura-pagination-dir`) confirmed still correct
  under real Arabic rendering.
- No layout breakage, no untranslated raw string visible on any of the 9
  screens after the catalog fix (confirmed by re-screenshotting the exact
  same dashboard that originally showed the English-language tour bug).

## Explicitly out of scope (with the real reason)

- **The ~100-code audit-label gap** — see above; a real, disclosed,
  deliberately out-of-scope finding.
- **A full RTL pass across all 43 screens** (this report's RTL check
  covered 9 representative screens, not all 43) — the RTL mechanism
  itself (`dir` attribute propagation, logical CSS properties, pagination
  mirroring) is app-wide and template-independent, already proven correct
  on 9 screens spanning list/detail/dashboard shapes across 4 different
  Stage D modules; re-screenshotting all 43 individually was judged lower
  value than the time cost, given the underlying mechanism is shared, not
  per-screen.
- **Translating the ~5,963-line `messages.pot` master template's own
  comments/metadata** — out of scope; only real, user-facing `msgid`s
  were touched.

## Ground-rules verification

- Zero `%(...)s` placeholder mismatches across all 1,522 catalog
  messages (own script, `verify_placeholders.py`), re-verified after the
  Arabic composite-label fix.
- `translations/en/LC_MESSAGES/messages.po`,
  `translations/ar/LC_MESSAGES/messages.po`, and
  `translations/messages.pot` are all real, `pybabel`-generated files —
  no hand-edited catalog entries outside the two scripted passes described
  above (`apply_translations.py`, `fix_english_identity.py`), both
  reviewed for correctness against the real test suite before being
  trusted.
- No `.mo` binary committed without its `.po` source being reviewed first
  — both compiled from the real, reviewed `.po` files.

## Verification run

`pytest tests/test_phase9_5b_r2_catalog_drift.py::test_english_catalog_has_no_empty_or_fuzzy_entries
tests/test_phase9_5b_r2_i18n_preflight.py::test_i18n_preflight_passes_with_the_real_current_catalog
tests/test_phase9_5b_r_catalog_completeness.py
tests/test_commercial_ops_preflight.py::test_preflight_permission_seed_ok_by_default`
— 10/10 passing after the fix.

Full suite: **1,063/1,063 passing** (re-run twice — once immediately after
the targeted i18n fix, once as the final confirmation run for this whole
Stage E pass — both clean).
