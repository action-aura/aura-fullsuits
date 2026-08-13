# Phase 9.5E Milestone 26 — Dependency Vulnerability Scan (Executed)

Real `pip-audit --format json` run against the active `.venv` (the exact
environment the frozen Owner executable and every test run in this phase use)
on 2026-08-03. Not a documentation claim -- raw JSON evidence preserved at
`scratchpad/pip_audit_9_5e_final.json` for this run.

## Result

```
Found 1 known vulnerability in 1 package
```

| Package | Installed | Vulnerability | Fixed in | Description |
|---|---|---|---|---|
| pytest | 8.3.2 | PYSEC-2026-1845 (CVE-2025-71176, GHSA-6w46-j5rx-g56g) | 9.0.3 | Predictable `/tmp/pytest-of-{user}` directory naming on UNIX allows local users to cause DoS or possibly gain privileges |

Every other dependency (110 packages scanned, full list in the raw JSON) reports zero known vulnerabilities.

## Disposition

Same accepted residual risk documented in every prior Owner phase's own dependency scan (Phase 9 staging-pilot-readiness, Phase 9.5A, Phase 9.5D):

- The vulnerability is UNIX-only (`/tmp` path predictability); this project's runtime and CI both run on Windows, and the vulnerable code path is never reached by anything except `pytest`'s own local tempdir bookkeeping.
- `pytest` is a test-time dependency only -- it is never bundled into the frozen `AuraOwner.exe`/`AuraOwner-Dev.exe` artifacts (verified by the existing PyInstaller spec, which does not include the `tests/` package or `pytest` in its hidden-imports).
- No new Phase 9.5E code or dependency introduces this or any other vulnerability; the finding is inherited unchanged from every prior phase's own accepted baseline.

**No action required for Phase 9.5E.** Upgrading `pytest` to 9.0.3+ remains an open, low-priority housekeeping item tracked identically to how prior phases left it -- not a Phase 9.5E blocker.
