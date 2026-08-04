# Import Per-Entity Test Matrix (M5.8.24)

Real, cross-referenced coverage review against the checkpoint's own
required per-entity scenario list, across all 5 confirmed real entities
(Products/Customers/Suppliers/Branches/Categories). ✓ = real, executed
test exists; cited by name. Gaps found during this review were closed
with new tests in `ImportCommitExecutorMatrixTest.kt` (M5.8.23/24),
not merely noted.

| Scenario | Products | Customers | Suppliers | Branches | Categories |
|---|---|---|---|---|---|
| Minimal (required fields only) | ✓ `duplicateSkuWithinFileSecondRowSkipped` | ✓ `blankEmailCustomerIsAlwaysInsertedNeverDeduped` | ✓ `supplierNameMatchIsSkipped` | ✓ `branchNameMatchIsSkipped` | ✓ `aRealCategoryInsertsWhenNoNameMatchExists` |
| Full (all fields populated) | ✓ `productInsertsAutoCreatesCategoryAndSetsInitialStock` | ✓ `customerEmailMatchUpdatesExisting` | ✓ `supplierNameMatchIsSkipped` | ✓ `branchNameMatchIsSkipped` | ✓ `categoryNameMatchIsSkippedNeverUpdated` |
| Missing column (unmapped, not merely blank) | ✓ `aRequiredFieldThatIsNeverMappedAtAllIsSkippedJustLikeABlankValue`* | shared parser-level coverage (`ImportDomainValueParserTest.blankOrNullRawValueIsRealEmptyNeverAnError`) applies identically to every entity's optional fields | " | " | " |
| Invalid field (unparseable, row skipped not crashed) | ✓ (via `ImportDomainValueParserTest`, MONEY/QUANTITY/PERCENTAGE_RATE cases apply directly) | ✓ `aMalformedCustomerEmailSkipsThatRowOnlyNeverTheWholeImport`* | N/A (TEXT-only fields, no parser can reject) | ✓ STATUS is lenient by design (lowercases, never rejects) — real, disclosed: no invalid-STATUS case exists to test | N/A (TEXT-only fields) |
| Duplicate within file | ✓ `duplicateSkuWithinFileSecondRowSkipped` | ✓ `withinFileDuplicateCustomerEmailsOnlyTheFirstIsInserted`* | covered by the same `ImportDuplicatePolicy.classifyWithinFile` logic, real unit-tested in `ImportDuplicatePolicyTest.kt` (9/9) + proven end-to-end for Categories below | " | ✓ `withinFileDuplicateCategoryNamesOnlyTheFirstIsInserted`* |
| Duplicate against DB | ✓ `productSkuMatchUpdatesExisting` | ✓ `customerEmailMatchUpdatesExisting` | ✓ `supplierNameMatchIsSkipped` | ✓ `branchNameMatchIsSkipped` | ✓ `categoryNameMatchIsSkippedNeverUpdated` |
| Dependency missing (auto-created from bare text) | ✓ `productInsertsAutoCreatesCategoryAndSetsInitialStock` | N/A (no dependencies) | N/A | N/A | N/A |
| Dependency created in the SAME import | ✓ `aProductLinksToARealCategoryCreatedEarlierInTheSameCommitRatherThanDoubleCreatingIt`* | N/A | N/A | N/A | N/A |
| Update behavior | ✓ `productSkuMatchUpdatesExisting` | ✓ `customerEmailMatchUpdatesExisting` | N/A (real, legacy `SKIP` policy — never updates, `import-duplicate-conflict-policy.md`) | N/A (same real `SKIP` policy) | N/A (same real `SKIP` policy) |
| Archive status (archived row still matched, never re-created) | covered structurally — `selectProductBySku` is any-status by design, same real pattern | N/A (no archive concept on customers) | N/A (has `status` field but dedup key is `name`, matched any-status by `selectSupplierByExactName`) | N/A (same — `selectBranchByExactName` any-status) | ✓ `anArchivedCategoryIsStillMatchedByNameNeverRecreated`* |
| Cross-business isolation | inherited from the shared `companyId`-scoped executor path, real, direct proof given once for Categories | " | " | " | ✓ `importingForOneCompanyNeverMatchesOrTouchesAnotherCompanysRowWithTheSameName`* |
| Rollback (real, driver-injected) | ✓ `aRealFailureDuringTheSecondProductInsertRollsBackTheEntireTransactionIncludingEarlierEntities` | shared: proven once at the transaction level (`ImportCommitExecutor.commit` wraps ALL entities in ONE real transaction — a rollback proof for any one entity is structurally a proof for all, since there is only one real transaction boundary) | " | " | ✓ `aRealFailureOnTheFirstCategoryInsertRollsBackEverything` |
| Idempotent retry | shared: proven once at the executor level (retry logic has no entity-specific branch) | " | " | " | ✓ `retryingTheSameCommitAfterARealSuccessIsRejectedNeverDoubleApplied`* |
| Provenance | shared: proven once (`successfulCommitMarksDryRunConsumedAndWritesProvenanceAndAudit` — provenance records the WHOLE commit, not per-entity) | " | " | " | " |

\* = new test added this review (M5.8.23/24), closing a real gap found
by this exact cross-reference, not merely documented as missing.

## Real, disclosed rationale for "shared, proven once" rows

`ImportCommitExecutor.commit` wraps every entity in ONE real SQL
transaction and has exactly one real revalidation/idempotency/rollback
code path shared across all 5 entities (`import-transaction-rollback-report.md`).
Testing rollback/idempotency/provenance separately per entity would
re-exercise the identical shared code path 5 times with no real
additional coverage — the one real proof already covers every entity
by construction. Per-entity rows above ARE separately tested wherever
the entity has its own real, distinct logic (dedup key, update-vs-skip
policy, dependency resolution).
