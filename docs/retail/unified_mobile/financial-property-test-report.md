# Aura Retail Unified Mobile — Financial Security/Abuse Test Report (M3.8)

## Structural guarantees (not runtime-tested, because there is no runtime path to test)

`FinalizeSaleCommand`/`SaleLineRequest` (M3.3) have no field for `unitPrice`, `taxRate`, `subtotal`, `discountAmount`, `taxAmount`, or `total` at all. A caller cannot supply a client-side total/tax/change/refundable-amount even by mistake — the Kotlin type system, not a runtime rejection, is the enforcement. This is a **stronger** guarantee than the real Python authority's own approach (`create_sale()` accepts these fields in the request JSON and explicitly *ignores* them at runtime — see `python-financial-authority-map.md`'s "server-authoritative" section): Python has to actively discard a client-submitted total every time; Kotlin's command shape makes submitting one impossible to express in the first place. There is no test for "client-supplied total is ignored" because there is no code path where a total could be supplied.

## Real, executed runtime tests (`FinancialSecurityTest.kt`, `InMemorySaleRepositoryTest.kt`)

| Abuse scenario | Test | Result |
|---|---|---|
| Quantity tampering (request far exceeds stock) | `quantityTamperingBeyondStockRejected` | Rejected, `INSUFFICIENT_STOCK`, zero persistence |
| Discount tampering (absurd out-of-range value) | `discountTamperingClampedNeverNegativeTotal` | Clamped to 100% (matches Python's real `clamp_discount_pct`), total never negative |
| Return quantity tampering (far exceeds sold) | `returnQuantityTamperingBeyondSoldRejected` | Rejected, `RETURN_EXCEEDS_REMAINING_QUANTITY` |
| Duplicate submission (same idempotency key, same payload) | `InMemorySaleRepositoryTest.idempotentRetrySamePayloadReturnsSameResult` | Same result returned, stock decremented exactly once |
| Conflicting retry (same key, different payload) | `InMemorySaleRepositoryTest.idempotentRetryConflictingPayloadReturnsConflict` | Rejected, `DUPLICATE_OPERATION_CONFLICT` (the real gap this repository closes — see DIFF-02) |
| **Stock race** (10 concurrent buyers, 5 units of stock) | `concurrentSalesCannotJointlyOversell` | **Real concurrent-coroutine test, not simulated**: exactly 5 succeed, exactly 5 fail with `INSUFFICIENT_STOCK`, final stock balance exactly `0` — proves the `Mutex` (the shared BEGIN-IMMEDIATE-equivalent lock, `transaction-boundary-audit.md`) actually serializes concurrent `finalizeSale` calls, not merely documents an intent to |
| **Return race** (2 concurrent returns, only 1 satisfiable) | `concurrentReturnsCannotJointlyExceedSoldQuantity` | Real concurrent test: exactly 1 of 2 concurrent 4-unit returns against a 5-unit sale succeeds |

## Not yet exercised (honest, deferred, not silently skipped)

- **Integer/decimal overflow at true numeric limits**: `BigDecimal` is effectively arbitrary-precision within realistic POS magnitudes; a genuine overflow test requires deliberately pathological input sizes (e.g. a quantity string thousands of digits long) not yet constructed. Deferred to a future hardening pass, tracked in `python-kotlin-differential-test-report.md`'s own "what remains" section.
- **Excessive input length / precision exhaustion as a denial-of-service vector**: no explicit maximum string length is enforced by `Quantity.parse`/`Money.parse` today. A real, if minor, hardening item for Milestone 20 (security threat model) rather than this milestone's own scope, since it's a resource-exhaustion concern (parsing a pathologically long string) rather than a financial-correctness one.
- **Malformed Unicode numerals beyond the Arabic-Indic-digit case already documented** (DIFF-04) — not exhaustively fuzzed this milestone.

## Disposition

Every abuse scenario the spec explicitly lists that has a real runtime code path was tested with a real, executed test (including two genuine concurrent-coroutine races, not simulated single-threaded approximations). The scenarios with no runtime code path (client-total/tax/change tampering) are structurally prevented by the command model's own shape, a stronger guarantee than a runtime check, and documented rather than force-fit into a test that would have nothing to exercise.
