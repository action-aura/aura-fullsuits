# Phase 9.5B-R Milestone 13 — Locale Formatting Contract

`owner/app/i18n_format.py` — `format_owner_date()`, `format_owner_datetime()`, `format_owner_number()`,
thin wrappers around Flask-Babel's real `format_date`/`format_datetime`/`format_decimal` (CLDR-backed).

## Dates/times

Timezone-aware values pass through unchanged (no timezone conversion performed by these helpers — the
underlying `datetime` objects are already UTC-aware per this codebase's existing convention, unchanged by
this phase); `format="medium"` gives a locale-appropriate medium-length rendering (e.g. `Aug 1, 2026` in
English, day/month/year with Arabic month names in Arabic). Scheduler behavior (`Asia/Amman` business
semantics from Phase 9.5A's daily-reporting design) is untouched — these are presentation-only helpers
called from templates, never from `owner/app/employees/dashboard.py` or any scheduled job's own logic.
ISO 8601 API timestamps (`_iso()` in `api_operations/routes.py`) are untouched — never routed through
these formatters (`api-localization-boundary.md`).

## Numbers

`format_owner_number()` used only for real counts/quantities (dashboard metrics) — never for an employee
number, UUID, version, correlation ID, or phone number, all of which pass through unchanged as plain
strings (or `bidi_isolate`d) specifically because they are identifiers, not quantities.

## Arabic-digit policy — verified, not assumed

Western (ASCII 0-9) digits in both locales. Real verification performed this phase (not a guess): Babel
2.18's own `format_decimal`/`format_date` default `numbering_system` for locale `ar` is already `"latn"`
— confirmed by inspecting `babel.numbers.format_decimal`'s signature (`numbering_system: str = 'latn'`,
locale-independent default) and by rendering a real `ar`-locale date and checking its digit characters are
U+0030-U+0039, not the Eastern Arabic-Indic U+0660-U+0669 range. `test_phase9_5b_r_formatting.py` asserts
this directly against a real formatted value, so a future Babel upgrade changing this default would fail
the test rather than silently ship Eastern Arabic-Indic digits.

## Currency

Not applicable this phase — no financial/currency value is displayed anywhere in the gated template set
(confirmed: Owner's Phase 9.5A/9.5B employee-domain screens carry no monetary amounts; commission-plan
*names* are the only money-adjacent field shown, and those are plain strings, not currency values).
Recorded honestly as NOT APPLICABLE rather than a fabricated currency-formatting implementation.
