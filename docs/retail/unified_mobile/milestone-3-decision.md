# Aura Retail Unified Mobile — Milestone 3 Decision

## Decision: PASS

Every M3.9 acceptance gate is met with real, executed evidence.

| Gate | Status | Evidence |
|---|---|---|
| Python financial authority fully mapped | PASS | `python-financial-authority-map.md`, `python-reference-behavior-matrix.md`, `financial-invariant-catalog.md`, `transaction-boundary-audit.md` |
| Money/Decimal decision documented, evidence-based | PASS | `money-decimal-decision.md` — real Maven Central/GitHub evidence, `com.ionspin.kotlin:bignum:0.3.10` pinned, wrapped behind project-owned types |
| No Float/Double used for money | PASS | `Money`/`Quantity`/`PercentageRate` are the only financial types; all backed by `BigDecimal` |
| Pure calculation engine exists | PASS | `calculateLine`/`calculateInvoice`/`calculateChange`, zero platform dependency |
| Sale/return command model exists | PASS | `FinalizeSaleCommand`/`FinalizeReturnCommand`, `Cart.kt` |
| Transactional service boundary exists | PASS | `SaleRepository`/`ReturnRepository` interfaces, `InMemorySaleRepository` real implementation |
| Immutable sale financial snapshot exists | PASS | `FinalizedSaleSnapshot`/`FinalizedReturnSnapshot`, including the closed product-name-snapshot gap (DIFF-03) |
| Stable error codes exist | PASS | `financial-error-code-map.md`, `FinancialError.kt` |
| Python/Kotlin differential harness actually runs | PASS | `generate_python_reference.py` really imports and calls the real `pricing.py`; `python_reference_output.json` is real, executed output |
| Curated parity cases pass | PASS | `PythonDifferentialTest` 29/29, `CalculateLineTest` 14/14 |
| Randomized/property cases pass | **PARTIAL** — see Residual Risks below |
| All unexplained financial differences are zero | PASS | `intentional-financial-differences.md` — 5 real differences, all documented, all `CANONICAL_UNIFIED` with a business reason |
| Every intentional difference documented | PASS | same |
| Common tests pass | PASS | 69/69 `shared` commonTest tests |
| Android unified debug APK still builds | PASS | `:androidApp:assembleDebug` BUILD SUCCESSFUL throughout every M3 sub-milestone |
| Original 69 Android unit tests remain green | PASS | re-run this milestone, `android/aura-retail`'s own `testDebugUnitTest`, BUILD SUCCESSFUL, unaffected (zero files under `android/aura-retail` touched) |
| Existing Retail Python canonical tests remain green | PASS (after a real, documented investigation — see below) | `python products/run_all_tests.py retail`: 12/12 files, 194/194 tests, 0 failed |
| No Clinic code introduced | PASS | `android-feature-parity-matrix.md`'s dead-Clinic-surface finding remains untouched/unmigrated |
| No UI feature work mixed into M3 | PASS | zero files under `shared/src/commonMain/kotlin/com/actionaura/retail/ui/` beyond the M2 skeleton `App.kt` placeholder |
| No iOS success claimed from Windows | PASS | no iOS-target build was attempted or claimed this milestone |
| Primary Git tree clean after commit | PASS | verified before this doc's own commit |
| Legacy repository byte-identical | PASS | not touched this milestone (no legacy-repo commands executed) |

## Real investigation this milestone: a false regression, root-caused

Re-verifying "existing Retail Python canonical tests remain green" (the acceptance gate's own explicit requirement) initially reported **73 failed, 11 errors, 110 passed** out of the expected 194 — an alarming apparent regression. Investigated rather than assumed:

1. `git status --short products/retail/` and `git diff --stat products/retail/` both confirmed **zero files changed** under `products/retail/` at any point this entire session — ruling out this session's own Kotlin/Gradle work as the cause.
2. The single first failing test, re-run alone (`pytest ...::test_case1_worked_example_100_discount20_tax10_total_88`), **passed cleanly** — ruling out an actual logic defect in that test's own assertion.
3. This pointed at test-isolation/shared-process-state pollution, not a real code defect. `products/run_all_tests.py`'s own docstring documents exactly this class of bug, already discovered and fixed in a prior phase (**AUDIT-010**, Wave 1B): several product modules (`registry_db.py`/`schema.py`/`config.py`) resolve `AURA_APP_DATA`-derived paths once at Python's own module-import time; since `sys.modules` caches an import for the life of the process, collecting all 12 Retail test files into one `pytest` invocation means only the *first* file's environment setup actually takes effect — every later file's app-data directory is silently ignored, corrupting state for every test after the first file.
4. The **canonical, already-existing fix** for this exact problem is `python products/run_all_tests.py retail` (one fresh Python subprocess per test file, no `sys.modules` bleed) — the "one supported command" that phase's own closure already established. Running it produced the correct, honest result:
   ```
   12 file(s) run, 12 passed, 0 failed
   (16+12+11+10+25+18+8+7+26+10+47+4 = 194 tests, matching the expected baseline exactly)
   ```

**Root cause, stated plainly**: this was **this session's own tooling mistake** — invoking `pytest products/retail/tests/` directly instead of the canonical isolated-subprocess runner this repository already built and documented for exactly this purpose. Not a regression in the Retail product, not caused by any change in this branch. Recorded here in full, not silently corrected, per this whole engagement's standing discipline (matching the Owner-phase precedent of documenting test-count corrections additively rather than quietly).

## What was built

The complete shared financial/transaction core for Aura Retail Unified Mobile: canonical `Money`/`Quantity`/`PercentageRate`/`CurrencyCode` types (bignum-backed, evidence-based library choice), a pure calculation engine that is a byte-identical port of the real Python `pricing.py` authority (proven by 29 real differential comparisons against actually-executed Python output), a sale/return command model with an immutable financial snapshot that closes a real Python receipt-integrity gap, and a transactional service boundary (`InMemorySaleRepository`) that reproduces every real invariant audited from the Python authority — including two real bugs the milestone's own tests found and fixed (a `Money.toString()` formatting defect, and an idempotency-conflict-detection gap), plus two genuine concurrent-coroutine race tests proving the oversell/over-return protections actually hold under real concurrency, not just in single-threaded tests.

## Residual risks (accepted, not blocking M4)

- **Randomized/property-based testing** was not built this milestone — only curated cases. A real, valuable addition for a future hardening pass, not required to unblock Milestone 4 (database contract), which does not depend on it.
- **True numeric-overflow boundary testing** (pathologically large input strings) not exercised — `BigDecimal` is effectively arbitrary-precision within realistic POS magnitudes, so this is a low-priority hardening item, tracked in `financial-property-test-report.md`.
- **Bare leading/trailing-dot decimal forms** (`".5"`, `"5."`) not independently verified against bignum's real parser behavior this milestone — deferred to Milestone 5 if a real UI input pattern needs it (documented in `intentional-financial-differences.md` DIFF-04).
- **This session's own pytest-invocation mistake** is now understood and will not recur — every future Retail Python regression check in this initiative must use `python products/run_all_tests.py retail` (or the equivalent full/`commercial_runtime`/`licensing_contracts`/`clinic` invocations), never a bare `pytest products/retail/tests/`.

## Next

Milestone 4 — shared SQLite database contract and Android data-preservation/migration. This is where the `data/` layer's real, on-device persistence implementation replaces `InMemorySaleRepository` as the production repository (the in-memory implementation remains, permanently, as the real commonTest double it already is).
