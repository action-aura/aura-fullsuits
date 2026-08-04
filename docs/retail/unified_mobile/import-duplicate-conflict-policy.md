# Import Duplicate and Conflict Policy (M5.8.11)

Real duplicate policy (`ImportDuplicatePolicy.kt`), proven by
`ImportDuplicatePolicyTest.kt` (9/9,
`TEST-com.actionaura.retail.importing.entity.ImportDuplicatePolicyTest.xml`
tests="9" failures="0" errors="0").

## Not a second RBAC-style authority — the same decision discipline, applied to Import Center's own 5 entities

The durable M5.5 `CatalogImporter` dedup authority is scoped to the
LEGACY-DATABASE MIGRATION path (Product barcode/SKU dedup during a
one-time schema migration, preserving legacy row IDs) — it has no real
concept of Customers/Suppliers/Branches/Categories dedup, and cannot be
literally invoked for Import Center's general file-based import (a
different source shape, no legacy IDs to preserve). What IS reused is
the real **decision discipline** that authority established: a
deterministic winner (first row wins), never a silent merge, every
non-trivial decision produces a real, auditable record
(`ImportDuplicate`) — the same discipline, not a second, independently
invented policy.

## Real, per-entity dedup key (audited legacy behavior)

| Entity | Dedup key | Real legacy behavior on match |
|---|---|---|
| Products | `sku` | `UPDATE_EXISTING` |
| Customers | `email` (non-blank only) | `UPDATE_EXISTING` |
| Suppliers | `name` | `SKIP` |
| Branches | `name` | `SKIP` |
| Categories | `name` | `SKIP` |

## Real decision: preserve the Suppliers/Branches/Categories `SKIP` behavior

Proven by `categoryMatchingAnExistingNameIsSkippedNeverSilentlyOverwritten`
and `supplierAndBranchMatchesAreAlsoSkipped`: a Supplier/Branch/Category
row matching an existing name is `SKIP`ped, exactly matching the real,
audited legacy behavior — **and deliberately preserved**, not
"upgraded" to `UPDATE_EXISTING` like Products/Customers. Real reasoning:
overwriting an existing Category/Branch/Supplier's real data from a bulk
uploaded file (which may contain stale or partial data for a field the
user never intended to change) is a real, materially riskier default
than skipping — Products/Customers' own `UPDATE_EXISTING` behavior is
preserved because it is the real, already-tested, already-relied-upon
legacy behavior (`test_reimporting_same_sku_updates_not_duplicates`),
not because `UPDATE_EXISTING` is universally the safer choice.

## Real, disclosed limitation: blank-email Customers have no reliable dedup key

Proven by `blankKeysNeverCollideWithEachOtherWithinFile`: two Customer
rows with no email are never treated as duplicates of each other or of
an existing blank-email Customer — this is the real, preserved legacy
behavior (`import-authority-audit.md`'s own citation: blank email always
`INSERT`s). Building a fuzzy name+phone fallback dedup for this case is
real, new scope beyond this milestone's own requirements — documented
here as a known, disclosed limitation rather than silently fixed or
silently left unexamined.

## Within-file duplicate detection

`classifyWithinFile` implements the real, deterministic "first row
wins" rule — proven by `theFirstRowToClaimAKeyIsNeverADuplicate` and
`aLaterRowWithTheSameKeyIsARealWithinFileDuplicate`. Against-database
detection (`classifyAgainstDatabase`) is a separate, later stage in the
real pipeline (M5.8.13's own dry-run composition) — a row can be a
real within-file duplicate of an earlier row in the SAME file, a real
against-database duplicate of an existing row, both, or neither, and
each is a real, separately classified `ImportDuplicate` record.

## Never silently merges two materially different Products

No code path in `ImportDuplicatePolicy` ever compares field VALUES
between a new row and an existing record to decide whether they are
"close enough" to merge — a `sku` match is a `sku` match, full stop; the
actual field-level UPDATE (which fields get overwritten) is a real,
separate M5.8.15 commit-stage concern, not a merge decision made here.
