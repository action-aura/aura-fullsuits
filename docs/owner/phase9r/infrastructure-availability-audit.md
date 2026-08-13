# Phase 9R — Infrastructure Availability Audit (M0)

Date: 2026-08-04. Answers captured directly from the project owner; nothing
below is assumed or invented.

| Requirement | Status | Notes |
|---|---|---|
| VPS / cloud provider account | **None** | No provider selected or provisioned |
| Server region | **N/A** | Depends on provider selection |
| Server operating system | **N/A** | To be selected in M1 (deployment-architecture.md) against currently supported distributions, not decided here |
| Domain ownership | **None** | No domain purchased or owned |
| DNS provider | **None** | Depends on domain registrar/DNS choice |
| SMTP provider | **None** | Not yet required — no feature in the completed Phase 9.5E baseline sends outbound email; revisit if M24 runbooks need alerting-via-email |
| Object storage (private release artifacts, external backups) | **None** | Needed for M11 (private distribution) and M12 (external backup) |
| Backup target (external, off-server) | **None** | Same as above — must be genuinely off the production host |
| Monitoring/alerting destination | **None** | Needed for M14/M15 |
| Existing deployment credentials | **None** | Nothing to inventory |
| Existing environment secrets | **None in production form** | Local dev secrets exist in `.env` files, gitignored, not production-grade |
| Database size (production) | **N/A — no production DB exists** | Local dev/test PostgreSQL only |
| Expected employee count | Not yet specified | Ask before sizing connection pools / rate limits precisely |
| Expected active installations | Not yet specified | Ask before sizing device-cap/rate-limit defaults |
| Expected activation rate | Not yet specified | Same |
| Expected API request rate | Not yet specified | Same |

## Consequence for scope

This audit is the controlling fact for the rest of Phase 9R. Per the
project's own external-blocker rule:

- Every milestone that is genuinely repository-controlled (configuration
  contracts, secret/key lifecycle design, PostgreSQL production hardening,
  application-server topology, licensing and distribution hardening,
  backup/DR *tooling* — as opposed to a live backup target, observability
  *code*, CI/CD pipeline *definition*, migration/rollback procedure,
  runbooks, pilot plan) proceeds now.
- Every milestone that structurally requires a live public domain, a real
  remote server, real external object storage, or a real external monitoring/
  backup destination (M6 domain/DNS/TLS, M18 remote staging, M19 remote
  browser validation, M20 remote licensing client validation, M21 remote
  concurrency/abuse testing, M22 remote performance testing, most of M23
  security validation, M27 remote E2E) **cannot be executed** and is recorded
  as **NOT VERIFIED**, with this document as the named reason, not silently
  skipped and not faked.
- No final Phase 9R completion tag (`aura-owner-real-production-phase9r-complete`)
  is created while any PASS-required gate reads NOT VERIFIED.

See `external-dependency-register.md` for the exact provisioning checklist
needed to close each gap, and `production-cost-model.md` (M1) for a cost
estimate once a provider is selected.
