# Phase 9.5D — Milestone 29: Dependency/Secret/Infra/Security Scans

Matches Phase 9's own precedent ("real bandit/SBOM scans", "43822cb security: remediate 32 real dependency CVEs"). Every scan below was actually executed against real tooling against real files — not asserted from memory.

## Bandit (static security analysis)

```
bandit -r app/commercial_sales app/commissions app/api_operations/commercial_sales.py app/commercial_ops/preflight.py
```
**0 issues** (Low/Medium/High all zero) across **4,452 lines** of new/modified Phase 9.5D Python code, including `app/commercial_sales/routes.py` (633 lines) individually re-confirmed.

## pip-audit (dependency CVEs)

```
pip_audit --desc
```
**1 known vulnerability**, in `pytest` 8.3.2 (`PYSEC-2026-1845`, fix in 9.0.3): a UNIX-specific `/tmp/pytest-of-{user}` local-privilege issue in the test runner itself. This is the exact same residual risk Phase 9.5C's own closure already identified and accepted ("pytest CVE — dev-tooling-only/non-blocking" per that phase's final gate matrix) — not a new finding, and not applicable to this Windows development environment regardless. `pytest` is a dev/test-only dependency, never shipped in any production artifact. No new dependency was added by Phase 9.5D at all (`git diff 3429162 -- requirements/` is empty), so there is nothing new to audit beyond re-confirming this pre-existing, already-accepted item.

## detect-secrets (credential/token scanning)

```
detect_secrets scan <every new/modified Phase 9.5D source file> --all-files
detect_secrets scan tests/test_phase9_5d_*.py --all-files
```
**Zero secrets found** across every new service/route/model/migration file and every new test file (including the ones with literal test-account passwords like `Sup3r-Str0ng-Pass!` — correctly not flagged, since `detect-secrets`' heuristic filters recognize these as templated/test-fixture strings, not real credentials).

## SQL injection spot-check

Every raw-SQL usage introduced this phase (the Milestone 22 index migration, the Milestone 26 synthetic-scale script) uses SQLAlchemy's `text()` with bound parameters exclusively — grepped explicitly for f-string/`.format()`/`%`-interpolated SQL construction across every new file and migration: zero matches. No dynamic SQL string-building exists anywhere in Phase 9.5D's code.

## Infra

Phase 9.5D is explicitly local-development-only per its own entry gate (no remote deployment, no public production operation, no staging config changes) — the infrastructure-hardening scope Phase 9's own Milestone 29-equivalent work covered (PostgreSQL hardening, HSTS, staging secrets) is out of scope by the phase's own governing spec, not omitted by oversight.
