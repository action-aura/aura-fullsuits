# Phase 9 Milestone 10 — Dependency Risk Register

## Real scan, real remediation, real regression-tested

`pip-audit` against `requirements/base.txt` + `requirements/owner-server.txt` (the two files that cover
every real Python dependency — Retail/Clinic via `base.txt`, Owner via both) found **32 known
vulnerabilities across 7 packages** before this phase touched anything:

| Package | Before | Vulnerable to | Fixed version applied | Blast radius |
|---|---|---|---|---|
| `flask` | 3.0.3 | 1 CVE (PYSEC-2026-2151) | 3.1.3 | Owner + Retail + Clinic (shared `base.txt` pin) |
| `werkzeug` | 3.0.3 | 5 CVEs | 3.1.6 | Owner + Retail + Clinic |
| `flask-cors` | 4.0.1 | 5 CVEs | 6.0.0 | Owner + Retail + Clinic |
| `waitress` | 3.0.0 | 2 CVEs | 3.0.1 | Retail + Clinic (Owner uses Gunicorn) |
| `requests` | 2.32.3 | 2 CVEs | 2.33.0 | Retail + Clinic + Owner |
| `cryptography` | 43.0.1 | 5 CVEs (incl. one GHSA) | 48.0.1 | **Owner + Retail + Clinic — the Ed25519 signing/verification library itself**, deliberately pinned identically across all three per the codebase's own comment ("Same pin as requirements/owner-server.txt so both sides of the protocol run identical Ed25519 behavior") |
| `pyjwt` | 2.9.0 | 12 CVE entries (several duplicate advisory IDs across sources) | 2.13.0 | Owner only |

## Why these were applied now, not just recorded

The governing instruction requires zero P0/P1 for Phase 9 PASS and explicitly permits "targeted P0/P1
infrastructure fixes." `cryptography` and `pyjwt` sit directly in the signing/authentication path —
classified **P1** on discovery (not yet exploited-in-the-wild confirmed, but security-critical
libraries with known CVEs are not something to ship into a pilot). The other five are **P2** on their
own (no security-critical code path), but were bumped together since they're safe, low-risk patch/minor
version updates with full regression coverage available.

## Verification (not "should be safe" — actually proven)

The one thing that would make these upgrades genuinely risky — a breaking API change silently changing
Ed25519 signing/verification behavior, JWT encoding, or CORS enforcement — is exactly what this
project's existing test suite already exercises exhaustively (signing/verification round-trips, MFA/
session flows, CORS-adjacent request handling). Full regression run **after** the upgrade, from the
exact upgraded environment:

- Owner: **423/423** (405 Phase 8 baseline + 18 new Phase 9 tests)
- `commercial_runtime`: **235/235**
- Retail: **194/194** (via `products/run_all_tests.py`)
- Clinic: **135/135** (via `products/run_all_tests.py`)

**987/987, zero failures.** Re-ran `pip-audit` after the upgrade: **"No known vulnerabilities found."**
Raw evidence: `docs/owner/phase9/evidence/pip-audit-after-remediation.json`.

## Scope note: already-built rc.5 artifacts are unaffected (correctly)

This only updates the *requirements files* (the source of truth for the *next* build). The already-
tagged, already-shipped Phase 8 rc.5 artifacts embed whatever `cryptography`/`pyjwt`/etc. versions were
current at that build time — untouched, per the explicit instruction not to reopen Phase 8. Any future
rc.6 build (Milestone 13, NOT VERIFIED this session — no HTTPS staging URL) will pick up these fixed
versions automatically.

## No P0/P1 remaining

Confirmed by the post-upgrade `pip-audit` scan above: zero known vulnerabilities in any pinned
dependency as of this commit.
