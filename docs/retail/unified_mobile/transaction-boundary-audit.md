# Aura Retail Unified Mobile — Transaction Boundary Audit (M3.0)

Real audit of exactly where the Python authority opens/closes a database transaction, what it locks, and what rolls back on failure — the contract the shared Kotlin `SaleRepository`/`ReturnRepository` interfaces (M3.4) must reproduce, backed initially by an in-memory test double and later (Milestone 4) by the real on-device SQLite driver.

## Sale finalization (`create_sale`, `retail_api.py:614-818`)

```
conn.execute("BEGIN IMMEDIATE")          # write lock acquired up front
  for each line:
    resolve product (read)
    validate quantity/stock            # any failure -> conn.rollback(); conn.close(); return 400
  compute subtotal/discount/tax/total (pure, in Python process, not SQL)
  resolve payment/change/credit rules  # any failure -> conn.rollback(); conn.close(); return 400/404
  INSERT sales
  for each line:
    INSERT sale_items
    INSERT inventory_movements
    UPDATE inventory_balances            # decrement
  UPDATE customers (spend/loyalty)       # if customer attached
  _record_payment(...)                   # if paid > 0
  _adjust_credit(...)                    # if balance_due > 0 and customer attached
conn.commit()
```

**Real, exact contract**:
- `BEGIN IMMEDIATE` (not the SQLite default deferred transaction) — acquires the write lock at the *start* of the transaction, before any read that later informs a write decision. This is what makes the oversell-race mitigation real: a second concurrent `create_sale` call blocks on `BEGIN IMMEDIATE` until the first transaction fully commits or rolls back, so it always re-reads the *post*-first-sale stock balance, never a stale pre-sale one.
- Every validation failure inside the loop rolls back and closes the connection immediately — no line is ever partially persisted.
- The `except Exception as e: conn.rollback()` blanket handler at the bottom (`retail_api.py:816-818`) means *any* unexpected failure anywhere in the whole function (not just the lines explicitly checked) rolls back the entire transaction — a real, load-bearing safety net, not just the explicit checks.
- `_record_payment`/`_adjust_credit` run *inside* the same transaction, before `conn.commit()` — a failure in either would roll back the sale itself too (real, not assumed — confirmed by their position before `conn.commit()` in the function body).

**Shared Kotlin contract** (M3.4): `SaleRepository.finalizeSale(command): Result<FinalizedSale>` must be a single atomic operation from the caller's perspective — the in-memory test double enforces this by only committing its mutable state at the very end of a successful run, discarding all intermediate mutations on any validation failure (mirroring SQLite's real rollback semantics without needing a real database for M3.4's tests).

## Return finalization (`create_return`, `retail_api.py:875-1023`)

```
sale = SELECT ... WHERE id=? AND company_id=?     # read BEFORE the lock (real, minor ordering note below)
if not sale: return 404
conn.execute("BEGIN IMMEDIATE")
  for each line:
    validate quantity
    resolve original sale_items row (read)
    compute already_returned (read, aggregate)
    validate remaining <= sold - already_returned
    compute refund via pricing.calculate_line       # pure, from historical sale-line values
  INSERT returns
  for each line:
    INSERT return_items
    UPDATE inventory_balances                        # increment (restore stock)
    INSERT inventory_movements
  _audit(...)
conn.commit()
```

**Real, exact contract**:
- The `sale_id` existence check happens *before* `BEGIN IMMEDIATE` is issued — a real, minor ordering difference from `create_sale` (which takes the lock before any per-line read). Not a defect: the sale-existence check doesn't depend on a concurrently-mutable value the way stock/remaining-quantity do, so there's no race to protect against here. The Kotlin port should preserve this ordering distinction (sale-existence check outside the lock, remaining-quantity check inside it) rather than uniformly locking everything, since uniform locking would be a behavior change (slightly different failure-mode timing under concurrency), not a pure refactor.
- `BEGIN IMMEDIATE` again taken before the "remaining returnable" read — the real concurrency protection for the cumulative-return-limit invariant (two returns racing against the same sale+product cannot both read the same pre-return "remaining" snapshot).
- The blanket `except Exception as e: conn.rollback(); conn.close()` (`retail_api.py:1021-1022`) is the same full-function safety net as the sale path.

## What is explicitly NOT inside either transaction

- The idempotency-key lookup for both sale and return happens *before* `BEGIN IMMEDIATE` — a read-only check against already-committed data, correctly outside the transaction (no lock needed to read a value that, if present, means "return immediately without mutating anything further").
- `_emit('SaleCompleted', ...)` (`retail_api.py:789`) fires *after* `conn.commit()` — an event/webhook-style side effect, deliberately outside the transaction (a subscriber failure must never roll back a committed sale). The shared Kotlin core's equivalent (if any event/notification concept is added later) must preserve this same after-commit-only timing.

## Shared Kotlin transactional contract (M3.4 target)

```kotlin
interface SaleRepository {
    suspend fun finalizeSale(command: FinalizeSaleCommand): FinalizeSaleResult
}
interface ReturnRepository {
    suspend fun finalizeReturn(command: FinalizeReturnCommand): FinalizeReturnResult
}
```

Both implementations (in-memory test double now, real SQLite-backed implementation in Milestone 4) must guarantee: single atomic commit/rollback boundary per call, idempotency-key short-circuit before any mutation begins, and — closing the real Python gap from invariant #12 — a payload-hash comparison on idempotency-key hit that returns `DUPLICATE_OPERATION_CONFLICT` rather than silently returning stale data for a materially different retry.
