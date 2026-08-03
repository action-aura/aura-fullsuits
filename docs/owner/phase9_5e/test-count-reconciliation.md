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
