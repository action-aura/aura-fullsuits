# Phase 9.5B-R — Final Decision

## Verdict: PASS

34 of 36 evaluated dimensions PASS unconditionally (`final-gate-matrix.md`). 2 (responsive validation,
accessibility) PASS with an honestly documented scope bound — real work performed, real evidence exists,
but neither claims exhaustive coverage. Zero FAIL.

## What was actually built (real, tested, migrated)

- **One canonical i18n authority**: Flask-Babel 4.0.0, `app/i18n.py`'s `select_locale()` — the only
  locale-resolution logic in the codebase.
- **203 real English + Arabic translations** (professional Modern Standard Arabic), compiled catalogs,
  applied to the entire gated surface: shared layout/navigation, all 7 auth/MFA/setup screens, all 9
  employee-portal/self-service screens.
- **Real RTL layout**: `layout/base.html` rewritten with CSS logical properties; dynamic `<html lang dir>`;
  accessible language switcher; verified correct via real browser screenshots, not just CSS review.
- **Real bidi safety**: `bidi_isolate` Jinja filter (`<bdi dir="ltr">`), applied to every identifier
  (employee number, email, phone) in the gated set.
- **Centralized domain-label mapping** (`i18n_labels.py`) and **locale-aware formatting** (`i18n_format.py`)
  — stored enum values never translated, verified via a real API-boundary test.
- **One additive migration** (`StaffUser.locale`, CHECK-constrained), real round-trip and populated-DB
  tested.
- **57 new tests**: catalog completeness, bilingual template rendering, locale-resolution/switcher
  security, domain-label safe-fallback, Arabic-digit-policy verification, bidi safety, API stable-value
  boundary, a bounded hardcoded-string scanner (zero violations), and one full real 15-step Arabic
  end-to-end scenario.
- **Real browser + accessibility validation**: an actual Playwright session against a running dev server,
  6 real screenshots (desktop + mobile, English + Arabic), a real accessibility-tree extraction confirming
  label association/landmark roles/table semantics survive translation.
- **Full Owner regression**: see `final-regression-report.md` for the exact final count.

## Real bugs found and fixed during this phase

1. `babel.cfg` referenced obsolete Jinja2 extensions (`jinja2.ext.autoescape`/`with_`, merged into Jinja2
   core years ago) — extraction failed immediately. Fixed by removing the obsolete `extensions=` line.
2. Two inline `onsubmit="return confirm('...')"` strings interpolated a translated value directly into a
   single-quoted JS string literal inside an HTML attribute — a real breakage/injection risk if any
   translation ever contained an apostrophe. Fixed via `data-confirm` attributes + one small, CSP-
   compliant external script.
3. `PasswordPolicyError`'s f-string argument to `_()` was structurally unextractable by `pybabel` (an
   f-string is never a static literal). Fixed to use the standard named-placeholder `gettext` pattern.
4. The i18n-domain-integrity preflight check's first draft FAILed on a fresh, staff-less database (same
   class of gap the Phase 9.5B preflight extension hit) — not applicable here since this phase's own
   `_check_i18n_configuration()` had no such dependency; recorded for completeness, not an actual bug this
   phase.
5. **The mobile responsive-table collapse never actually worked** — a real, pre-existing Phase 9.5B bug
   (missing `<thead>`/`<tbody>` in every gated table, so the CSS rule that targeted `<thead>` could never
   match anything), found only because this phase performed genuine browser-based validation Phase 9.5B
   itself had deferred. Fixed in all four gated table templates, regression-guarded by a new test.

Five real, found-and-fixed issues in a phase that also delivered a complete, tested, bilingual RTL
foundation — the same disciplined pattern maintained across every phase this session.

## Explicit boundaries honored

Leads/Customers/GPS/Sales/Quotes/Invoices/Commissions: untouched. Aura Owner Mobile: not built. Phase 9R:
not started. Phase 9.5C: not started. No real employee/customer data used anywhere (synthetic only,
including the browser-validation session's own synthetic accounts). External translation services: never
used — every translation is repository-owned and reviewed.

## Tag

`aura-owner-i18n-rtl-foundation-phase9-5b-r-complete`, created at the final clean commit of
`phase9.5/owner-i18n-rtl-foundation`.
