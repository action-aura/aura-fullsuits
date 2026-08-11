# Phase 9 — Phase 8 Evidence Reuse Decision

## What is reused as-is (not re-verified this session)

All Phase 8/8V-P/8V-P7/8V-P9 commercial-enforcement, licensing-contract, activation, renewal, expiry,
grace, emergency-extension, pilot-conversion, device-replacement, device-limit, downgrade,
device-overage-reconciliation, temporary-device-exception, audit, and backup/restore evidence is
reused without re-verification. Phase 8 is complete and closed; Phase 9 does not reopen it. No source
in `owner/app/commercial_ops`, `owner/app/licensing*`, `commercial_runtime/licensing_contracts`
(business logic, not deployment packaging), or the product commercial-enforcement paths is touched by
this phase.

## What is NOT reused — must be freshly re-verified this session

- All test totals (Milestone 19 requires a fresh run from the final Phase 9 HEAD; the Phase 8 405/235/
  194/135 numbers are a *starting reference*, not a substitute).
- Artifact versions — Phase 9 staging-connected artifacts (where achievable) must be a new immutable
  version, not a relabeled rc.5.
- Anything touching deployment, packaging, secrets, network exposure, backups, monitoring, or
  scheduling — none of this existed as verified Phase 8 evidence; Phase 8 was validated entirely
  against a local dev Owner process, not a hardened deployment.

## Rationale

Phase 8's scope was commercial-licensing *correctness*. Phase 9's scope is *how that already-correct
system is safely operated*. The two are orthogonal — reusing Phase 8's functional evidence is correct
and required (`Phase 8 is complete and must not be reopened without a newly reproduced regression`);
treating it as evidence of deployment/operational readiness would not be honest, since none of that was
tested in Phase 8.
