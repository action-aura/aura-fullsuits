# Phase 6, stage 6b — changed-field deltas and tombstones

Design record, written 2026-08-26, BEFORE any code. Stage 6a is done and
pushed (`adcb712`, `119591c`): catalogue writes bump `row_version`, and the
apply side rejects stale rows and records them in `sync_conflicts`.

Design source: `docs/launch-readiness/multi-device-design.md` §6 —
"*`row_version`, reject-stale, changed-field deltas, tombstones, visible
`sync_conflicts`*". Stage 6a delivered the first, second and fifth. This
stage is the remaining two.

## Part 1 — changed-field deltas

Today an `update` event carries a **full-row snapshot**, and the apply path
writes every column it carries.

That is what the design means by "one-field edit clobbers everything": a
device that edits only a product's `name` emits every other field too, at the
values it happened to hold. Once the row is applied, an untouched field is
overwritten with that device's stale copy of it — even though the operator
never touched it and the gate correctly judged the row as newer.

Reject-stale (6a) narrows this but does not close it: it decides WHETHER a row
applies, not how much of it lands when it does.

**Change: an `update` payload carries only the fields the request actually
changed**, and the apply path sets only those columns. The wire identity,
`row_version` and `updated_at_utc` always travel.

Note what this does NOT become: field-level conflict merging. A stale delta is
still rejected whole, by `row_version`, exactly as now. Deltas reduce the blast
radius of an APPLIED update; they do not make two concurrent edits both win.
Anyone reading this later should not mistake one for the other.

The emission sites already know what changed — `update_product`,
`update_customer` and `update_supplier` build a `sets` list from the request's
own keys (`retail_api.py`), and stage 6a-i already added conditional bumping
driven by that same set. The delta is that set, so this is cheaper than it
looks.

**A `create` still carries the full row.** There is nothing to diff against.

## Part 2 — tombstones

`deleted_at_utc` exists on all five catalogue tables (v13,
`RETAIL_ROW_VERSION_TABLES`) and **no code anywhere writes it** — verified by
grep across `retail_api.py`, `import_api.py`, `reorder_hook.py` and
`sync_service.py`. It is a dead column, exactly as `row_version` was before
6a-i.

Deletion today is expressed two different ways, and both are wrong for sync:

* **`product`, `customer`, `supplier`** soft-delete by setting
  `status='inactive'`. That **overloads `status`**, which independently means
  "deactivated but not deleted" — a real, distinct operator action. A device
  cannot tell a deletion from a deactivation, so it cannot present them
  differently, and un-deleting is indistinguishable from re-activating.
* **`category` hard-`DELETE`s.** It is the one catalogue path a stale remote
  event can still destroy, because a hard delete has no `row_version` to
  compare and so could not be gated in 6a-ii. Flagged there; this is where it
  is fixed.

**Change: deletion stamps `deleted_at_utc` and bumps `row_version`.** It
becomes an ordinary gated update, which means a stale delete loses to a newer
edit for the same reason every other stale write does. `status` goes back to
meaning only what it says.

### The two things this must not break

**Reads must exclude tombstoned rows.** Every catalogue read path — list
endpoints, POS lookups, reports, the barcode scan path — needs
`deleted_at_utc IS NULL`. A missed one shows deleted products for sale. This is
the "field present on one path, absent on another" shape this codebase has
shipped before; enumerate the read sites exhaustively rather than by ranked
search, the way stage 6a-i had to for the write sites.

**`category` deletion has foreign-key consequences.** `products.category_id`
references it, and `retail_category_delete_fk_sync_test.py` exists precisely
because a delete had to be reconciled with rows pointing at it. Read that file
FIRST and understand what it pins before changing the delete from hard to
soft — a tombstoned category that products still reference behaves differently
from a deleted one, and that test is the record of why the current behaviour
is what it is.

## Migration

Both parts are behavioural; no new column is needed, because v13 already added
`deleted_at_utc` and nothing has used it. **No new schema version is expected.**
If one turns out to be needed, reserve it in `ROADMAP.md` BEFORE dispatching
any agent — a schema version is a single-writer resource, and this project has
already had two branches silently claim the same one.

Backfill question to answer before writing code, not after: rows already
soft-deleted as `status='inactive'` are indistinguishable from genuinely
deactivated ones. **Do not guess.** They stay as they are, `deleted_at_utc`
NULL, and only new deletions are tombstoned — the same posture Phase 2 took
when it refused to fabricate `created_at_utc` for history.

## Acceptance

* An update that changes ONE field leaves every other field on the receiver
  untouched, including one another device edited concurrently. Prove it with a
  second field that differs on the two devices — a single-field fixture cannot
  see this bug.
* A delete is a gated update: a stale delete does not remove a row that was
  edited more recently, and a genuinely newer delete does remove it.
* A tombstoned row is invisible to EVERY read path. Enumerate them; prove the
  ones a shop actually hits (list, POS search, barcode scan, reports).
* `status` no longer carries deletion meaning: a deactivated row and a deleted
  row are distinguishable on both devices.
* `category` delete is no longer a hard `DELETE`, and
  `retail_category_delete_fk_sync_test.py` still passes — or, if it must
  change, exactly what it can no longer catch is written down.
* Every guard mutation-proved, both directions, per ENGINEERING.md.
