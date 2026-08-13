# Phase 9.5B — Baseline

## Entry gate (real, verified)

- `git status`: clean at entry.
- `git branch --show-current` (before branching): `phase9.5/commercial-operations-foundation`.
- `git rev-parse HEAD` (before branching): `21be07bf5b24a3a26925e2972153f5aea9377212`.
- `git merge-base --is-ancestor 21be07b HEAD`: confirmed ancestor (trivially — it IS HEAD).
- `aura-owner-commercial-operations-phase9-5a-complete` → `21be07b`. Matches (the governing spec names
  this tag `aura-owner-commercial-operations-foundation-phase9-5a-complete` — a naming discrepancy from
  the real tag created in this repo; the real tag is used, not re-created under the spec's name).
- `aura-commercial-licensing-operations-phase8-complete` → `4131e610e8e554193b01c1e923ede00f374100e3`. Unmoved.
- `aura-owner-commercial-ops-phase8-conditional-complete` → `f593bce770a58d22f7a636db2efde629061ab123`. Unmoved.
- `aura-secure-staging-phase9-complete`: does not exist. Confirmed.
- Original `C:\Users\Dell\Desktop\AuraEnterprise\AuraEnterprise` repository: untouched this session (a
  wholly separate repository, never opened this phase).
- New branch created: `phase9.5/employee-management-portal`, from `21be07b`.

## Naming discrepancy, recorded honestly

The governing spec references several Phase 9.5A documents that were not created under those exact
names (`PHASE9-5A-COMMERCIAL-OPERATIONS-FOUNDATION-HANDOVER.md`, `final-gate-matrix.md`,
`final-residual-risk-register.md`, `final-regression-report.md`). The real, existing equivalents are:
`phase9-5a-final-decision.md` (contains the final decision, bug list, and regression totals),
`phase9-5a-gate-matrix.md` (the final gate matrix), `milestone-24-security-tests.md` (the real/reserved
security requirement breakdown — functions as the residual-risk register). No separate handover doc was
created in Phase 9.5A. All required reading was done against these real files — see
`retained-foundation-audit.md` for what was actually read and learned.

## Real regression baseline inherited

474/474 Owner tests passing at `21be07b` (423 pre-existing + 51 Phase 9.5A). This is the number Phase
9.5B's own final regression must meet or exceed with zero failures.
