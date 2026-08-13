# Phase 9.5A — Baseline

## Entry gate (real, this session)

- `git status`: clean.
- `git branch --show-current` (before branching): `phase9/secure-staging-and-pilot-readiness`.
- `git rev-parse HEAD`: `466b0e515e2b3137e55afc78353de73a335f4972` — matches the required starting commit.
- `aura-commercial-licensing-operations-phase8-complete` -> `4131e61...`, unmoved.
- `aura-owner-commercial-ops-phase8-conditional-complete` -> unmoved.
- `aura-secure-staging-phase9-complete`: confirmed does NOT exist (`git tag --list` empty result).
- Original `AuraEnterprise` repository: not opened this session.
- Working branch created: `phase9.5/commercial-operations-foundation`, off `phase9/secure-staging-and-pilot-readiness` at `466b0e5`.

## Referenced-document discrepancy (recorded honestly, consistent with the same finding in Phase 9)

`docs/owner/phase8vp9/PHASE8VP9-FINAL-CONTRACT-AND-DEVICE-CLOSURE-HANDOVER.md` does not exist in this
repository (confirmed via `ls docs/owner/phase8vp9/`) — same discrepancy already recorded in
`docs/owner/phase9/phase9-baseline.md`. Not re-litigated; the real, closest equivalent
(`docs/owner/phase8vp9/phase8-final-unconditional-decision-vp9.md`) was read instead.

All six other required Phase 9 documents exist and were read in full:
`PHASE9-SECURE-STAGING-AND-PILOT-READINESS-HANDOVER.md`, `phase9-final-decision.md`,
`final-staging-gate-matrix.md`, `final-residual-risk-register.md`, `final-regression-report.md`,
`environment-separation-matrix.md`, `secrets-and-key-management.md`.

## Phase 9 status inherited (not reopened)

CONDITIONAL PASS. No remote staging deployment, no `aura-secure-staging-phase9-complete` tag. Phase
9.5A does not touch deployment/infrastructure — it is a pure domain-model/API-contract/migration
phase, entirely local, and does not depend on Phase 9's remaining NOT VERIFIED items.

## Real Owner codebase audit performed before any design (Milestone 1)

A full read-only audit of `owner/app/` and `owner/migrations/versions/` was performed before writing
any new model or migration — see `existing-owner-capability-audit.md`,
`existing-domain-reuse-matrix.md`, `duplication-risk-report.md`. Current migration head confirmed:
`0f8d55b753ed` ("phase 8 milestone 5 manual activation approval and device slot ops"), 6 revisions
total, chain verified in order.

## Real, load-bearing finding that shapes the Lead/Customer design (Milestone 6)

`Customer.lifecycle_status` (String(32), default `"LEAD"`) already exists, but is a vestigial,
unstructured field: grep across the entire real codebase confirms it is only ever written as `"LEAD"`
(at row creation) or `"ARCHIVED"` (via `archive_customer()`) — never `"ACTIVE"` or `"PILOT"`, despite
`owner/app/dashboard/services.py` already querying both of those values for real dashboard counts
(`pilot_customers`, `active_customers`). Those two existing dashboard counts have therefore always
reported `0` in the real running system — a genuine, pre-existing latent gap, not something this phase
introduced. This is exploited constructively, not treated as a blocker: the new `Lead` entity
(Milestone 6) becomes the real pre-qualification pipeline, and `LeadConversionService` sets the
newly-created `Customer.lifecycle_status = "ACTIVE"` on conversion — finally making the existing
dashboard query meaningful, with zero schema change to the existing `Customer` table and zero risk to
any existing Phase 5-8 test or code path (none of them depend on `lifecycle_status` ever being
`"ACTIVE"`).
