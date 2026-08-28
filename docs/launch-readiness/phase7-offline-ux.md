# Phase 7 — offline UX and exceptions

Design record, written 2026-08-28, BEFORE any code. Phase 6 is complete and
pushed (`82933a1`, `ef66790`, `d478d42`, `6a37b51`).

Scope from `multi-device-design.md` §5 and §7 item 7: the offline banner,
stale-stock hiding, the 24h warning and 72h hard stop, the logout/exit block,
the oversell exception queue, and the PO partial-receipt model.

**Two findings below change that specification before a line is written.** Both
were found by reading the code the spec depends on, not by inspection of the
spec itself. Neither is a defect in what exists today; both are cases where the
written plan, implemented literally, would ship a serious bug.

## What already exists, and is more than it looks

`SyncService.get_health()` (`sync_service.py:2438`) already returns everything
the banner needs: `last_success_at` and `last_failure_at` per half,
`last_failure_reason`, `consecutive_failures`, `healthy`, `running`, and a
freshly-read `pending_count` from `_pending_outbox_count()`. A frontend
freshness indicator already consumes it.

So Phase 7 is not building sync telemetry. It is building the *decisions* taken
on top of it, and persisting the one fact those decisions cannot be made
without.

---

## FINDING 1 — the 72-hour hard stop is defeated by restarting the app

`last_success_at` lives in `self._health`, built by `_fresh_half_health()`
(`sync_service.py:2389`), which initialises it to `None`. It is **in-memory
only**. Nothing writes it to any database.

So on every app start it is `None`, and every rule in §5 that is defined in
terms of elapsed time since the last successful sync silently loses its clock:

* "Offline since 14:20" cannot be rendered — the device does not know.
* Stale-stock hiding after 30 minutes cannot trigger.
* The 24-hour soft warning cannot trigger.
* **The 72-hour hard stop on new sales cannot trigger.**

The last one is the serious one. A till that is restarted daily — which is the
normal way a shop opens in the morning — would never accumulate 72 hours of
recorded offline time, so the hard stop that exists to prevent a shop trading
blind for a week would never fire at all. The feature would appear to be built,
would pass any test written against a single long-lived process, and would be
inert in the field.

That is this project's recurring shape, from `ENGINEERING.md`: a guard whose
test manufactures the exact state that hides the bug. A test must restart the
service — or construct a second instance against the same database — or it is
not testing the guard.

**Consequence: `last_success_at` must be persisted.** That is a schema change,
and it is why Phase 7 claims schema v18 (below) before any agent is dispatched.

Persist the timestamp, not a derived "is offline" boolean: a boolean computed
at write time is stale the moment it is stored, and the thresholds (30 min /
24h / 72h) must be comparable against one recorded instant.

**Clock skew is a known residual here**, consistent with `multi-device-design.md`
§9 risk 3. Elapsed-offline is measured against this device's own wall clock, so
a device whose clock jumps backwards could delay its own hard stop. Ordering
elsewhere in this system is by Owner's server sequence precisely because device
time is not trusted — but there is no server to ask when offline, which is the
whole point. Accepted, and named here rather than discovered later.

---

## FINDING 2 — "block logout while `sync_outbox` is non-empty" bricks every install that does not sync

§5 says logout and app-exit are blocked while `sync_outbox` is non-empty,
because that is how offline orders get lost.

`_queue_sync_event` (`retail_api.py:580`) writes to `sync_outbox` on **every**
catalogue and sale write, unconditionally. It does not consult whether sync is
configured. What is inert on an unconfigured install is the *service*:
`retail_sync_inert_when_unconfigured_test.py` proves `_sync_service` is never
constructed and `nudge()` is a no-op — not that nothing is queued.

So on an install with no relay configured, `sync_outbox` fills from the first
sale and **never drains, because nothing is draining it.**

Implemented literally, that rule means: on day one, the first sale makes the
outbox non-empty, and from that moment **no member of staff can ever log out and
the application can never be closed.** On the majority of installs — single
device, no sync — this is a total, immediate, unrecoverable lock-in. It would
be worse than the data loss it exists to prevent.

The same trap exists in a second form even on a syncing install. The outbox
also fails to drain when the relay is unreachable for a long period, when the
licence lapses, or when a poison event jams the batch — the exact scenario
`ROADMAP.md` already tracks as "one poison event jams a licence permanently".
In all of those the staff are locked in through no fault of their own.

**Required changes to the rule, and none of these is optional:**

1. The block applies **only when sync is actually configured**. Unconfigured
   installs are unaffected, because there is nothing for them to lose — nobody
   is coming to collect those rows.
2. The block must have **an explicit, logged operator override**, so a shop
   whose relay has been down for a week is not held hostage. What it must never
   be is silent: the override should say plainly how many events are unsent and
   that they may be lost.
3. Ask of the emptiness test the question `ENGINEERING.md` teaches: *can this
   state also arrive legitimately?* An empty outbox is the healthy case; a
   permanently non-empty one is not necessarily unsynced work in flight — it can
   equally be work that will never move. Blocking on a condition that can be
   permanently true, with no escape, is the same defect class as the
   `LOCAL_STATE_CORRUPT` sentinel that once blocked the retry that was the cure.

---

## Stage decomposition

Ordered so each stage is independently provable, and so nothing that can brick
an install ships before the condition that makes it safe.

**7a — persisted freshness (schema v18, first because everything depends on it).**
Persist `last_success_at` across restarts; expose elapsed-offline through
`get_health()`. Prove it with a test that constructs a SECOND service instance
against the same database, since a single long-lived instance cannot fail.

**7b — telling the truth (no schema).** The persistent banner ("Offline since
14:20 — 412 unsynced"), and stale-stock hiding: after 30 minutes the POS tile
shows no on-hand number rather than a confidently wrong one. A confidently
wrong figure is precisely the "stock is not accurate" complaint this whole
programme exists to answer, so hiding is the feature, not a degradation.
Decide and write down what the tile shows instead — a dash, a "last known at
HH:MM", or a tap-to-reveal — because "hidden" alone is not a design.

**7c — the blocks (no schema).** Offline-blocked mutations with a plain reason
(create/edit product, category, customer, supplier, employee; role change; PO
receipt; cash-variance approval; stocktake), the 24h soft warning, the 72h hard
stop on new sales, and the logout/exit block **with both conditions and the
override from Finding 2**. This stage can refuse to sell, so it needs the
allow-half proof more than any stage in the programme: prove a healthy till
still sells.

**7d — oversell exceptions (schema v18).** `stock_exceptions`, deliberately NOT
created in v17 because a shipped table with no writer misleads the next reader
into thinking the feature exists. Both devices selling the last unit both
succeed; the merged sum goes negative; `compute_drift` already surfaces it
attributed to two terminals; this stage lands it in a queue the owner resolves
as a business exception. We do not prevent it and we do not build reservations —
a refused sale is worse than an oversell, and the UI says so.

**7e — PO partial receipt and PO sync.** The largest and most separable piece.
Deliberately last: `multi-device-design.md` §8 records that the `purchase_in`
movement already syncs, so *stock* is already correct on both devices today, and
only PO status is phone-invisible. Nothing here is a correctness hole.

---

## Schema v18 — claimed here, before any agent is dispatched

`RETAIL_SCHEMA_VERSION` is a single-writer resource. This project has already
had two branches silently claim the same version, and nothing objected, because
the migration gated on live shape rather than on the number.

**CLAIMED: retail v18, by this branch, covering exactly two things —**

1. Persisted sync freshness (Finding 1). One row, not a column on a business
   table: this is device state, not tenant data.
2. `stock_exceptions` (stage 7d).

Both land through `ensure_schema_version()` like everything else — live backup,
`PRAGMA integrity_check` before and after, marker advanced only on full
success — and every `ALTER`/`CREATE` guarded by `PRAGMA table_info` so a second
run is a no-op.

Anyone else touching `schema.py` before Phase 7 lands should re-derive the head
first and add their own claim; this line records a moment, not live state.

## Open decisions, to answer before the stage that needs them

* **7b:** what the POS tile shows in place of a hidden stock number.
* **7c:** whether the 72h hard stop is overridable, and by which capability.
  It refuses to take money, which is the most severe thing this application can
  do to a shop. `retail.stock.adjust` (manager) is the obvious candidate, by
  analogy with the customer-restore gate added in 6b-iii-b.
* **7c:** whether the offline mutation block is enforced server-side in the
  route handlers, client-side in the UI, or both. Only the handler is
  authoritative; a UI-only block is a suggestion.
* **7d:** who resolves an oversell exception and what the resolutions are
  (backorder / substitute / refund, per §5) — and whether resolving one writes
  a stock movement, which would make it a money-adjacent path needing its own
  capability gate.
