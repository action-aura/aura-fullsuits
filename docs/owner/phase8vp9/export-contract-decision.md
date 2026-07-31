# Phase 8V-P9 — Export Contract Decision

## Decision: Branch B for both products — Export is not a Phase 8 requirement. Not implemented.

## Canonical evidence

See `canonical-discount-and-export-contract.md` Q3/Q4. Summary: no export route or handler exists
anywhere in either backend, on either platform (`products/retail/backend/api/import_api.py`'s own
source comment: "No export endpoint exists in the source implementation ... not a regression");
confirmed by the Wave 1C `docs/audit/18-backup-and-recovery-audit.md`, the Windows-vs-Android parity
audit, and the migration parity matrix's own `test_no_export_endpoint_exists_yet` marker test. A
`*.data.export` capability-code string exists only inside each backend's `READ_ONLY_ALLOWLIST` (a
licensing-restriction concept) with no implementing route -- a placeholder, not a feature.

## Requirements satisfied for Branch B

1. Canonical evidence cited above.
2. Distinction made explicit: Backup + Restore (the `.aurabak.zip` workflow) is the real, implemented,
   already-physically-proven (Phase 8V-P7, both products) recovery contract. "Export" as a future
   data-portability feature (CSV/XLSX/structured download) is a distinct, unbuilt concept.
3. No release-gate documentation overclaims Export as available -- `docs/release/wave1c/
   release-gate-scorecard.md:77` already correctly flags its absence as a compliance-readiness gap, not
   a Phase 8 licensing blocker.
4. The incorrectly-introduced Export release gate from prior sessions' own governing specs is removed
   as a blocking item this session.
5. Backup/Restore preserved as mandatory and already PASS (Phase 8V-P7 evidence retained unchanged).
6. Export belongs on the product backlog as a future data-portability/compliance feature, not
   Phase 8 licensing-closure scope.
7. Not described as available anywhere in this session's own documentation.
8. Backup is never called "Export" or "user-readable Export" in this session's writing.

## What this means for the final Phase 8 decision

Per this session's own governing spec: "Export PASS where required, or canonically documented as out
of scope." The second condition is met, with real, multi-source evidence, for both Clinic and Retail.
Backup/Restore's own real PASS from Phase 8V-P7 stands, unchanged, as the actual recovery-contract
evidence this phase needs.
