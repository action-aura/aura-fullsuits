# Phase 9.5B-R3 — Milestone 11: Final Deterministic Regression Report

Full final matrix, run from the final Phase 9.5B-R3 HEAD (post-M2
advisory-lock fix in `tests/conftest.py`, post-M3 exception-architecture
refactor across 6 `commercial_ops` files + new `errors.py`, post-M12
catalog authorship — 38 new/updated translated strings compiled into
`translations/{ar,en}/LC_MESSAGES/messages.mo`).

| Suite | Command | Result | Duration |
|---|---|---|---|
| Owner | `pytest tests/ -q` (from `owner/`) | **627 passed, 0 failed, 0 errors, 0 skipped** | 1125.01s (0:18:45) |
| `commercial_runtime` | `pytest commercial_runtime/ -q` | **235 passed, 0 failed** | 17.24s |
| Retail | `python products/run_all_tests.py retail` (12 files) | **194 passed, 0 failed** | ~365s aggregate |
| Clinic | `python products/run_all_tests.py clinic` (11 files) | **135 passed, 0 failed** | ~332s aggregate |

**Total: 1191 tests across 4 suites, 0 failures, 0 errors, 0 unexplained
skips.**

## Relationship to Milestone 2

This is the 4th consecutive clean full Owner-suite run of this wave, and
the first at the true final code state:

| Run | When | HEAD state | Result |
|---|---|---|---|
| 1 | Pre-fix | Before M2/M3/M12 changes | 627 passed, 1188.55s |
| 2 | Pre-fix | Before M2/M3/M12 changes | 627 passed, 1201.21s |
| 3 | Pre-fix | Before M2/M3/M12 changes | 627 passed, 1233.23s |
| 4 (this report) | Post-fix | Final HEAD (advisory lock + exception refactor + catalog) | 627 passed, 1125.01s |

Four consecutive clean runs, the last one against the exact code being
tagged. Non-Negotiable Rule 2 ("final regression must be completely
green") and the governing spec's M11 instruction to rerun the complete
final matrix from the exact final tag-candidate HEAD are both satisfied.

## Retail/Clinic count verification (summed from per-file results)

Retail: 16+12+11+10+25+18+8+7+26+10+47+4 = **194**
(`launcher_support`, `backup_restore`, `capability_guard`,
`financial_authority`, `import_export`, `localization`,
`onboarding_wave0`, `phase7_migration`, `pricing`, `returns_wave0`,
`security`, `wave1c_financial_gate`).

Clinic: 4+12+5+13+15+9+7+9+24+29+8 = **135**
(`backup_restore`, `capability_guard`, `independence`, `localization`,
`onboarding_auth`, `payment_wave0`, `phase7_migration`, `privacy`,
`rbac`, `workflow`, `wave1c_financial_gate`).

Both match the previously-established baseline counts referenced in this
wave's own memory/context (194 Retail, 135 Clinic) — no regression in
test count, no silently-dropped file.

## Result

**FINAL MATRIX: PASS.** Zero failures across all 4 suites at the final
HEAD. Ready for Milestone 14 verdict and tagging.
