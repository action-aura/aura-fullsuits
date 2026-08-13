# Phase 9.5B-R2 — Complete Owner Surface Inventory

Source-driven (grep/find against `owner/app/`), not estimated. 67 templates,
173 route decorators, 19 registered blueprints.

## Template-rendering blueprints (translation-relevant)

| Blueprint | Routes file | Routes | render_template calls | Templates | Status entering R2 |
|---|---|---|---|---|---|
| auth | app/auth/routes.py | 14 | 20 | 7 | LOCALIZED (Phase 9.5B-R) |
| employees | app/employees/routes.py + self_routes.py | 19 | 11 | 7 | LOCALIZED (Phase 9.5B-R) |
| profile | (served by employees/self_routes.py) | — | — | 2 | LOCALIZED (Phase 9.5B-R) |
| dashboard | app/dashboard/routes.py | 1 | 1 | 1 | CURRENT REACHABLE — MUST LOCALIZE |
| audit | app/audit/routes.py | 4 | 3 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| catalog | app/catalog/routes.py | 6 | 3 | 5* | CURRENT REACHABLE — MUST LOCALIZE |
| releases | app/releases/routes.py | 2 | 2 | 0 (reuses catalog/versions.html, catalog/channels.html) | CURRENT REACHABLE — shares catalog templates |
| customers | app/customers/routes.py | 8 | 4 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| installations | app/installations/routes.py | 5 | 3 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| licensing | app/licensing/routes.py | 7 | 4 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| licensing_admin | app/licensing_admin/routes.py | 13 | 6 | 6 | CURRENT REACHABLE — MUST LOCALIZE |
| staff | app/staff/routes.py | 7 | 3 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| subscriptions | app/subscriptions/routes.py | 8 | 3 | 3 | CURRENT REACHABLE — MUST LOCALIZE |
| system | app/system/routes.py | 3 | 1 | 1 | CURRENT REACHABLE — MUST LOCALIZE |
| commercial_ops_ui | app/commercial_ops/ui_routes.py | 43 | 76 | 19 | CURRENT REACHABLE — MUST LOCALIZE (largest domain) |

`*catalog` has 5 templates but only 2 direct render_template targets shown in
its own routes.py (`plan_new`, `plan_detail`); `catalog/index.html` is its
list view (3rd call not captured in single-line grep due to multi-line
`render_template(` call at line 27 — confirmed present by template file
listing), `versions.html`/`channels.html` are rendered by `releases`.

## Machine-only / non-template blueprints (confirmed out of translation scope)

| Blueprint | Routes | render_template calls | Reason |
|---|---|---|---|
| api | 2 | 0 | JSON only |
| api_external | 6 | 0 | JSON only, conditionally registered |
| api_operations | 18 | 0 | JSON only — Phase 9.5B operations API, untouched |
| commercial_ops (base) | 6 | 0 | JSON API paired with commercial_ops_ui |
| locale | 1 | 0 | redirect only (`GET /locale/<code>`) |
| health | (not counted above) | 0 | health-check JSON endpoint |
| licensing_api | conditional | 0 | JSON only, external licensing protocol |

## Confirmed-absent domains (spec assumed these exist; source inspection says no)

Products/platforms, add-ons/entitlement-definition management, payments,
settings, price history — see `phase9-5b-r2-scope-and-boundaries.md` for the
full explanation. Classified NOT APPLICABLE, not counted toward any
localization total, not built.

## Totals

- Current reachable, must-localize template count: **50**
  (dashboard 1 + audit 3 + catalog 5 + customers 3 + installations 3 +
  licensing 3 + licensing_admin 6 + staff 3 + subscriptions 3 + system 1 +
  commercial_ops 19)
- Already localized (Phase 9.5B-R, retained unchanged): **17**
- Total real Owner templates: **67**
- Dead/unregistered templates found: **0** (every template under
  `app/templates/` is reachable from a registered blueprint's route — no
  orphaned template file was found)
- Future-contract-only surfaces: **0** (no template exists yet for any
  not-yet-built domain — nothing to classify here, confirming Non-Negotiable
  Rule 2 was never violated by pre-existing code)
