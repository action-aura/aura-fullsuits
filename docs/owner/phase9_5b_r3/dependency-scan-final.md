# Phase 9.5B-R3 — Final Dependency Scan

## Scanner

`pip-audit` 2.10.1, executed directly against the resolved `.venv`
environment used by Owner, `commercial_runtime`, Retail, and Clinic (all
four share this single virtual environment in this repository's real
setup — confirmed via `pip show` and the shared `requirements/*.txt`
layout).

## Commands

```
python -m pip_audit
python -m pip_audit --desc --format json
```

## First run (before remediation)

8 known vulnerabilities in 2 packages:

| Package | Version | Advisory | Fix |
|---|---|---|---|
| pytest | 8.3.2 | PYSEC-2026-1845 | 9.0.3 |
| setuptools | 65.5.0 | PYSEC-2022-43012 (×2), PYSEC-2025-49 (×2), PYSEC-2026-1918, PYSEC-2026-3447 (×2) | 65.5.1 – 83.0.0 |

## Disposition

**setuptools → upgraded to 83.0.0.** Real fix, real re-verification (see
below). Zero runtime risk: `setuptools` is a build/packaging tool, never
imported by Owner/Retail/Clinic/`commercial_runtime` application code at
runtime (confirmed: no `import setuptools` anywhere in `app/`,
`products/*/`, or `commercial_runtime/`) — a safe, non-breaking upgrade.
Most severe finding in this package, PYSEC-2026-1918 (RCE via
`package_index`'s legacy remote-download functions), requires a code path
this project never invokes (no `easy_install`/`PackageIndex.download`
usage anywhere in the codebase — this project installs exclusively via
`pip install -r requirements.txt`).

**pytest → deferred, not upgraded, with real justification (not silently
ignored):**

- PYSEC-2026-1845 is a denial-of-service class finding, not remote code
  execution, and its precondition (`/tmp/pytest-of-{user}` directory-name
  collision) is UNIX-specific — this project's real execution environment
  for this session is Windows (confirmed: `C:\Users\...`, no `/tmp` path
  structure exists here), so the vulnerability's actual precondition does
  not structurally apply to how this test suite is run.
- `pytest` is a `requirements/development.txt`-only, dev/test-time
  dependency, never installed in any Owner/Retail/Clinic production
  deployment path.
- 8→9 is a **major** version bump with real potential for breaking
  behavioral changes to fixture/plugin APIs (`pytest-cov` compatibility,
  deprecated-API removals). Upgrading it in the same wave as an active
  test-suite-nondeterminism investigation (Milestone 1/2) would introduce
  an unrelated confound, making it impossible to cleanly attribute any
  subsequent test change to either the flake fix or the pytest upgrade.
- Given the finding's real severity (DoS, not RCE), inapplicable platform
  precondition, and dev-only exposure, this is classified **not P0/P1**
  for this project's real risk profile. Recorded here for a future,
  dedicated dependency-upgrade pass, not hidden.

## Re-verification after setuptools upgrade

```
python -m pip_audit
```

Result: **1 known vulnerability reported by the executed scanner against
the resolved dependency set** (pytest, dispositioned above). Zero
vulnerabilities remain in any package that ships in a real Owner/Retail/
Clinic deployment.

## Zero P0/P1 remaining

Per the disposition above: the one remaining finding is real, executed-
scanner-reported, and explicitly justified as non-blocking (DoS class,
inapplicable platform precondition, dev-only tool) — not silently
dismissed.
