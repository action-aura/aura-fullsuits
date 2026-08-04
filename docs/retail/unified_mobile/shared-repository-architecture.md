# Aura Retail Unified Mobile — Shared Repository Architecture (M5.1)

## Dependency direction (binding on all future milestones)

Shared UI -> Shared ViewModel/presentation state -> Shared use case -> Shared repository interface -> Shared repository implementation -> SQLDelight query authority.

M5.1 delivers the bottom three layers for five of the seventeen repositories named in the governing spec, and declares the rest as documented interface boundaries. No UI or ViewModel code exists yet (Milestone 6 scope) — nothing in M5.1 depends on anything above the repository-interface layer.

## What is real (SQLDelight-backed, tested) in M5.1

| Repository | File | Backing table(s) |
|---|---|---|
| `CategoryRepository` | `data/sqldelight/SqlDelightCategoryRepository.kt` | `categories` |
| `BranchRepository` | `data/sqldelight/SqlDelightBranchRepository.kt` | `branches` |
| `ProductRepository` | `data/sqldelight/SqlDelightProductRepository.kt` | `products` |
| `InventoryRepository` | `data/sqldelight/SqlDelightInventoryRepository.kt` | `inventory_balances`, `inventory_movements` |
| `SettingsRepository` | `data/sqldelight/SqlDelightSettingsRepository.kt` | `retail_settings`, `doc_sequences` |

These five were chosen because M5.3 (Category domain), M5.4 (Branch domain), and M5.5 (Product/Inventory) — the very next milestones — depend on them directly. `SaleRepository`/`ReturnRepository` already exist as interfaces from Milestone 3, currently backed only by the in-memory, Mutex-locked test double (`InMemorySaleRepository`, real and fully tested for M3's scope). A SQLite-backed implementation matching that same atomic-transaction guarantee is real, separate, near-term work, not yet done — not claimed done here.

## Design decisions carried over from M3/M4

- **Typed boundary, no raw rows**: every repository method returns/accepts `data.model.*` domain types (`Category`, `Branch`, `Product`) or the M3 financial types (`Money`, `Quantity`, `PercentageRate`) — never a generated SQLDelight row class, `Double`, or `Float`, matching database-schema-contract.md rule 1 and the M5.1 spec's own "no raw SQLDelight rows/cursors/Float/Double exposed to UI" requirement.
- **`RepositoryError`/`DomainResult`**: a new, separate sealed-class pair from `financial.FinancialError`/`FinancialResult` (M3), because the general repository layer's error space (not-found, insufficient-stock, last-active-protected) is distinct from the financial core's sale/return-specific error space (financial-error-code-map.md). Same discipline: stable `code` string, no prose, localization deferred to a later UI layer.
- **Stored-value parsing throws, not returns an error**: `parseStoredMoney`/`parseStoredQuantity` (`data/StoredValueParsing.kt`) call `error(...)` on a parse failure, not `RepositoryError`. A parse failure here means this codebase's own previously-written data is corrupt — a different class of problem than a normal validation failure, consistent with how a corrupt local SQLite file is treated everywhere else in this codebase.
- **Two real atomicity-sensitive invariants live in the repository layer, not the use-case layer above it**: `BranchRepository.setActive` (a company must never reach zero active branches) and `InventoryRepository.adjustStock` (a stock decrease must never go negative). Both need a lock-fresh read taken atomically with the write — exactly `SaleRepository`'s own justification (transaction-boundary-audit.md) for why oversell protection lives inside the repository, not split across use-case-orchestrated read/write calls. Every other business rule (duplicate-name detection, Arabic normalization, current-branch selection) is deliberately left to the M5.2/M5.3/M5.4 use-case layer.
- **`Quantity` cannot represent a negative value** (M3's own invariant, `Quantity.parse`/`zeroOrMore` both reject negative input) — `InventoryRepository.adjustStock` therefore has no "allow negative result" option; a decrease that would go below zero always fails with `RepositoryError.InsufficientStock`. This was caught during design, before writing the implementation, by re-reading `Quantity.kt`'s own real constraints rather than assuming a signed-delta API would work.
- **`PercentageRate.trusted(String)`** was added to `financial/PercentageRate.kt` (additive, M3 file) because `tax_rate` is a TEXT column as of M4 (database-schema-contract.md rule 1) — the pre-existing `trusted(Double)` factory could not represent a stored value without a lossy `Double` round trip.

## What is boundary-only (declared, not implemented) in M5.1

`data/RepositoryBoundaries.kt` declares eleven interfaces with no backing implementation: `BusinessRepository`, `UserRepository`, `SessionRepository`, `CartRepository`, `DashboardRepository`, `ReportingRepository`, `ImportRepository`, `BackupRepository`, `LicensingRepository`, `AppHealthRepository`, `VersionRepository`. Each cites, in its own KDoc, the real milestone that implements it (M5.6 for Dashboard/Reporting, M5.8-M5.11 for Import, M16 for Backup, M9/M10 for Licensing, M20+ for AppHealth, M26 for Version). The six whose real shape is not yet designed (Dashboard/Reporting/Import/Backup/Licensing/AppHealth/Version) are empty marker interfaces, not fabricated placeholder methods — inventing a method signature now, before the owning milestone has made its real design decisions, would only be code written to be deleted later.

## Real, executed evidence

`./gradlew :shared:testDebugUnitTest`: 92/92 tests passing (78 M5.0 baseline + 14 new: `CategoryBranchRepositoryTest` (6), `ProductInventoryRepositoryTest` (6), `SettingsRepositoryTest` (2)). `./gradlew :androidApp:assembleDebug`: BUILD SUCCESSFUL, unified debug APK still builds with the new shared-module code linked in.
