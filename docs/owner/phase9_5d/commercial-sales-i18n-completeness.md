# Phase 9.5D — Milestone 20: i18n/RTL Completeness Pass

Per the execution plan: every new template/route/service function was already built with `_()`-wrapped EN/AR strings from its first commit throughout Milestones 3–19 (Non-Negotiable Principle 18). Milestone 20 is the *completeness verification* pass — extract, compile, and prove zero gaps — not the first implementation pass.

## What was found and fixed

Running `python -m babel.messages.frontend extract` + `update` against the real template tree surfaced two distinct real gaps, both closed:

1. **48 genuinely-new strings** (error-code localizations in `localize_commercial_sales_error()`/`localize_commission_error()`, Milestone 17 dashboard labels) came back with empty `msgstr` in the Arabic catalog — expected for brand-new msgids, translated directly.
2. **117 template UI strings** (nav labels, form labels, buttons, status words) were marked `fuzzy` by babel's proximity-based fuzzy-matching — it guessed an *existing, unrelated* translation for each based on textual similarity to nearby strings in the regenerated `.pot`, not a real match. Verified by diffing fuzzy-entry counts against the pre-session committed catalog (0 fuzzy before this update, 118 after) — none of this was pre-existing debt. A spot check before fixing (`grep -A1 '"Quantity"'`) showed the fuzzy-guessed Arabic string for "Quantity" was actually "الكيان" ("the entity") and "Discount" was "العدد" ("the count") — genuinely wrong, not merely unconfirmed. Every one of the 117 was given a real, reviewed translation (EN: identity per the catalog's established convention; AR: authored fresh, reusing established terminology from non-fuzzy entries elsewhere in the catalog for consistency — e.g. `"Total"` → `"الإجمالي"` already existed for the same word in another domain).

A blind "strip the fuzzy flag and keep babel's guess" fix was explicitly rejected — that would have shipped confirmed-wrong Arabic text (`"الكيان"` for "Quantity") to production. Each entry was individually reviewed against real values before being written.

## Pre-existing gap closed opportunistically

The platform-wide `_check_i18n_configuration()` preflight check (`app/commercial_ops/preflight.py`) also caught 4 Arabic translations that were already empty before this phase touched anything — Phase 8 pilot/staff-domain strings (`"The new end date must be after the current pilot end date."` etc.), unrelated to commercial-sales. Since the preflight check is platform-wide (not scoped to this phase) and fixing it was low-risk/low-effort, it was closed rather than left as a known-red gate this milestone could have but didn't cause.

## Verification

`python -m babel.messages.frontend compile` for both locales, then the real `_check_i18n_configuration()` preflight function called directly (not just its test) — confirmed `i18n_catalog_complete_en`/`i18n_catalog_complete_ar` both `OK`, zero empty, zero fuzzy. `tests/test_phase9_5b_r_hardcoded_strings.py` (extended: `commercial_sales` added to `GATED_DIRS`, `&ndash;` added to the decorative-separator allowlist alongside the existing `&middot;`/`&mdash;`/`&rarr;` entries) and both i18n preflight test files — 8/8 pass.

## RTL

No new CSS was introduced this phase — every new template reuses `layout/base.html`'s existing logical-property rules (`margin-inline-start`, `text-align: start`, the `dir="{{ current_direction }}"` root attribute), the same foundation Phase 9.5B-R/9.5C's RTL work already validated. Real-browser RTL rendering validation (multi-viewport, actual Arabic layout inspection) is Milestone 25's job, not duplicated here.
