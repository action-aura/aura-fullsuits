# Phase 9.5B-R2 — Entry Baseline

## Primary repository entry state

- Path: `C:\Users\Dell\Desktop\AuraEnterprise\aura-fullsuits`
- Entry HEAD: `632100dd3d2d989f10497223e97677646d65be7a` (matches spec's expected `632100d`)
- Entry branch: `phase9.5/owner-i18n-rtl-foundation`
- Entry tree: clean (`git status --short` empty)
- Corrective branch created: `phase9.5/owner-i18n-rtl-final-closure`

## Historical tag verification (real `git rev-list -n1` output)

| Tag | Expected commit | Actual commit | Match |
|---|---|---|---|
| `aura-owner-i18n-rtl-foundation-phase9-5b-r-complete` | `632100d` | `632100dd3d2d989f10497223e97677646d65be7a` | YES |
| `aura-owner-employee-management-portal-phase9-5b-complete` | `61e507e` | `61e507ebcf2fbaf027063d40dd42d3398840f825` | YES |
| `aura-commercial-licensing-operations-phase8-complete` | `4131e61` | `4131e610e8e554193b01c1e923ede00f374100e3` | YES |
| `aura-owner-commercial-ops-phase8-conditional-complete` | (unmoved only) | `f593bce770a58d22f7a636db2efde629061ab123` | present, unmoved |

**Real discrepancy — Phase 9.5A tag name**: the governing spec names the tag
`aura-owner-commercial-operations-foundation-phase9-5a-complete`. This tag does
not exist. The actual tag is `aura-owner-commercial-operations-phase9-5a-complete`
(no `-foundation-` segment) and it points to `21be07bf5b24a3a26925e2972153f5aea9377212`,
which matches the spec's expected commit `21be07b` exactly. This is a naming
mismatch in the spec text only — the commit identity is correct. No tag was
renamed, created, or moved to resolve this; the real, existing tag name is used
throughout this wave's documentation and final response.

Forbidden tag `aura-secure-staging-phase9-complete`: confirmed absent
(`git tag --list` returns empty).

## Legacy repository entry state (read-only, `AuraEnterprise/AuraEnterprise`)

- Entry HEAD: `414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34`
- Entry modified/untracked file count: **37**
- Entry `git diff --stat` (tracked changes only): 22 files changed, 1441
  insertions(+), 260 deletions(-)
- Entry modified file list (tracked, `M`):
  `.gitignore`, `.superpowers/sdd/progress.md`, `api/auth.py`, `api/mt_auth.py`,
  `api/standalone_auth.py`, `api/subsystems/retail_api.py`,
  `api/system_download.py`, `app.py`, `config.py`, `core/crm/db.py`,
  `core/crm/domain.py`, `core/crm/dto/__init__.py`,
  `core/crm/services/__init__.py`, `core/crm/services/lead_assignment_service.py`,
  `core/crm/services/lead_lifecycle_service.py`,
  `core/crm/services/lead_management_validation_service.py`,
  `core/crm/services/lead_qualification_service.py`,
  `core/crm/services/lead_scoring_service.py`, `core/crm/services/lead_service.py`,
  `database/subsystem_db.py`, `scratch/seed_admin.py`,
  `static/js/subsystem-retail.js` (22 files)
- Entry untracked (`??`) list: `core/crm/repository/lead_conversion.py`,
  `core/crm/repository/lead_duplicate.py`, `core/crm/repository/lead_merge.py`,
  `core/crm/repository/lead_timeline.py`,
  `core/crm/services/lead_conversion_service.py`,
  `core/crm/services/lead_duplicate_service.py`,
  `core/crm/services/lead_management_query_service.py`,
  `core/crm/services/lead_merge_service.py`,
  `core/crm/services/lead_normalization_service.py`,
  `core/crm/services/lead_timeline_service.py`, `core/retail/`, `core/security/`,
  `docs/retail/`, `tests/retail_pricing_test.py`, `tests/retail_security_test.py`
  (15 entries)
- Total: 22 + 15 = 37, matches spec's stated count exactly.
- Not touched, not analyzed further, not cleaned. This state pre-dates the
  entire Phase 9.5B-R / 9.5B-R2 session (last commit 2026-07-11, well before
  this session's work). No command executed against this repository this wave
  was anything other than `git status --short`, `git rev-parse HEAD`,
  `git diff --stat`, `git diff --name-only` — all read-only.

## Real Owner surface counts (source-driven, not estimated)

- Registered Flask blueprints: **19**
  (`auth`, `staff`, `catalog`, `customers`, `subscriptions`, `licensing`,
  `installations`, `dashboard`, `audit`, `releases`, `system`,
  `licensing_admin`, `commercial_ops`, `commercial_ops_ui`, `health`,
  `employees`, `profile`, `api_operations`, `locale`, plus conditionally
  `external_api`, `licensing_api`)
- Total route decorators across all blueprint files: **173**
- Total Jinja templates under `app/templates/`: **67**
- Templates already localized as of Phase 9.5B-R (`632100d`): **17**
  (`layout/base.html` ×1, `auth/*` ×7, `employees/*` ×7, `profile/*` ×2)
- Templates NOT yet localized entering this wave: **50**, across 11 template
  directories: `commercial_ops` (19), `licensing_admin` (6), `catalog` (5),
  `subscriptions` (3), `staff` (3), `licensing` (3), `installations` (3),
  `customers` (3), `audit` (3), `system` (1), `dashboard` (1)

**Real discrepancy — remaining-template estimate**: Phase 9.5B-R's own
documentation described "approximately 37 pre-existing templates" as the
untranslated remainder. The actual, source-counted figure is **50**. The
37-template figure undercounted the `commercial_ops` UI surface specifically —
it has 19 templates on its own, more than any other single domain, built
across Phase 9.5A/9.5B commercial-operations work. This is recorded here as a
real, evidence-based correction to the prior estimate, not a silent change.

## Machine-only / non-template blueprints (out of translation scope)

`api` (2 routes, 0 template renders), `api_external` (6 routes, 0),
`api_operations` (18 routes, 0), `locale_routes` (1 route, redirect only, 0),
`commercial_ops` base blueprint (`commercial_ops/routes.py`: 6 routes, 0 —
this is the JSON API paired with the separate `commercial_ops_ui` blueprint
which does the template rendering). These emit only machine-readable JSON or
redirects and are confirmed out of scope for translation per Non-Negotiable
Rule 3.
