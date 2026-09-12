# Phase 6, stage 6b — the decisions taken before code

Written 2026-08-27, BEFORE any 6b code, as
`phase6b-deltas-and-tombstones.md` requires. Read that document first; this one
answers only the questions it deliberately left open, plus one design change to
Part 1 that reading the apply path forced.

Both were settled by reading the code they affect rather than by preference. The
reasoning is recorded because in all three cases the cheaper option is the wrong
one, and the reason is not obvious from the code alone.

---

## Change to Part 1 — the payload keeps the full snapshot and GAINS a changed list

The design says "an `update` payload carries only the fields the request
actually changed". **Implemented instead as: the payload keeps the full-row
snapshot and gains an explicit `_changed_fields` list, and the apply path's
`DO UPDATE` half sets only the listed columns.**

This is a deliberate deviation, not a shortcut. The reason is structural:

**The apply path is an UPSERT, and its INSERT half legitimately fires.** A
receiver that has never seen an id takes the INSERT half — that is the normal
path for a row whose `create` has not arrived, and event ordering across two
devices' independent streams does not guarantee create-before-update. Under a
true delta, that INSERT writes a product with a NULL `sku` and no price.
Fabricating a corrupt row is strictly worse than the field-clobbering being
fixed, and it would be invisible: the row exists, the sync reports success.

`sku` makes the point concretely. It is not in `update_product`'s `allowed`
list, so it can never appear in a changed set — yet the INSERT half needs it on
every create-shaped apply.

What the design actually asks for is that *an untouched field is never
overwritten with another device's stale copy of it*. Keeping the snapshot for
the INSERT half and gating the `DO UPDATE` half on an explicit list delivers
exactly that, with no new ordering requirement anywhere in the system.

The wire payload does not shrink. That is a real cost and it is accepted: the
design's stated motivation is clobbering ("one-field edit clobbers everything"),
not bandwidth, and catalogue edits are admin-driven rather than per-sale.

**Absent `_changed_fields` means "every column changed"** — byte-for-byte
today's behaviour. Every `create` event and every event emitted by a pre-6b-i
device is in that case, so a shop upgrading with a backlog keeps applying its
queued events unchanged. This is the same rule stage 6a-ii established for a
missing `row_version`: an event emitted under the old contract must be judged by
the old contract. Getting this backwards — coalescing absent to "nothing
changed" — would make every legacy update a silent no-op, which is precisely the
defect 6a-ii shipped with and had to fix.

`_changed_fields` is a leading-underscore meta key; no table column starts with
an underscore, so it cannot collide with one.

**Security posture, stated because it is wire data:** the column list is
code-owned and is the only source of column names that reach the SQL string. The
incoming list is membership-tested against it and never interpolated. A payload
naming a column outside that list is ignored, not injected.

---

## Decision A — a tombstoned category DOES cascade, nulling `products.category_id`

**Chosen: walk `products` and null `category_id`, applied identically on the
local delete path AND on the sync-apply path.** Not "leave a dangling id hidden
behind a join filter".

Three reasons, heaviest first:

**1. It preserves the property the FK was added for, deterministically.**
Schema v3's `ON DELETE SET NULL` exists because a hard delete arriving at a
device that still held products raised `IntegrityError` inside `_apply_event`,
which aborted `apply_pull_result` *before the cursor advanced*, and `run_once`
swallowed it — so that device silently stopped receiving every event from every
device, forever. Tombstoning removes the `DELETE`, so the cascade never fires
and that self-healing is gone. Performing the same cascade explicitly keeps the
outcome. Leaving a dangling id instead makes every current and future categories
join responsible for remembering a filter, forever, with nothing to enforce it.

**2. It costs the existing regression test far less than predicted.** The design
predicted two of `retail_category_delete_fk_sync_test.py`'s assertions become
false. With the cascade, only ONE does: `COUNT(*) FROM categories WHERE id=?`
goes from 0 to 1. Both `category_id is None` assertions (`:213`, `:260`) still
hold, because the cascade reproduces exactly the result the FK produced. Every
cursor-advance assertion (`:262`, `:296`, `:406`) — the anti-wedge half, which
is the valuable half of that file — is untouched.

That one assertion changes from "the row is gone" to "the row is tombstoned and
invisible to reads", which is a strictly more precise statement.

**Stated plainly, per ENGINEERING.md, what that file can no longer catch:** the
specific hard-`DELETE`-wedges-the-cursor failure becomes structurally impossible
once no `DELETE` runs, so those cases become historical record rather than live
coverage. The file keeps its value for the tombstone equivalent, which must be
asserted in its place.

**3. No event storm, because the cascade is derived rather than broadcast.** The
apply path performs the identical walk locally when it applies the category
tombstone, so every device reaches the same state from the same single event.

**The cascaded products therefore must NOT bump `row_version` and must NOT emit
product events.** Emitting would turn one category delete into N product
updates, and worse, would advance those products' counters on one device only —
so a genuine concurrent product edit from another till would arrive LOWER and be
rejected as stale. That is stage 6a-i's loyalty-accumulator trap reached by a
third route.

### The residual divergence, named rather than hidden

Device B can edit product P's `category_id` to point at the doomed category
concurrently with device A deleting it. B's product update carries the doomed
id; A applies it (legitimately newer `row_version`) and ends with a dangling
reference, while B — having applied the tombstone and cascaded — has NULL.

Mitigation: every categories join in this codebase is a `LEFT JOIN`; there is no
`INNER JOIN categories` anywhere. Adding `c.deleted_at_utc IS NULL` to those
joins renders the dangling case as a blank category name, identical to what B
renders for NULL.

So the divergence is **cosmetically invisible but really present in stored
data**. Closing it would cost a category lookup on every product apply, which is
not worth it. It is written down so the next reader finds it here rather than
discovering it.

---

## Decision B — a CSV re-import RESURRECTS a tombstoned product

`import_api.py:1366` dedupes on `(company_id, sku)` with **no status filter and
no deleted filter at all**. Today that means re-importing a catalogue row whose
product was soft-deleted updates the dead row and leaves `status='inactive'`
untouched: the operator re-imports their catalogue and the product **silently
does not come back**. That is pre-existing, and it is exactly the failure mode
not to repeat with `deleted_at_utc`.

**Chosen: an import row matching a tombstoned SKU clears `deleted_at_utc`, bumps
`row_version`, and names `deleted_at_utc` in `_changed_fields`.**

An operator putting SKU X in an import sheet is unambiguously asserting "this
product exists, and here are its values". The competing option — leave it
tombstoned and quietly update an invisible row — fails invisibly, which is the
worse of the two failure modes. Resurrecting wrongly (re-importing an old sheet
that still lists discontinued lines) fails visibly and is undone by deleting
them again.

**Deliberately NOT changed in the same edit: `status`.** After this stage
`status` means only "deactivated", a distinct operator action, so an import must
not silently reactivate something a human deactivated on purpose. The read-path
pass established that all four writers of `status='inactive'` are delete-route
handlers and that no deactivate UI exists, so every such row today is in fact a
legacy deletion — but reconciling that belongs in a one-off decision about
legacy rows, not as a side effect of importing a spreadsheet. The backfill
posture is unchanged: legacy `status='inactive'` rows keep `deleted_at_utc`
NULL.

---

## Carried forward from the read-path pass, still owed by 6b-ii

`update_customer`'s allowed-fields list omits `status`, so **a deleted customer
has no restore path at all**, while products and suppliers do. Stage 6b-ii adds
a real undelete per table (clearing `deleted_at_utc`) and must close this gap
rather than carry it forward silently.
