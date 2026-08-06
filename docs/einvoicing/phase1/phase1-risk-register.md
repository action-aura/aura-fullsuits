# Phase 1 risk register

| # | Risk | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|---|
| 1 | Feature changes behavior for existing pilot customers | Low | High | Default OFF; regression suites pin exact disabled-path behavior (response shapes, table schemas, thread count); additive-only migration with automatic pre-migration backup | Low — proven by test, re-run after every wiring change in this wave |
| 2 | Double-submission to the tax authority | Low | High (compliance/financial) | Four independent idempotency layers, tested including a real crash-simulation | Low |
| 3 | Checkout/invoice creation slows down or fails because of this feature | Low | High (revenue-blocking) | Async outbox, best-effort enqueue wrapped in broad try/except, proven under artificial 5s provider latency | Low |
| 4 | Credentials leak (disk, logs, HTTP responses) | Low | High | Encrypted at rest, redacted `repr`/`str`, forbidden-marker audit guard, no raw exceptions to clients | Low |
| 5 | Real ISTD field mapping guessed incorrectly, producing non-compliant filings | N/A in Phase 1 | High | `DirectISTDProvider` deliberately unimplemented; zero live traffic possible | None (deferred to Phase 2 entirely) |
| 6 | Reconciliation sweep misses or double-processes sales/invoices due to timestamp format mismatches | Was realized during this wave | Medium | Found and fixed for both products before merge (`outbox-state-machine.md`); covered by `test_reconciliation_sweep_picks_up_a_lost_enqueue` / `test_enabled_at_prevents_backfilling_pre_enablement_sales` in both products' integration suites | Low |
| 7 | Android app breaks because the backend now imports new modules | Low | Medium | `segno` import is function-local (lazy) in `qr.py`, not module-level; `cryptography` already a Chaquopy dependency; Android build/UI work itself deferred (see below) | Low — verified by reasoning, not by an actual Android build in this session |
| 8 | PyInstaller packaging silently missing a hidden import (repo has a prior real incident of this class — `trust_anchor.json`) | Was a real risk | High | All `commercial_runtime.einvoicing.*` submodules + `segno` added explicitly to both `.spec` files; **verified with a real PyInstaller build of Retail**, frozen exe launched, `/api/health` and `/api/einvoicing/status` both responded correctly | Low |
| 9 | Coverage regresses below the 80% bar | Low | Low | Measured directly: 97% on `commercial_runtime/einvoicing/` (169 tests) | Low |

See `phase1-residual-risk-register.md` for what remains open after this
wave closes.
