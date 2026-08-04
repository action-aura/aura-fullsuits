# Aura Retail Unified Mobile — Shared Financial Core Design (Milestone 3 summary)

Consolidated design reference for `mobile/aura-retail-unified/shared/src/commonMain/kotlin/com/actionaura/retail/{financial,usecases,data}/`. Each sub-decision has its own detailed doc (linked below); this is the map, not a duplicate of the detail.

## Layers

```
financial/   -- pure, platform-free domain types + calculation formulas + immutable snapshots
                Money, Quantity, PercentageRate, CurrencyCode, TaxMode, FinancialError,
                FinancialResult, calculateLine/calculateInvoice/calculateChange, Cart,
                FinalizedSaleSnapshot/FinalizedReturnSnapshot
usecases/    -- commands (caller-facing intent, never trusted financial values)
                FinalizeSaleCommand, FinalizeReturnCommand, SaleLineRequest, ReturnLineRequest
data/        -- transactional service boundary (repository interfaces + implementations)
                SaleRepository/ReturnRepository interfaces, InMemorySaleRepository
                (real implementation used by every test this milestone; the real SQLite-
                backed implementation is Milestone 4 scope)
```

Dependency direction is one-way: `usecases`/`data` depend on `financial`; `financial` depends on nothing else in this codebase (no Android/iOS/SQL/UI/network/localization). `ui`/`repositories` (Milestone 5+) will depend on `usecases`, never call into `data` or `financial` directly for anything beyond reading already-resolved values.

## Key decisions (each with its own doc)

| Decision | Doc |
|---|---|
| Money/Quantity representation: `com.ionspin.kotlin:bignum` wrapped behind project-owned types | `money-decimal-decision.md` |
| What the real Python authority actually does, source-cited | `python-financial-authority-map.md`, `python-reference-behavior-matrix.md` |
| Every invariant the shared core must enforce, LEGACY_PARITY vs CANONICAL_UNIFIED | `financial-invariant-catalog.md` |
| Stable error codes (new in Kotlin -- Python has none) | `financial-error-code-map.md` |
| Exact transaction/locking semantics the repository layer must reproduce | `transaction-boundary-audit.md` |
| Real differential proof against the Python reference | `python-kotlin-differential-test-report.md` |
| Every intentional behavior difference, with business reasoning | `intentional-financial-differences.md` |
| Receipt data-model completeness proof | `receipt-financial-parity-report.md` |
| Security/abuse/concurrency test results | `financial-property-test-report.md` |

## Why a single atomic repository method, not separate read/write calls

`SaleRepository.finalizeSale(command)` and `ReturnRepository.finalizeReturn(command)` are each one method, not a set of composable read/validate/write steps the use-case layer orchestrates. This mirrors the real Python authority's own shape exactly (`create_sale()`/`create_return()` are themselves single functions that acquire a lock, then validate against a lock-fresh read, then persist, all inside one function) — splitting it into separate calls from outside the repository would reintroduce the exact oversell/over-return race the real backend was hardened against (AUDIT-009), because the validation would then run against a read that could go stale before the write. This is not an accident of Kotlin idiom; it is a direct, deliberate structural mirror of a real, audited concurrency guarantee.

## What "shared" actually means, proven not asserted

Every one of the 69 `shared/src/commonTest` tests (as of Milestone 3's close) runs identically against whichever Kotlin target compiles it — today that's the Android JVM target (the only one this Windows host can execute), and once a Mac is available, the exact same test source runs against the iOS targets with zero code changes. There is no Android-specific or iOS-specific branch anywhere in `financial/`, `usecases/`, or `data/`.

## What Milestone 3 deliberately did not do

- No UI (`ui/`) — explicitly out of scope per the governing spec ("Do not begin shared UI migration before the financial core and its differential parity suite are stable").
- No real SQLite persistence — `InMemorySaleRepository` is a real, complete implementation of the interface (not throwaway scaffolding), but the on-device durable storage is Milestone 4.
- No licensing/branch/category/report logic — those are later milestones' scope (M4-M13 range, per the Product Owner Override's expanded complete-product scope).
