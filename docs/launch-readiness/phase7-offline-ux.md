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

---

# The four open decisions, answered 2026-08-28

Answered by reading the code each one depends on. One of them changed
materially once the code was read, and it is the first.

## Decision 1 (7b) — hiding the stock number means suspending TWO derived behaviours, not relabelling one

The spec says the POS tile hides the on-hand number once the last sync is older
than 30 minutes, rather than show a confident wrong one.

Read what `total_stock` actually feeds in `subsystem-retail.js`, and it is
**three** things, not one:

1. the tile label `Stock: N` (`:2097`);
2. the `is-low` / `is-out` visual state, including rendering the words
   **"Out of stock"** in place of the number (`:2059`, `:2097`);
3. **`max_stock` on the cart line (`:2123`)**, which `_updateQty` enforces as a
   hard refusal: `if (newQty > item.max_stock) { showToast('Max stock: …');
   return; }` (`:2136`).

So hiding only the label would leave the two behaviours that *act* on the stale
figure fully live. A till that has been offline for an hour would still refuse
to add a fourth unit to the cart because its hour-old cache says three, and
would still tell the cashier "Out of stock" about something sitting on the
shelf in front of them.

That is the confident-wrong behaviour moved somewhere worse: from a number the
cashier can disbelieve to a refusal they cannot override. And it contradicts
this programme's own stated principle in `multi-device-design.md` §8 — *a
refused sale is worse than an oversell*, which is precisely why we build no
reservations.

**Decision: when stock is stale, suspend all three together.** The tile shows
the last known figure explicitly timestamped and de-emphasised rather than a
bare number — "Stock at 14:20" — so the cashier gets a *dated* fact instead of
either a confident lie or a useless blank. The `is-out` state and the
`max_stock` cap both stop being enforced while stale; the cashier's eyes are a
better sensor than an hour-old cache. Anything that goes negative as a result
lands in the 7d oversell queue, which is exactly what that queue is for.

This makes 7b depend on 7d being at least designed, and is the reason the tile
must not simply be blanked: "no number" removes the lie but also removes the
only information the cashier had.

## Decision 2 (7c) — the 72-hour stop is a manager-overridable block, not an absolute refusal

Two facts constrain this.

**A new capability code is expensive.** `CAPABILITY_CODES`
(`user_accounts.py:159`) is a fixed tuple of exactly eight, and its comment
records that the tuple IS the seeding contract: every account gets a row for
every code, so "never provisioned" and "explicitly denied" stay
distinguishable. Adding a ninth means a registry migration and a re-seed of
every existing account. Reuse an existing code.

**An absolute stop contradicts the programme's own principle.** §8 says a
refused sale is worse than an oversell, and that is the stated reason we build
no reservations. A hard stop at 72 hours refuses *every* sale — for a shop whose
internet has been down for three days, the application would close the shop.
That is the most severe thing this software can do to a business, and it would
be done on the basis of a locally-measured clock the design itself does not
trust (§9 risk 3).

**Decision: at 72 hours new sales are blocked behind a manager override**, not
refused outright. The block states elapsed offline time and unsent event count;
the override is gated on **`CAP_CASH_APPROVE`** and every use is logged with
both figures.

`CAP_CASH_APPROVE` rather than `CAP_STOCK_ADJUST` because it is already this
codebase's "a manager accepts an anomaly instead of the system refusing"
authority — it is what approves a cash variance. `CAP_STOCK_ADJUST` is
master-data editing, a different kind of permission that happens to be held by
the same people.

The 24-hour soft warning needs no capability: it informs, it does not block.

## Decision 3 (7c) — enforced in the handler, explained in the UI

Enforce in the Flask route handlers; surface the plain reason in the UI. A
UI-only block is a suggestion, and this codebase already treats that
distinction as settled — `retail_route_capability_matrix_test.py` exists to
assert every mutating route carries its own server-side gate.

Worth stating because it is easy to get backwards: "offline" here is a **local
policy decision, not an unreachable server**. The Flask backend runs on the
device, so the handler is always reachable; what is unreachable is the relay.
The handler therefore asks the persisted freshness from 7a, not the network.

## Decision 4 (7d) — resolving an oversell writes a stock movement, so it is gated like one

§5's resolutions are backorder, substitute and refund. Every one of them
changes stock, money, or both, so resolving an exception is not an
acknowledgement — it is a write on the ledger this entire programme exists to
keep honest.

**Decision: resolution writes an ordinary `inventory_movement` through the
existing paths and is gated on `CAP_STOCK_ADJUST`**, the same capability every
other movement-writing path already requires. No new code, no separate ledger,
and `compute_drift` keeps working because the correction is a movement like any
other rather than a silent balance edit.

**An exception is never auto-resolved and never silently dropped**, matching
the posture already required of quarantined sync events in `ROADMAP.md`. It is
resolved by a human or it stays open and visible.

Left deliberately unanswered, because it needs the owner's input rather than
the code's: whether an unresolved oversell should ever *block* anything (it
should not, on the same "refused sale" principle), and how long resolved
exceptions are retained.

---

# Correction to Decision 1, made before building 7b (2026-08-28)

Decision 1 above says that when stock is stale, all three behaviours driven by
`total_stock` suspend together: the tile label, the `is-out` state, and the
`max_stock` cart cap. **The third of those is wrong, and this is the correction.**

`create_sale` enforces the SAME rule server-side, and always has
(`retail_api.py:~3193`):

    on_hand = float(balance['quantity_on_hand']) if balance else 0.0
    if qty > on_hand:
        return 'Insufficient stock for "..." (have N, requested M).', 400

Both the client cap and the server check read the same local
`inventory_balances` row. Suspending only the client half therefore does not
let the cashier sell anything — it just moves the refusal from a clean toast at
the cart to a 400 from the server after the sale is attempted. **Strictly worse
for the cashier, with no benefit.** A stale cache would still refuse the sale;
the operator would simply be told later and less clearly.

To actually let a cashier sell past a stale figure, the SERVER check has to
relax, and that is a money-path change: it is the difference between "this
device's ledger says no" and "sell it anyway and record an exception". That
belongs with **stage 7d**, not here, and the two are inseparable in the right
order — an oversell queue with nothing able to put anything in it is a table
with no writer, which is exactly what v17 refused to ship for
`stock_exceptions`.

**Corrected scope for 7b: hide and date the number. Change no enforcement.**
The confident lie is removed from the display, which is the complaint §5
actually names, and nothing about what the till will or will not accept changes
until 7d makes the oversell recordable.

What this does NOT change: the cross-device oversell in §5 still happens
exactly as designed. Each device checks its OWN local balance, so when two
devices each believe they hold the last unit, both sales genuinely succeed and
the merged ledger goes negative. That model was never in question; my error was
about the single-device path, where client and server agree and suspending one
of them achieves nothing.

# Two silence rules 7b must honour, or it harms the majority install

`sync_health`'s own docstring already states the first, and it is not optional:

> `{"configured": false}` is the honest answer on two real installs and is NOT
> an error: `SYNC_RELAY_BASE_URL` unset (the default — most installs never turn
> sync on), and Android (Kotlin's `SyncCoordinator` owns that loop). **The
> frontend banner must stay completely silent on it.**

1. **No banner when sync is not configured.** Most installs are single-device
   and never enable sync. A permanent "offline" banner about a feature they do
   not use is noise that trains people to ignore banners, and it is the same
   class of mistake as FINDING 2's logout block.

2. **No stale-stock hiding when sync is not configured either.** This one is
   not in the existing docstring and matters more. On a single-device install
   the local balance is not a stale copy of anything — it is the only ledger
   there is, and it is exactly as authoritative thirty minutes after a sync as
   it was during one. Hiding it would remove correct information from the
   till's most-used screen to protect against a divergence that cannot occur.

   The freshness clock only means "how far behind other devices might I be",
   so with no other devices it means nothing at all.

`never_synced` (added in 7a) needs its own wording rather than a timestamp: a
configured install that has never completed a first sync has no "offline since"
instant to render, and "Offline since never" is not a sentence.

---

# Correcting §5's offline block list, before building 7c (2026-08-28)

§5 says these are blocked offline with a plain reason: create/edit product,
category, customer, supplier or employee; change a role; receive a PO; approve
a cash variance; stocktake.

**That list was written before Phase 6 existed, and most of it is now wrong.**
Implementing it literally would discard the machinery Phase 6 was built to
provide and make the product worse offline than it needs to be. Checked item by
item against the code:

## Do NOT block: catalogue and party edits

Product, category, customer and supplier edits are exactly what Phase 6 made
safe offline. They carry `row_version`, reject-stale gating, changed-field
deltas so an untouched field is never clobbered, and tombstones instead of hard
deletes. Two devices editing different fields of the same product now converge;
two editing the same field resolve deterministically and record the loser in
`sync_conflicts`.

Blocking them offline would mean building all of that and then refusing to use
it. A shop whose relay is down for an afternoon could not correct a mistyped
price.

The honest residual: a rejected stale edit is DISCARDED, and `sync_conflicts`
has no UI, so the operator who made it is never told. That is real — but it
requires a concurrent edit to the same row on another device, and the remedy is
a conflicts screen, not a blanket refusal to work. **Rare-and-recorded beats
always-blocked.** The missing conflicts UI is recorded in ROADMAP.md.

## Do NOT block: employee and role changes

Wave B2 syncs `user` and `user_permission` with the same `row_version` gating,
plus `session_version` that never regresses. Same reasoning.

## Do NOT block: cash-variance approval

Phase 4 made the drawer terminal-bound: a cash session belongs to one terminal,
and its variance is approved on that terminal. There is no second device to
diverge from, so being offline changes nothing about it.

## Does not exist: stocktake

Grepped: there is no stocktake feature in this product. `adjust_stock` exists
and is a different thing. Blocking it would be blocking nothing.

## DO block: receiving a purchase order

The one item on §5's list that survives scrutiny, and it survives it for a
reason the design itself gets wrong.

`multi-device-design.md` §8 states, as part of the justification for not
syncing purchase orders, that "receiving is admin-device-gated". **It is not.**
`receive_purchase_order`'s guards are `mt_login_required`,
`mt_require_subsystem`, `require_license_capability` and
`mt_require_capability(CAP_STOCK_ADJUST)`. There is no `_is_admin_device`
check on it — that predicate is used on exactly one route in the whole file,
the audit log.

So two devices whose users both hold `retail.stock.adjust` can both receive the
same PO. The route's own double-receive guard is a conditional UPDATE on
`status='pending'`, which is per-device-local, and **PO status does not sync at
all** (§8 keeps it deliberately device-local). The guard therefore cannot see
the other device's receipt, online or offline. Each device writes its own
`purchase_in` movement with its own `uid`, both survive the merge because
movements are additive by design, and the stock is double-counted.

Blocking receipt while behind is a partial mitigation of a hazard that is
**pre-existing and wider than offline** — recorded in ROADMAP.md separately,
because the real fix is either syncing PO status or moving the guard somewhere
that sees both devices.

## Resulting scope

7c blocks exactly one write — PO receipt — and only when sync is configured and
this device is behind. Everything else on §5's list is either already safe
(Phase 6, wave B2, Phase 4) or does not exist.

Both silence rules from 7b apply unchanged: nothing blocks on an install where
sync is not configured, and `never_synced` is not "behind".

## And the logout/exit block is not built at all

FINDING 2 above established that §5's "block logout while `sync_outbox` is
non-empty" would brick every install that does not sync, and prescribed two
conditions plus a logged override to make it safe.

Checking what the block would actually protect, before building that machinery,
answers a prior question: **nothing.**

    @auth_bp.route('/api/auth/logout', methods=['POST'])
    def logout():
        session.clear()
        return jsonify({'success': True})

Logout clears the session and touches no data. `sync_outbox` is a table in a
SQLite file on disk. It survives logout, app exit and reboot, and the sync loop
drains it on the next launch. §5 cites Shopify losing offline orders, but that
failure mode is losing a queue held in volatile memory; this queue is not.

So the cost is real and the benefit is zero:

* it would block **shift handover** — a cashier logging out at the end of a
  shift is a normal, frequent operation, not an edge case;
* `logout` lives in `commercial_runtime/identity/auth_routes.py`, which Clinic
  also registers, and Clinic is out of scope for features;
* per FINDING 2 it would need configured-gating AND a logged override merely to
  avoid bricking installs — elaborate machinery guarding nothing.

The genuine residual is a device retired or uninstalled while still holding
unsent events, and a logout block does not prevent either. What helps there is
telling the operator the count, which **7b's banner already does**. That is the
right amount of intervention.

**So §5's block list, after all three corrections, is exactly one item:
receiving a purchase order.** The rest is either already safe (Phase 6, wave
B2, Phase 4), does not exist (stocktake), or protects nothing (logout/exit).
