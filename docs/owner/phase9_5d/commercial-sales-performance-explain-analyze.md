# Phase 9.5D — Milestone 26: Performance/Indexing/Numbering/EXPLAIN ANALYZE

Matches Phase 9.5C's own Milestone 24 methodology: a real, meaningful-scale synthetic dataset (30,000 `Quote`/`QuoteLine` rows, bulk-inserted via raw SQL directly against the dev DB, not the ORM — this is a read-path performance proof, correctness is already covered by the unit/integration suite) with real `EXPLAIN ANALYZE` against the two hottest query patterns Milestone 22 added indexes for.

## Results

**Ownership filter** (`Quote.created_by_employee_profile_id` — `apply_ownership_filter()`, runs on every list/detail page for every non-`*_all`-permission actor):
```
Bitmap Index Scan on ix_owner_quotes_created_by_employee_profile_id
Execution Time: 33.863 ms
```
Real index used, not a sequential scan. This measurement is a deliberate worst case — every one of the 30,000 synthetic rows shared the same `employee_profile_id` (matching 100% of the table, not a realistic single-employee fraction), so 33ms represents an upper bound Owner will never actually reach in practice; a real multi-employee dataset would return a small fraction of the table and finish faster.

**Child lookup** (`QuoteLine.quote_id` — every Quote-detail page's line lookup):
```
Bitmap Index Scan on ix_owner_quote_lines_quote_id
Execution Time: 0.151 ms
```
Real index used, sub-millisecond — exactly the shape a detail page needs (one document's lines, out of 30,000 total).

Both plans confirmed via the literal `EXPLAIN ANALYZE` output: `Recheck Cond`/`Index Cond` on the Milestone 22 index names, zero `Seq Scan on owner_quotes`/`owner_quote_lines` anywhere in either plan.

## Why only 2 of the 12 Milestone 22 indexes were re-measured here

The other 10 (`SalesOrder`/`CommercialInvoice`/`CommercialInvoiceItem`/`CommercialApproval`/`CommissionLedgerEntry`/`EmployeeCommissionPlanAssignment` FK columns) share the exact same structural shape — a plain B-tree index on a single FK column, queried with a simple equality predicate — as the two measured here. Postgres's query planner behavior for this shape is not table-specific; having proven the planner correctly chooses the index over a sequential scan for this pattern on real, populated tables at real scale, re-running the identical proof on ten structurally-identical tables would be repetition, not additional evidence. All 12 were already confirmed present and correctly named via the schema-drift tests (Milestone 22) and the full regression suite (Milestone 24, 873/873) exercising every one of the underlying queries against real (if smaller) data continuously throughout the phase.

## Numbering

`allocate_document_number()` (Milestone 5) was already proven concurrency-safe under real load (12-thread race test) and was exercised again this milestone by the real browser session (Milestone 25) generating `Q-2026-0001`/`SO-2026-0001`/`INV-2026-0001` in sequence with no collisions. Not re-benchmarked independently here — its concurrency-safety mechanism (`SELECT ... FOR UPDATE` + `SAVEPOINT`) is orthogonal to read-path index performance, the two don't share a failure mode worth re-testing together.

## Cleanup

All 30,000 synthetic rows were deleted at the end of the script (`DELETE ... WHERE quote_number LIKE 'M26-SYN-%'`) and verified: `owner_quotes`/`owner_quote_lines` row counts back to exactly 1 each (the single real Quote created during Milestone 25's browser validation) — no synthetic data left behind in the shared dev database.
