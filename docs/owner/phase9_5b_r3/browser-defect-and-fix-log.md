# Phase 9.5B-R3 — Milestone 5: Browser Defect and Fix Log

## Defects found during Milestone 5/6 real browser validation

**None.**

Every family/viewport/locale combination captured in
`browser-family-evidence-matrix.md` rendered correctly: no layout
overflow, no broken RTL/LTR mirroring, no untranslated ("raw msgid")
string leakage, no console errors observed during navigation, correct
bidi handling of mixed-direction content (Arabic names in LTR tables and
vice versa), correct native browser-validation and focus behavior
(Milestone 6), and correct native `confirm()`-based confirmation dialogs
(grep-confirmed, `data-confirm` pattern, see
`keyboard-focus-accessibility-evidence.md`).

## Port-blocking note (not an application defect)

Chrome/Playwright rejected navigation to `http://127.0.0.1:5060` and
`:5061` with `net::ERR_UNSAFE_PORT` (both ports are in Chrome's built-in
unsafe-port blocklist, historically associated with SIP). This is a
browser-platform restriction unrelated to the application; resolved by
running the dev server on port `5551` (the project's own
README-documented default) instead, with zero code change.
