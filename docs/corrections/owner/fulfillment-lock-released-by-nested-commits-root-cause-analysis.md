# fulfill_order's "row lock" was released by its own nested commits — root cause

Found on GitHub Actions 2026-09-07, fixed and re-measured the same day.
Written in the same shape as
`employee-code-collision-after-sync-root-cause-analysis.md` because it is
the same family of defect: a concurrency guard that reads correctly, was
never actually run under real concurrency until CI happened to schedule
two callers close together, and turned out not to guard anything.

## What the owner would have seen

Nothing, yet — this was caught by a test, not in production. But had it
shipped: two staff members (or one client double-clicking "Fulfill", or a
retried request racing a slow one) confirming the same paid Sales Order at
close to the same moment would each get their own Subscription and their
own License for one order. The customer would hold two licence keys for
one purchase; billing/entitlement math downstream of `Subscription` would
double-count the order.

## Measured

GitHub Actions, run `34138397829`,
`owner/tests/test_phase9_5d_fulfillment.py::
test_item3_concurrent_fulfillment_requests_only_one_creates_subscription`:

    AssertionError: expected exactly 1 Subscription, got 4

Three local runs on the Windows dev machine, same test, same code: 3/3
passed. The bug was never a matter of what the code does — it is a matter
of whether the specific interleaving of five threads happens to expose it,
and CI's scheduler (busier host, more contention, different GC/OS
scheduling) hit that interleaving where a quiet dev machine mostly did not.
This is precisely the failure shape ENGINEERING.md's "a passing test is not
evidence" section warns about: the test asserted an outcome (`count == 1`)
that a lucky interleaving can also produce with a broken guard, so passing
locally proved nothing.

## Root cause

`fulfill_order` (owner/app/commercial_sales/fulfillment.py) took
`db_session.refresh(order, with_for_update=True)` and its own comment
claimed the `SELECT ... FOR UPDATE` row lock this takes "serializes two
concurrent fulfillment attempts for the SAME order". It does not, because
nothing in the rest of the function keeps that transaction open. Every one
of the following statements, in order, calls `db_session.commit()` and
therefore releases the row lock at that point:

1. `audit_record(..., action_code="FULFILLMENT_STARTED", ...)` —
   `app/audit/services.py::record()` ends with `db_session.commit()`, a few
   lines after the row lock is acquired.
2. `create_subscription(...)` (`app/subscriptions/services.py`) — commits
   its own transaction internally (this is intentional, existing behavior:
   fulfillment's own "recovery guard" comment for items #1/#2 relies on
   each step being durable on its own, so a crash mid-sequence can resume
   from whichever rows already exist).
3. `transition_subscription(...)` (same module) — commits again, when the
   Subscription moves DRAFT → ACTIVE.
4. `create_license(...)` (`app/licensing/services.py`) — commits again.
5. `issue_license_key(...)` (same module) — commits again.
6. The function's own final `db_session.commit()` — writes
   `order.status = "FULFILLED"` and the idempotency-key row.

So the row lock is live for roughly the time between "acquire it" and "the
very next line", i.e. long before this function has created anything. A
second concurrent caller can acquire that same row lock right after step 1
commits, read `order.status == "CONFIRMED"` (still true — step 6 hasn't
run), find no `Subscription` row yet for this order (the first caller may
still be inside `create_subscription`, or may not have started it), and
proceed to create a second `Subscription` and, following the same path, a
second `License`. With five concurrent callers all racing through this
same short window, CI measured **4** Subscriptions from **5** callers, not
1.

The bug was not in the recovery-guard logic (items #1/#2, which really do
work — a retry after a crash correctly reuses an existing Subscription/
License row) — it was in believing a lock held on `db_session` could
survive commits that `db_session` itself issues.

## Why local runs never showed it

The window between the row lock's release (step 1's commit) and the
first caller reaching the `SELECT ... WHERE sales_order_id = ...` /
`create_subscription()` call is small — a handful of Python statements and
one more DB round trip. On a quiet single-user dev machine, five freshly
started threads calling `app.app_context()` and doing their own DB setup
rarely land inside that exact window at the same time; one thread
typically gets far enough ahead that the others always see
`order.status == "FULFILLED"` by the time they get there, and the test
passes by accident. A busier CI runner (more scheduling noise, slower or
throttled I/O making the DB round trips take longer relative to the
window) makes the window enormously more likely to be hit — which is
exactly what happened.

## The fix

A per-order mutual-exclusion lock held on a **dedicated database
connection**, entirely separate from `db_session`, so none of
`fulfill_order`'s internal commits can release it early:

- `_hold_fulfillment_lock(order_id)` (owner/app/commercial_sales/
  fulfillment.py) opens its own connection via `get_engine().connect()`,
  begins a transaction on it, and takes a **transaction-scoped Postgres
  advisory lock** (`pg_advisory_xact_lock`, keyed by a SHA-256 hash of
  `"commercial_sales.fulfill_order|<order_id>"`, the same derivation shape
  already used by `operational_reports/scheduler.py::_advisory_lock_key`).
  The lock is released only when this context manager's `finally` rolls
  back that connection's transaction — i.e. only when `fulfill_order`'s
  entire body, including every nested commit, has finished (or raised).
- `fulfill_order` now wraps everything from the row-lock re-read through
  its return statement in `with _hold_fulfillment_lock(order.id):`. The
  `SELECT ... FOR UPDATE` re-read is kept (it is a legitimate use — a
  fresh read of the order's current state once the advisory lock is held,
  in case a previous holder just committed a change), but the comment
  above it no longer claims it is the serialization mechanism.
- A caller that cannot acquire the advisory lock within Postgres's
  `lock_timeout` (10s by default, `app/extensions.py::init_db`) gets a
  `CommercialSalesError("FULFILLMENT_IN_PROGRESS", ...)` instead of a raw
  `OperationalError` (SQLSTATE `55P03`, `lock_not_available`) — a real,
  distinguishable, retryable error rather than a crash. Any other
  `OperationalError` is re-raised untranslated, since only the timeout
  case is this function's to interpret.
- Costs one extra pooled connection for the duration of a fulfillment
  call. This is rare in practice (an operator confirming one order at a
  time) and self-limiting via `lock_timeout`.

## The proof (both directions, verbatim)

Mutation: `with _hold_fulfillment_lock(order.id):` temporarily replaced
with `with contextlib.nullcontext():` in
`owner/app/commercial_sales/fulfillment.py`, then restored exactly. The
test itself
(`test_item3_concurrent_fulfillment_requests_only_one_creates_subscription`)
was also strengthened at the same time: `create_subscription` is
monkeypatched in the fulfillment module's namespace to `time.sleep(0.75)`
before delegating to the real implementation, so every one of the 5
threads is deliberately pushed into the race window instead of depending
on CI-only timing luck.

RED (lock replaced with `contextlib.nullcontext()`):

    C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe -m pytest owner/tests/test_phase9_5d_fulfillment.py -q --no-header -p no:cacheprovider -k item3_concurrent

    AssertionError: expected exactly 1 Subscription, got 5
    assert 5 == 1
    owner\tests\test_phase9_5d_fulfillment.py:425: AssertionError
    1 failed, 17 deselected in 21.68s

GREEN (lock restored exactly):

    C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe -m pytest owner/tests/test_phase9_5d_fulfillment.py -q --no-header -p no:cacheprovider -k item3_concurrent

    1 passed, 17 deselected in 20.31s

Full file, both directions of the guard (allow the one legitimate
Subscription/License to be created; deny four duplicate attempts), and
every other fulfillment test (idempotency replay/conflict, the two
recovery-guard scenarios, incompatible-state rejection, refund
consequence, key issuance/audit, structural no-direct-write proof):

    C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe -m pytest owner/tests/test_phase9_5d_fulfillment.py -q --no-header -p no:cacheprovider

    18 passed in 124.36s (0:02:04)

The strengthened test also asserts the four losing callers are refused
specifically with `CommercialSalesError` code `FULFILLMENT_ALREADY_
COMPLETE` (not merely that the row count is 1) — proving the check ran and
refused them, rather than proving only that some other accident produced
one row. That assertion passed as part of the run above; no worker raised
a lock-timeout or any other error.

## What remains

A partial `UNIQUE` index on `subscriptions.sales_order_id` (`WHERE
sales_order_id IS NOT NULL`) would make this invariant enforced at the
database level too — belt-and-braces alongside the advisory lock, and the
only thing that would also catch a future caller that mutates
`subscriptions` directly, bypassing `fulfill_order` entirely. Not added
here: the Owner Alembic migration head is a single-writer shared resource
across two diverged Owner branches (see the project's own
`feedback_multiagent_schema_coordination` note — reserve versioned
single-writer resources in writing before touching them), and adding a
migration in this pass would risk colliding with in-flight work on either
branch. Deferred to ROADMAP.md rather than added silently.
