# Phase 9.5B-R2 — Owner-Wide i18n Final Closure — Handover

## What this phase is

A narrow corrective closure wave finishing what Phase 9.5B-R started:
every real Owner web surface (not just auth/employees) is now translated,
the required four-viewport browser matrix is real, and the complete
four-suite cross-product regression has actually been run. Nothing new was
built beyond localization/fixes to what already existed.

## Where things live (unchanged locations, expanded content)

| Concern | Path |
|---|---|
| Canonical locale authority | `owner/app/i18n.py` (unchanged this wave) |
| Domain-label localization | `owner/app/i18n_labels.py` (~30 new functions) |
| Date/number formatting | `owner/app/i18n_format.py` (unchanged) |
| Translation catalogs | `owner/translations/{en,ar}/LC_MESSAGES/messages.po` (742 messages, 0 empty, 0 fuzzy) |
| i18n preflight | `owner/app/commercial_ops/preflight.py::_check_i18n_configuration` (extended) |
| Tests | `owner/tests/test_phase9_5b_r2_*.py` (5 files, 32 tests) |
| Design/evidence docs | `docs/owner/phase9_5b_r2/` (this directory, ~35 files) |

## Real bugs found and fixed this wave (see `phase9-5b-r2-final-decision.md`
   for the full list)

1. `pybabel update` fuzzy-mismatched 221 new strings onto wrong old
   translations.
2. `gettext()` inside service-layer exceptions broke every non-HTTP caller.
3. **44 tables never actually collapsed on mobile** — missing
   `class="responsive-table"`.
4. **All 4 "create new record" forms 405'd on every submission** —
   pre-existing, found via real end-to-end testing.
5. Incomplete audit-action-label coverage against real production data.

Items 3 and 4 are the most significant: both are real, user-facing,
severity-relevant defects that only a genuine end-to-end browser/submission
pass (not GET-only checks) could ever surface — direct vindication of the
governing spec's own insistence on real form submission and real viewport
testing.

## Honest, documented residual limitations (see
   `final-residual-risk-register.md`)

Missing `data-label` polish on the 44 newly-responsive tables; an
inherently-unbounded audit-action-label set; notification titles that
cannot be safely translated without a real architecture change; bounded
(not exhaustive) browser/accessibility validation scope; one pre-existing,
isolation-confirmed test-order-dependence flake; bilingual real-submission
testing scoped to the workflows this wave's defect-hunting reached.

## What a future phase inherits, ready to use

Everything Phase 9.5B-R already provided (working `_()`/catalog system,
safe language switcher, proven RTL pattern, centralized label/formatting
helpers), now genuinely proven across the *entire* current Owner
application rather than one corner of it — plus two hard-won lessons
recorded for reuse: (1) service-layer code must never depend on
request-scoped `gettext()`; translate only at the display boundary, and
(2) `pybabel update`'s fuzzy-matching is not trustworthy for bulk-filling —
always check `.fuzzy` explicitly, never just string emptiness.

## Verdict

See `phase9-5b-r2-final-decision.md` and
`combined-phase9-5b-final-closure-decision.md`: **PASS**. Combined Phase
9.5B + 9.5B-R + 9.5B-R2 result: **PASS**.

## Stop condition

Per the governing spec's explicit instruction: stop completely after Phase
9.5B-R2. Phase 9.5C, Phase 9R, and Aura Owner Mobile were not started.
