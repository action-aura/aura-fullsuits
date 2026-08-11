# Phase 9.5E — Test Count Reconciliation (876 → 884 vs. 42 new)

The checkpoint report said "42 new tests" and "884/884 full Owner regression," against a stated Phase 9.5D baseline of 876 — an apparent +8 instead of +42. Investigated with real `pytest --collect-only` evidence, not inference.

## Evidence

1. **The Phase 9.5D baseline was never actually 876.** Checked out a temporary git worktree at the exact tag `aura-owner-commercial-sales-phase9-5d-complete` (commit `0472043`) and ran `pytest tests/ --collect-only -q` there directly:

   ```
   879 tests collected in 7.95s
   ```

   The "876" figure carried in this session's memory/summary was a recorded number from Phase 9.5D's own closure documentation, not re-verified against the real tag before being repeated here. It was off by 3. This is a pre-existing documentation drift, not something introduced by Phase 9.5E — no Phase 9.5D test file was ever modified during 9.5E (confirmed: every `git diff --stat` this phase touched only new `test_phase9_5e_*.py` files).

2. **The background full-suite run (`884 passed`) started before 37 of the 42 new tests' files existed on disk.** The process (PID 38832) has a confirmed `CreationDate` of `2026-08-02 20:55:22`. File mtimes for the four new Phase 9.5E test files:

   | File | mtime | Existed at 20:55:22? |
   |---|---|---|
   | `test_phase9_5e_dev_server_port_isolation.py` | 20:13:55 | Yes (5 tests) |
   | `test_phase9_5e_expense_payments_attachments_duplicates.py` | 21:10:31 | No |
   | `test_phase9_5e_cash_closing.py` | 21:11:03 | No |
   | `test_phase9_5e_expense_lifecycle_and_approval.py` | 21:25:02 | No |

   pytest collects every test item once, at the start of a run — files created after that point are structurally invisible to an already-running invocation. The background run could only ever have collected the 5 port-isolation tests from Phase 9.5E's own work.

3. **The arithmetic closes exactly:** `879 (real 9.5D baseline) + 5 (only 9.5E file collectible at the time) = 884` — matching the reported result exactly, with zero unexplained residual.

4. **Current full collection, fresh, at this checkpoint's HEAD** (all four new files present): `879 + 42 = 921`, independently confirmed via `pytest tests/ --collect-only -q` → `921 tests collected`.

## Conclusion

No tests were silently consolidated, parameterized-and-undercounted, or double-counted. Two independent, ordinary causes, both now closed:
- A stale baseline figure (876 vs. the real 879) carried forward in memory without re-verification.
- A background regression launched mid-build, before most of the new test files existed, so it validated schema-level non-regression correctly but never actually executed 37 of the 42 new tests.

**Corrective action:** the 42 new tests were subsequently re-run to completion against an isolated scratch database (`aura_owner_test_9_5e_scratch`, migrated to head) after all four files existed — `42 passed` (see the M2-14 commit message). The full current-tree collection (`921`) is the number that must be used as the baseline for every remaining Phase 9.5E milestone's regression reporting from here on, not `876`.

## Update — second reconciliation (65 vs. the reported "73")

After Milestones 15–19, the checkpoint report to the user stated "73 Phase 9.5E tests green" without re-running `pytest --collect-only` to verify it — a real counting error, the same class of mistake as the original 876 figure (a number carried forward and repeated without re-derivation from evidence).

**Real evidence, collected per file:**

```
tests/test_phase9_5e_api_expenses_and_operations.py                     : 10
tests/test_phase9_5e_audit_catalog.py                                   :  3
tests/test_phase9_5e_cash_closing.py                                    : 10
tests/test_phase9_5e_dev_server_port_isolation.py                       :  5
tests/test_phase9_5e_expense_lifecycle_and_approval.py                  : 15
tests/test_phase9_5e_expense_payments_attachments_duplicates.py         : 12
tests/test_phase9_5e_migration_and_preflight.py                         :  4
tests/test_phase9_5e_web_operations_ui.py                               :  6
                                                                    total: 65
```

`879 + 65 = 944`. `pytest tests/ --collect-only -q` at this exact HEAD reports **944 tests collected** — an exact match, zero unexplained residual. Independently re-confirmed the pre-9.5E baseline is still exactly `879` via `pytest tests/ --collect-only -q --ignore-glob="tests/test_phase9_5e_*.py"`, so no regression in the base suite either.

**Root cause of the "73" error:** the number was accumulated by mentally adding partial `passed` counts reported across several intermediate `pytest` runs during the session (52 → 58 → 61, then a further mental addition that was never re-verified against a fresh `--collect-only`) rather than being recomputed from the actual file set at the time of the final report. No tests were lost or hidden — the underlying work (M15–M19) is exactly what was built and committed; only the summary arithmetic was wrong.

**Corrective standard going forward:** every test-count claim in this phase's remaining milestones must be backed by a `pytest --collect-only -q` (or an explicit per-file sum shown, as above) run immediately before it is reported — never a remembered running total.

## Update — third and final reconciliation (65 -> 93, after M12/M20/M21/M23-26 landed)

The "65 / 944 total" figure above was itself a mid-phase checkpoint, captured
after Milestones 15-19 and before Milestones 12, 20, 21, 23, 24, 25, and 26
added their own test files/cases. Per this document's own corrective
standard, the number was re-derived from a fresh `pytest --collect-only -q`
at the actual final HEAD rather than carried forward or added to by memory.

**Real evidence, collected per file, at the M26 HEAD (including the new
`test_phase9_5e_backup_restore.py`):**

```
tests/test_phase9_5e_api_expenses_and_operations.py                     : 10
tests/test_phase9_5e_audit_catalog.py                                   :  3
tests/test_phase9_5e_backup_restore.py                                  :  1
tests/test_phase9_5e_cash_closing.py                                    : 10
tests/test_phase9_5e_dashboards.py                                      :  6
tests/test_phase9_5e_dev_server_port_isolation.py                       :  5
tests/test_phase9_5e_expense_lifecycle_and_approval.py                  : 15
tests/test_phase9_5e_expense_payments_attachments_duplicates.py         : 12
tests/test_phase9_5e_financial_timezone_scheduler.py                    :  7
tests/test_phase9_5e_full_local_e2e.py                                  :  1
tests/test_phase9_5e_migration_and_preflight.py                         :  6
tests/test_phase9_5e_security_and_idor.py                               : 11
tests/test_phase9_5e_web_operations_ui.py                               :  6
                                                                    total: 93
```

`879 (verified 9.5D baseline) + 93 (real 9.5E total) = 972`. `pytest
--collect-only -q` at this exact HEAD reports **972 tests collected** --
an exact match, zero unexplained residual.

Note `test_phase9_5e_migration_and_preflight.py` grew from 4 (at the second
reconciliation, before M25) to 6 (M25 added the attachment-corruption and
duplicate-published-snapshot corruption tests on top of the two schema
tests that were already there) -- consistent with `blocking-preflight.md`'s
own description of what M25 added, not a discrepancy.

**This 93-test / 972-total figure is the one that must be used for the
final five-repository regression and the final gate matrix** -- the earlier
65/944 figures in this document remain as an honest record of what was true
at their own checkpoint, per this phase's standing instruction never to
silently overwrite prior evidence.
