# Phase 9.5B-R3 — Entry Baseline

## Primary repository entry state

- Entry HEAD: `ba69736c14670759ef0e40a92b60fa4e0289d7ce` (matches expected `ba69736`)
- Entry branch: `phase9.5/owner-i18n-rtl-final-closure`
- Entry tree: clean
- Working branch created: `phase9.5/owner-i18n-final-verification`

## Historical tag verification (real `git rev-list -n1` output)

| Tag | Expected | Actual | Match |
|---|---|---|---|
| `aura-owner-i18n-rtl-final-closure-phase9-5b-r2-complete` | `ba69736` | `ba69736c14670759ef0e40a92b60fa4e0289d7ce` | YES |
| `aura-owner-i18n-rtl-foundation-phase9-5b-r-complete` | `632100d` | `632100dd3d2d989f10497223e97677646d65be7a` | YES |
| `aura-owner-employee-management-portal-phase9-5b-complete` | `61e507e` | `61e507ebcf2fbaf027063d40dd42d3398840f825` | YES |
| `aura-owner-commercial-operations-phase9-5a-complete` | `21be07b` | `21be07bf5b24a3a26925e2972153f5aea9377212` | YES |
| `aura-commercial-licensing-operations-phase8-complete` | `4131e61` | `4131e610e8e554193b01c1e923ede00f374100e3` | YES |
| `aura-owner-commercial-ops-phase8-conditional-complete` | (unmoved only) | `f593bce770a58d22f7a636db2efde629061ab123` | present, unmoved |

Forbidden tag `aura-secure-staging-phase9-complete`: confirmed absent.

## Legacy repository entry state (read-only)

- Entry HEAD: `414e6ea5ca9fc698fe075a4ae4464cebf0b7bf34`
- Entry modified/untracked file count: **37** (identical to every prior wave)
- Entry `git diff --stat`: 22 files changed, 1441 insertions(+), 260 deletions(-)
- Identical to Phase 9.5B-R2's own entry AND exit state — this repository
  has not changed at all across three consecutive corrective waves.

## Carried-forward implementation (per governing spec's own retained list)

173 Owner routes, 67/67 templates translated, 742/742 catalog messages
(0 missing, 0 fuzzy), 0 hardcoded-string violations, 44 responsive tables,
`commercial_runtime` 235/235, Retail 194/194, Clinic 135/135 — all verified
again this wave, not assumed.

## Unresolved gates entering this wave (real, from Phase 9.5B-R2's own
   honest documentation)

1. One Owner test failure in the full ~619-test run
   (`test_commercial_ops_renewal_requests.py::test_terminal_states_reject_further_transitions`,
   `ObjectDeletedError`), passing 24/24 when its file runs alone —
   classified but not root-caused or fixed.
2. Dependency/secret scans not re-executed after Phase 9.5B-R2's final
   source change.
3. Three service-exception messages
   (`pilot_lifecycle.py`'s max-extensions error,
   `renewal_requests.py`'s two invalid-transition errors) left in English
   after a real request-context bug was found and the translation
   reverted — reachability to a real user never conclusively proven either
   way.
4. Direct browser evidence covered 5 of the real route families
   (Dashboard, Customers, Installations, Licenses, Catalog) — the
   remaining families (Auth/MFA, Employees, Self-profile, Plans/pricing,
   Subscriptions/payments, Audit, Backup/restore, Notifications/queue,
   error pages) were validated only via template-render tests, not real
   browser interaction.
5. Keyboard/focus interaction was not independently re-tested in Phase
   9.5B-R2 — carried forward from Phase 9.5B-R's own evidence.
