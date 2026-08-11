# Phase 9.5B-R2 — Execution Plan

Order of work, matching the governing spec's 16 milestones, adapted to the
real surface counts established in `phase9-5b-r2-baseline.md`:

1. Entry gate (done) — branch `phase9.5/owner-i18n-rtl-final-closure` created
   from `632100d`.
2. M1 — source-driven surface inventory (done, see `complete-owner-surface-inventory.md`).
3. M2 — expand hardcoded-string scanner from `GATED_DIRS = (layout, auth,
   employees, profile)` to all 15 template directories.
4. M3 — translate the 50 remaining templates, domain by domain, smallest/
   highest-value first: `dashboard` (1) → `audit` (3) → `staff` (3) →
   `customers` (3) → `installations` (3) → `licensing` (3) →
   `subscriptions` (3) → `system` (1) → `catalog` (5) → `licensing_admin` (6)
   → `commercial_ops` (19, largest, done last).
5. M4 — extend `i18n_labels.py` with real localized-label functions for every
   stable status/enum actually rendered in the newly translated templates
   (subscription status, license status, installation status, renewal status,
   pilot status, emergency-extension status, pending-activation status,
   notification severity/status, queue role, audit action — already partially
   covered — device-key/signing-key status).
6. M5 — wrap the real hardcoded `error=`/`entity=` strings found in
   `commercial_ops/ui_routes.py` (and any equivalent in other route files) in
   `gettext()`.
7. M6 — RTL review of the newly translated templates (tables, forms, badges,
   timelines) using the same logical-property pattern already proven in
   Phase 9.5B-R; fix only real defects found.
8. M7 — template-render tests (English + Arabic) for all 50 newly translated
   templates, plus a route/template manifest test that fails if a future
   template is added without localization coverage.
9. M8 — real Playwright browser validation at the four required viewports
   (1440×900, 1024×768, 768×1024, 390×844) × 2 locales, across representative
   pages from every real route family (not the confirmed-absent domains from
   `phase9-5b-r2-scope-and-boundaries.md`).
10. M9 — accessibility revalidation of the newly translated surfaces.
11. M10 — bilingual functional parity for the real, implemented commercial
    workflows (customer create, subscription create, license issue,
    installation inspect, renewal, pilot, emergency extension, audit).
12. M11 — catalog extraction/compile/drift check against the expanded scope.
13. M12 — security regression (locale/redirect/injection/API stability),
    re-run against the expanded surface.
14. M13 — extend i18n preflight to validate the expanded catalog scope.
15. M14 — complete final regression: Owner, `commercial_runtime`, Retail,
    Clinic, infrastructure — all four, from the final HEAD, real totals only.
16. M15 — legacy-repo close-out read-only re-check.
17. M16 — verdict amendments, final decision, handover, commit, tag.

Each milestone is executed with real commands/tests, not narrated as done
without evidence, matching this project's established discipline.
