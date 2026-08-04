# Aura Retail Unified Mobile — Milestone 5.5 Decision

## Verdict: **CONDITIONAL PASS**

Every gate that has a real, buildable, testable target in this milestone's actual scope passes with real, executed evidence. Two gates are structurally out of scope for real, cited reasons (not oversights, not silently skipped) and are called out explicitly below rather than folded into a false unconditional PASS.

## Gate-by-gate

| Gate | Status | Evidence |
|---|---|---|
| Actual Product/Inventory authorities audited | PASS | `product-inventory-authority-audit.md`, cited file:line throughout |
| Shared Product model complete | PASS | `product-domain-contract.md` |
| No Float/Double for money or Quantity | PASS | Verified throughout — `Money`/`Quantity`/`PercentageRate` only |
| Product lifecycle works | PASS | `product-lifecycle-report.md`, 22/22 |
| Archive/reactivate works | PASS | Same |
| Historical snapshots remain immutable | PASS | Structural (no write path exists into `sale_items`/`return_items`) |
| Barcode rules deterministic | PASS | `barcode-and-sku-contract.md` |
| Leading-zero barcode preserved | PASS | `createPreservesLeadingZeroBarcodeExactly` |
| Duplicate barcode race prevented | PASS | Real parallel-thread proof, `ProductInventoryConcurrencyTest` |
| SKU rules deterministic | PASS | `barcode-and-sku-contract.md` |
| Category assignment rules work | PASS | `product-category-integration.md` |
| Archived-category behavior documented and tested | PASS | Same |
| Branch inventory is canonical | PASS | `branch-inventory-contract.md` |
| One inventory row per (business, branch, product) | PASS | Real `UNIQUE` constraint, unchanged since M4 |
| Initial inventory uses a canonical command | PASS | `insertWithInitialStock`, real atomicity proven |
| Stock mutation is transactional | PASS | `inventory-mutation-contract.md` |
| Tracked stock cannot go negative | PASS | `InsufficientStock` guard, structural + tested |
| Sale decrement contract integrated | PASS (boundary only, by design) | `sale-return-inventory-integration.md` — explicit spec instruction was to build the boundary, not a full `SaleRepository`; the boundary is real and tested |
| Return restoration contract integrated | PASS (boundary only, by design) | Same |
| Current-branch behavior is safe | PASS | `branch-domain-contract.md`'s M5.5.11 addendum, `CartTest` |
| Active cart cannot switch inventory context | PASS | `Cart.branchId` immutable across all pure transformations |
| Low-stock authority documented | PASS | `low-stock-definition.md` |
| Authorization enforced at use-case level | **DEFERRED, documented** | `product-inventory-authorization.md` — no Kotlin RBAC exists anywhere in this codebase yet (Milestones 7-10 not reached); building one now would be the "second RBAC authority" the spec explicitly forbids |
| Idempotency works | PASS | `inventory-mutation-contract.md`, `CreateProductWithInitialStockUseCaseTest` |
| Final-unit concurrency proven | PASS | Real parallel-thread proof, exactly 1 winner, stock never negative |
| Backup/restore remains compatible | PASS (with a real regression found and fixed) | `product-inventory-backup-restore-report.md` |
| Android legacy preservation remains green | PASS | `CatalogImporterTest`, re-confirmed against the new schema |
| Real SQLite query plans reviewed | PASS (with a real bug found and fixed) | `product-inventory-query-plan-report.md` |
| All new common tests pass | PASS | 169/169 |
| 112-test M5.4 baseline remains green | PASS | All 112 baseline tests still present and passing |
| Retail Python remains green | PASS | 194/194, canonical `run_all_tests.py` runner, re-run after the schema change |
| Android debug APK builds | PASS | Confirmed after every real commit this milestone |
| No Clinic code introduced | PASS | Scope unchanged, verified via `git status` scope review every commit |
| No iOS success claimed from Windows | PASS | None claimed |
| `aura-fullsuits-phase9r` worktree untouched | PASS | Never referenced or touched this milestone |
| Legacy repository byte-identical | PASS | Never touched (`AuraEnterprise` repo not referenced) |
| Branch clean after commit | PASS | Confirmed via `git status --short` after every commit |

## Why CONDITIONAL, not unconditional PASS

One gate is a real, structural deferral: **use-case-level authorization**. This is not a gap in this milestone's own work — it is a real dependency on Milestones 7-10 (Owner licensing authority, multi-device activation, session model) that have not been reached yet, and the spec's own instruction ("reuse the current... model... do not create a second RBAC authority") makes building a placeholder now actively wrong, not merely premature. The real integration point is identified and ready (`product-inventory-authorization.md`): every M5.5 use case is already structured as a plain constructor-injected class, so a future permission-check dependency plugs in the same way `UnicodeTextNormalizer` or any repository already does, with no redesign.

## Real bugs found and fixed during this milestone (not merely "no bugs found")

1. `categories_company_name` unconditional unique index blocked the intended M5.3 archive-then-reuse UX (found before M5.5, fixed then — cited here as the established pattern this milestone continued).
2. Legacy import threw and rolled back entirely on a source database with duplicate barcodes/SKUs — a real, audited-possible legacy state M5.5.2's own new constraint made newly fatal. Fixed with a deterministic dedup pass in `CatalogImporter`.
3. `JdbcSqliteDriver`'s single shared JDBC connection is not safe for genuinely concurrent multi-threaded transactions — found by actually running parallel-thread tests (real `SQLITE_ERROR` exceptions and real silent lost updates), fixed with a per-repository-instance `Mutex`.
4. The barcode-lookup query never actually used the barcode index at all — SQLite's planner cannot use a partial index unless it can prove the query satisfies the partial condition, discovered only by inspecting real `EXPLAIN QUERY PLAN` output at 10K-product scale, fixed with a dedicated non-partial lookup index.

Each was found by the milestone's own real tests or real diagnostic investigation, not assumed away — consistent with this entire initiative's build-discipline.

## Proceed to Milestone 5.6

Per the governing spec: M5.6 (shared reporting authority) may now begin. No shared Compose UI work begins in M5 (unchanged constraint, still honored).
