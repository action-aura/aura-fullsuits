# Phase 9R — Final Residual Risk Register (Repository-Controlled Closure)

## Infrastructure-blocked (not a code risk — see `infrastructure-acquisition-manifest.md`)

| Risk | Severity | Status |
|---|---|---|
| No real domain/DNS/trusted TLS | Blocking for M18/M19/M26/M27 | Documented, not fabricated |
| No remote host | Blocking for M18-M27 | Documented, not fabricated |
| No external object storage | Blocking for M11's real artifact delivery, M12's off-host backup | Local/test adapter proven; real adapter not yet built (deliberate M11 scoping) |
| No monitoring/alert destination | Blocking for M14/M15's real delivery | Signals and catalog real and ready to wire in |
| CI never actually executed | Blocking for M16's hosted-run verification | Remote now exists (new this session); push is the repo owner's decision, not made here |

## Known, accepted, unchanged from prior phases

| Risk | Severity | Disposition |
|---|---|---|
| `pytest` UNIX-only tempdir CVE (PYSEC-2026-1845) | Low | Windows-only runtime/CI, test-only dependency, never in the frozen artifact — accepted unchanged since Phase 9/9.5A/9.5D/9.5E |
| `aura_owner` local dev role has `CREATEDB` | Low, local-only | Shared local dev role, out of this project's scope to revoke; real staging/production role must be provisioned without it from day one (`production-postgresql.md`) |
| No explicit clock-skew tolerance on assertion verification (distinct from the activation-timestamp check) | Low | Noted in `signed-license-lease-contract.md`, tracked for M20 real remote-client validation where real clock drift becomes observable |

## Found and fully closed this phase (not residual — listed for completeness)

1. Unbounded DB statement/lock/idle-in-transaction timeouts — fixed, tested (M4)
2. Missing `Installation` composite index (full table scan on every activation) — fixed, tested (M4)
3. Dead scheduler execution path (`SCHEDULER` generated_by value unreachable) — fixed, tested (M5)
4. Broken expense-attachment uploads over 64KB — fixed, tested (M6)
5. Incorrect License-status check in the new download-authorization flow (`ACTIVE` vs real `ISSUED`) — fixed, tested (M11), before it ever shipped
6. Download-token leaking into access logs — fixed, tested (M14)
7. Anti-enumeration HTTP-status side channel between `RELEASE_NOT_FOUND`/`RELEASE_NOT_PUBLISHED` — fixed, tested (M11)
8. `cryptography` 48.0.1 → 50.0.0 (3 CVEs, confirmed unreachable by this codebase's actual usage, upgraded as defense in depth anyway) — fixed, tested (M0)
9. Broken `alembic downgrade` for migration `96429a63cb29` (M10) — `drop_constraint(None, ...)` is not valid; the downgrade path had never actually been executed before the final regression caught it. Fixed with the real Postgres-generated constraint names, proven by actually running the downgrade (twice) and upgrade back, not just re-reading the diff — found and closed during the final repository-controlled regression (closure), the last of the real bugs this phase surfaced

## Explicitly not risks (structural guarantees, re-confirmed this phase)

- No Retail/Clinic operational business data can reach Owner — confirmed
  structurally true at every milestone that touched the licensing/
  distribution boundary (M8, M9, M11), not just asserted once.
- Legacy repository and Unified Mobile workspace integrity — verified at
  every checkpoint throughout this phase, unchanged.

## What remains open, honestly

Everything in the "Infrastructure-blocked" table above. No other
repository-controlled P0/P1 is known open at closure — see
`final-regression-report.md` for the exact test evidence this claim rests
on.
