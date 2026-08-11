# Phase 9R — M14/M15: Observability, Alerting, Operational Security Monitoring

## Extends real, already-tested Phase 9 infrastructure

Phase 9 already built structured/redacted JSON logging
(`app/observability/logging_config.py`, real, 7 tests), a real
`/health/live` + `/health/ready` contract (`health-readiness-contract.md`,
backed by the existing `flask commercial preflight` checks), and a design
alert catalog (`alert-catalog.md`, 24 rows mapped to real signals). This
milestone re-verified all of it against the current, much larger schema and
found one real gap the M11 build introduced.

## `/health/ready` re-verified against the current preflight surface

Hit the real endpoint against the real local dev database:

```
$ curl /health/ready
```

**50 checks now** (Phase 9's own evidence showed 12) — every 9.5A-E
addition (i18n catalog completeness, lead/customer/quote/sales-order/
invoice/refund/commission/expense/cash-closing/management-note status and
integrity checks) is present and correctly wired into the same real
`/health/ready` contract, with zero code changes needed for this
milestone — the preflight system was already built to be extended this way
by each phase that added it. `database_connectivity: OK` and
`migration_at_head: OK` confirm the two new migrations from this phase
(M10, M11) are correctly recognized. One `FAIL`
(`signing_key_sign_verify_roundtrip`) observed and confirmed to be a
pre-existing local-environment gap (no signing key ever generated for this
fresh worktree's dev database — `owner/var/signing-keys/` doesn't exist at
all), not a regression from this phase's work.

## Real gap found and fixed: M11's download token leaked into logs

`app/observability/logging_config.py`'s redaction patterns are all
`key=value`/`key: value`/full-license-key-shape matches. M11's download
token lives directly in a URL path segment
(`/releases/download/<token>`) with no `key=` prefix at all — confirmed by
direct test that none of the 8 existing patterns matched it, and that
`request.path` (logged on every request via `_log_request`) would have put
the raw bearer token into every access-log line for its own fetch. Fixed:
a 9th pattern, a lookbehind-anchored match on the specific route, redacts
only the token segment while leaving the rest of the path (and every other
route) untouched. Two new tests
(`test_redacts_release_download_bearer_token_from_url_path`,
`test_does_not_over_redact_other_release_routes`) — 9/9 passing in the
existing `test_observability_logging.py` (extended in place, not
forked into a new file).

## Alert catalog extended for M10/M11/M13's new signals

Added five rows to the real design catalog (`docs/owner/phase9/alert-catalog.md`):
repeated release-download denial (M11 abuse signal), unexpected SUPER_ADMIN
creation, privileged role/permission change, signing-key rotation
occurred, and restore invoked (ties directly to M13's real
`OWNER_DB_RESTORE_SUCCEEDED`/`FAILED` audit events). Consistent with the
existing catalog's own honest framing: design-level, mapped to real signals
that already exist, **not verified as a running Prometheus/Alertmanager
system** — that requires infrastructure this phase doesn't have
(`infrastructure-availability-audit.md`).

## Monitoring and audit remain separate authorities, as required

Every new event this phase added (`RELEASE_PUBLISHED`, `RELEASE_WITHDRAWN`,
`RELEASE_DOWNLOAD_AUTHORIZED`, `RELEASE_DOWNLOAD_FETCHED`) goes through the
existing canonical `audit_record()` / hash-chain (`app/audit/services.py`)
— the same authority every other Owner event uses, not a parallel logging
path. The alert-catalog rows above are monitoring *on top of* that audit
trail (pattern/threshold detection), never a replacement for it.

## Disposition

**PASS for the repository-controlled portion.** Real structured logging,
real health/readiness (now 50 real checks), real redaction (now 9 tested
patterns, one gap found and closed), real alert catalog extended for this
phase's own new surface. What remains **NOT VERIFIED**: an actual running
Prometheus/Alertmanager/log-aggregation stack receiving these signals over
a real network — unchanged from Phase 9's own honest accounting, blocked
on infrastructure.
