# Phase 6 — catalogue correctness (retail v17)

Design record, written 2026-08-26, BEFORE any code. Phases 1–5 are done and
pushed; wave B completed multi-device sync for stock, users and permissions.
This phase closes the last unguarded path.

Programme design: `docs/launch-readiness/multi-device-design.md` §6 —
"*Catalogue correctness (v17) — `row_version`, reject-stale, changed-field
deltas, tombstones, visible `sync_conflicts`.*"

## The problem, in shop terms

The five catalogue types — `category`, `product`, `customer`, `supplier`,
`reorder_request` — still apply with `ON CONFLICT(id) DO UPDATE`, i.e. plain
**last-write-wins**. Every other synced entity has been given a real conflict
posture by now; these have not.

So a till that has been offline can push a stale product row and **silently
revert a price change** made on another device. Nothing errors, nothing is
logged, and the shop sells at the old price until someone notices. That is the
last money-affecting hole in the sync design.

## The finding that reshapes this phase

**`retail_api.py` does not contain the string `row_version` even once.**

Retail v13 added `row_version`, `updated_at_utc` and `deleted_at_utc` to all
five catalogue tables (`RETAIL_ROW_VERSION_TABLES`, `schema.py:362`). No write
site has ever bumped any of them. Every product, category, customer and
supplier in every install sits at `row_version = 1` permanently, and
`updated_at_utc` is referenced nowhere in the retail API at all.

The column has been dead since the day it landed — the same shape as
`quantity_reserved`, but far more dangerous, because **this phase is built on
it**.

### Why that makes the obvious implementation catastrophic

Reject-stale means "apply only if incoming `row_version` is strictly greater
than local". With every row frozen at 1, incoming is never greater than local,
so **every catalogue update from every device would be discarded** — silently,
because a rejected stale row is by design not an error.

Turning on the gate before the writers bump would not degrade catalogue sync.
It would **stop it completely**, while every test that only checks "the row
arrived once" continues to pass.

**Ordering is therefore not a preference, it is the phase.** The bump lands and
is proved FIRST; the gate is switched on SECOND, in that order, or not at all.

This is the same defect wave B2 found in `auth_routes.set_language` — an
allowlisted field written with no bump — except there it was one site and here
it is the entire catalogue.

## Scope

**Stage 6a — make `row_version` real, then gate on it.**

1. **retail v17**: create `sync_conflicts`; drop the dead `quantity_reserved`
   column from `inventory_balances`.
2. **Every catalogue write site bumps `row_version` and stamps
   `updated_at_utc`, in the SAME statement as the field change.** Ten UPDATE
   sites exist in `retail_api.py` (categories, products ×2, customers ×2,
   suppliers ×2, reorder_requests ×2, plus the loyalty accumulator noted
   below), plus the create sites and `import_api.py`. Enumerate exhaustively;
   a ranked search will miss one, and a missed site is a change that silently
   never syncs once the gate is on.
3. **Emission payloads carry `row_version`.**
4. **Apply becomes reject-stale**: strictly greater wins, equal or lower is
   discarded and NOT an error — identical posture to wave B2's `user`.
5. **A rejected row writes a visible `sync_conflicts` entry**, never a silent
   drop. This is the whole point of the table: design §6 calls for "a visible
   `sync_conflicts` table instead of silent drops".

**Stage 6b — changed-field deltas and tombstones.** Deferred to its own stage;
deltas are what kill the remaining "one-field edit clobbers everything" gap,
and tombstones replace hard `DELETE`, which the design forbids.

## Decisions

**`stock_exceptions` is NOT created in v17, despite being reserved there.**
Nothing in Phase 6's scope writes it — it belongs to the oversell exception
queue, which no stage here implements. Creating a table with no writer is
speculative generality, and an empty table in a shipped schema actively
misleads: the next reader assumes the feature exists. It stays reserved in
ROADMAP.md for the phase that actually implements the queue.

**Dropping `quantity_reserved` is safe enough to do here.** It is confirmed
dead by three independent audits and by grep — it appears only in schema
definitions, never read or written. `inventory_balances` is in any case fully
derivable from `inventory_movements` (Phase 3), so even total loss of the
table is recoverable with `repair_drift`, which makes this the lowest-risk
destructive migration available in this codebase. SQLite is 3.50.4, so
`ALTER TABLE ... DROP COLUMN` is supported natively. It must still go through
`ensure_schema_version` — live backup and `integrity_check` before and after —
and must be idempotent, checking the column exists before dropping it.

**Reject-stale must prove its ALLOW half.** A gate that discards everything
passes every "stale is rejected" test and silently breaks the catalogue. Wave
B2 proved this exact shape by mutating its gate to `WHERE 0` and watching only
the allow-half test fail. Repeat that here.

## Flagged, not fixed: loyalty accumulators diverge per device

`retail_api.py:2754` does
`UPDATE customers SET total_spent=total_spent+?, loyalty_points=loyalty_points+?`
on every sale. Those two columns are **accumulators**, and the apply branch
deliberately does not carry them (`sync_service.py:754` syncs only name,
phone, email, address, status).

That is currently correct — last-write-wins on an accumulator would LOSE
points, the same way it would lose stock — but it means a customer's points
differ on every till and never converge. No financial impact today, because
points can only be earned and never redeemed (see ROADMAP), so nothing is paid
out against the wrong number.

The right fix, when someone owns it, is the pattern Phase 3 already proved for
`inventory_balances`: **derive them from the synced `sales` ledger rather than
syncing the cache.** The ledger is already replicated, so the derivation is
available on every device. That is a phase of its own, not a line item here.

Do not "fix" this by adding the two columns to the customer payload. That
converts a visible divergence into silent point loss.
