# Phase 9.5B-R2 — Complete Owner Surface Audit Plan

## Method (source-driven, not estimated)

1. Enumerate every `*.html` under `owner/app/templates/` (`find`/`Glob`) —
   real count: 67.
2. Enumerate every registered blueprint in `owner/app/__init__.py` — real
   count: 19.
3. For every blueprint's routes file, count `@bp.route(...)` decorators and
   `render_template(...)` calls (`grep -c`) to distinguish template-rendering
   routes from JSON/API/redirect-only routes.
4. Pair each `render_template("path/to.html", ...)` call with its source
   route via direct file inspection, to build an exact route-to-template map
   (not inferred from naming convention alone).
5. Classify every surface per the governing spec's taxonomy: CURRENT
   REACHABLE — MUST LOCALIZE / CURRENT ERROR SURFACE — MUST LOCALIZE / DEAD /
   FUTURE CONTRACT ONLY / MACHINE-API ONLY.
6. Cross-check the spec's assumed domain list (products, platforms, add-ons,
   payments, settings, price history) against real code — confirmed absent,
   recorded in `phase9-5b-r2-scope-and-boundaries.md`, excluded from all
   coverage totals as NOT APPLICABLE rather than silently ignored.

Results: `complete-owner-surface-inventory.md`,
`route-template-localization-matrix.md`, `unreachable-and-future-surface-report.md`.
