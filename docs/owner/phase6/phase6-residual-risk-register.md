# Phase 6 -- Residual Risk Register

| # | Item | Severity | Disposition |
|---|---|---|---|
| 1 | `pyjwt` dependency is unused (Phase 6 chose a hand-rolled signed envelope, ADR-6.5) but still carries CVE exposure in `requirements/owner-server.txt` | Low | Recommend removal in a follow-up pass (`dependency-security-scan.md`) |
| 2 | `cryptography` 43.0.1 has known advisories, though not in the Ed25519 code path this app uses | Medium | Recommend upgrading to latest patch + full crypto-suite re-run in the next maintenance pass |
| 3 | `flask`/`werkzeug`/`flask-cors` advisories present, not fixed this phase (broader upgrade, out of Phase 6's scope) | Low-Medium | Scheduled follow-up, full regression required |
| 4 | Rate limiting has no license-HMAC-derived bucket dimension yet -- a single-key, multi-IP guessing pattern is not detectable by the current IP-only bucketing | Medium | Documented gap; a future phase could add a safe (hashed) derived bucket |
| 5 | Nonce/idempotency/rate-limit table cleanup (`purge_expired_*`) exists as a function but is not wired to a scheduled job | Low | Tables grow unbounded until manually purged; acceptable at pilot scale, needs a cron/scheduled task in a future phase |
| 6 | No CI pipeline runs the test suite or `pip-audit` automatically (pre-existing Phase 5 residual risk, not resolved by Phase 6) | Medium | Carried forward from Phase 5 |
| 7 | Signing-key backup is explicitly NOT automated (private PEM files live outside the database, outside the automated Postgres backup) | Medium (by design) | Documented in `server-signing-key-management.md`; an operator must define a separate, explicitly-secured key-backup process before any real production use |
| 8 | The `/activations/check` route is currently an alias for check-in rather than a distinct read-only status probe | Low | Functionally adequate for Phase 6's scope; could be split into a genuinely separate, non-state-mutating endpoint in a future phase if a real client needs that distinction |
| 9 | Offline-policy enforcement inside Retail/Clinic does not exist (by design -- Phase 6 defines and returns policy only) | N/A (explicitly out of scope) | Phase 7/8 decision |
| 10 | Concurrency proof covers 2 simultaneous threads against one process; true multi-process (multiple `gunicorn` workers) concurrency was not separately load-tested, though the underlying mechanism (Postgres row lock) is process-agnostic by construction | Low | The row-lock mechanism does not depend on single-process assumptions, but a dedicated multi-worker load test would add further confidence in a future phase |

## Explicitly NOT risks (by design, verified)
No plaintext license key persisted, logged, or returned anywhere (structurally proven, not just claimed). No server or device private key ever stored in a database column, logged, or shown in any UI. No forbidden business/medical data reachable through any Phase 6 table or response (structurally proven). External API unreachable unless explicitly enabled, and fails closed on every dependency (database, replay store, signing key) when enabled. No license enforcement wired into Retail or Clinic -- none of this phase's code is imported by, or imports, product code.
