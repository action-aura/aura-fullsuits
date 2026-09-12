# Phase 5 prerequisites — the three things sync cannot open without

Written 2026-08-22, ahead of Phase 5, because none of these three existed when
`multi-device-design.md` was written. All three were found by verification
during Phases 1–2, all three are real, and none was estimated in the original
plan. Designing them now takes them off Phase 5's critical path.

Phase 5 is the phase where the owner finally sees one stock figure and one
takings figure on both devices. It is also the phase that turns a per-device
outbox into a shared, permanent, append-only ledger. Everything below is about
making that irreversible step safe.

---

## 1. The identity-side `company_id` rebind (registry v4)

### Why this blocks sync

Retail v14 already exists and is deliberately inert. It converges `retail.db`
onto whatever tenant key `registry.db` has adopted — it never leads.

That was not caution, it was necessity. `session['company_id']` is read from
`registry.db`'s `users` row (`mt_auth.py`), and `retail_api._cid()` filters
essentially every retail query on it. Rebind retail alone and every
`WHERE company_id=?` matches zero rows: **no exception is raised,
`PRAGMA integrity_check` still returns `ok`, and the shop's entire history
simply disappears from the screen.** That is the worst failure shape this
product can have — silent, total, and indistinguishable from data loss.

So identity must move first. Until it does, v14 is a correct no-op on 100% of
installs, and Phase 5 must not open, because rows pushed under
`md5(admin_email)` arrive at Owner scoped to a tenant it does not recognise.

### The design

**Reserve registry v4 in `ROADMAP.md` before writing a line.** That ledger
already records one silent collision where two branches both claimed v9.

The Owner-issued value is **`license_public_id`**, not `installation_public_id`.
The latter is per-device: adopting it would give every till in one shop a
different tenant key, which is precisely the fragmentation this exists to end.
Established by reading Owner, not inferring — `owner/app/sync/routes.py` scopes
the stream by `SyncEvent.license_id`, and `assertions.py` signs that same value.

Tables to rebind in `registry.db`: `users.company_id`, `company_settings.company_id`,
and **any other `company_id`-bearing table — discovered at runtime, not
hardcoded.** Retail's v14 found 30 scoped tables where the design doc estimated
"~13", because the estimate predated the einvoicing, email and WhatsApp tables.
A hardcoded list is a thing that silently goes stale.

**Ordering and atomicity.** There is no cross-database transaction in plain
`sqlite3`, so the two rebinds cannot be atomic together. The design does not
pretend otherwise; it makes the intermediate state safe and self-healing:

1. Identity rebinds `registry.db` in one `BEGIN IMMEDIATE`, verifying row counts
   inside the transaction, rolling back entirely on any mismatch.
2. Retail's existing v14 converges on next launch, deriving the old key from its
   own rows. Already built, already tested, already proven to survive a hard
   `os._exit(9)` mid-transaction and finish on the following launch.

The window between them is the interesting case. During it, identity says the
Owner key and retail still says `md5(admin_email)`, so **every retail query
matches zero rows** — the exact catastrophe described above, just bounded to one
process lifetime. That is not acceptable even briefly.

**Therefore: identity's rebind must run in the same process startup that
immediately triggers retail's convergence, before any request is served.**
`init_app()` already calls `init_retail()` unconditionally; the identity rebind
belongs immediately before it, with retail's convergence following in the same
startup path. If retail's half fails, identity's half must be rolled back or the
app must refuse to serve rather than serve a shop that appears empty.

**Multi-tenant installs refuse rather than merge.** CLAUDE.md states one install
can host more than one company. Retail's v14 already raises `CompanyRebindError`
and changes nothing when more than one non-target `company_id` is present.
Identity must do the same, and — critically — that refusal must be caught in the
migration wrapper. Raising out of a migration would leave a multi-tenant install
unable to advance its schema version *ever again*.

**Activation-time trigger.** Licensing is off by default, so most installs will
migrate long before they ever activate. The rebind therefore cannot be
migration-only: it is one idempotent function called at migration **and** at
licence activation, and a clean no-op when no Owner key exists. Retail already
wires this through `on_activation_success`; identity uses the same seam.

### Tests that must exist

- A v3 registry with real users rebinds correctly; counts unchanged; no row
  stranded on the old key.
- A genuine no-op with no licence present, and the version still advances.
- Interrupted mid-transaction: nothing half-applied, next launch completes it.
- Multi-tenant install refuses and changes nothing.
- **The window test:** identity rebound but retail not yet — assert the app
  refuses to serve rather than serving an empty-looking shop. This is the one
  that matters most and the one easiest to forget.

---

## 2. Pruning `owner_sync_events`

### Why this blocks sync

Today the table holds tens of events. After Phase 5 it holds roughly
**5,000 events per day per device**, each carrying full-row JSONB. There is no
TTL, no retention policy, and **no `DELETE` anywhere** in
`owner/app/sync/routes.py` or `owner/app/models/sync.py`.

A ten-device customer generates ~50,000 rows a day. Within a month that is a
table nobody planned for, on a 2 GB droplet that also runs the Owner web app.
This is not a tidiness concern; it is the shape of an outage that arrives
without warning several weeks after launch, when it is hardest to fix.

### The design

Prune below the **slowest device cursor** for each licence. An event is
deletable only once every registered, non-revoked device for that licence has
acknowledged a sequence at or beyond it. That is the only safe rule: pruning by
age or by count would silently drop events a device that was offline for a
fortnight still needs, and that device would then resync into a hole with no
error.

Explicitly:

- Compute `min(cursor)` across the licence's active devices. Revoked devices are
  excluded — otherwise one revoked terminal pins the table forever.
- A licence with **zero** active devices prunes nothing. Deleting its history
  because nobody is currently listening is how a shop that reinstalls loses its
  ledger.
- Deletion runs in bounded batches, never one unbounded statement, so it cannot
  hold a long lock on the table sync is actively writing to.
- Never delete inside the request path. This runs as its own job.

**Ship it in Phase 5 as a gate, not after.** The original design says so and it
is right: the moment the firehose opens is the moment the table needs a ceiling,
and "we will add pruning later" is how the outage happens.

Add a **per-licence row-count alarm** as well, because pruning that silently
stops working looks exactly like pruning that is working.

Owner uses Alembic and PostgreSQL, unlike the retail side. This is an Alembic
migration plus a scheduled job.

### What must not be touched

`pg_advisory_xact_lock(hashtext(license_id))` in `owner/app/sync/routes.py`
closes a sequence-visibility race and was established at real cost. Pruning runs
outside that critical section and must not extend, weaken or reorder it.

---

## 3. Quarantine for poison events

### Why this blocks sync

Apply is all-or-nothing: `routes.py` rolls back the entire batch on
`INVALID_EVENT`. So **one malformed row stops that shop syncing forever.** Every
subsequent push retries the same batch, fails on the same row, and rolls back.
There is no skip, no visibility, and no operator recovery short of hand-editing
production Postgres.

Today this is nearly harmless because the sync allowlist is narrow. Phase 5
widens it to sales, sale items, returns, return items, payments, inventory
movements, branches and users — eight entity types, thousands of rows a day,
written by two clients on different release cadences. A version-skewed client
sending one field the server rejects would wedge that customer permanently.

### The design

A `sync_quarantine` table and a **skip-and-surface** path:

- An event that fails validation is written to quarantine with the raw payload,
  the rejection reason, the device and the sequence — then **skipped**, so the
  rest of the batch applies and the cursor advances past it.
- Nothing is ever silently dropped. Every quarantined event stays visible in the
  Owner console and is **replayable** once the cause is fixed, because the raw
  payload is retained verbatim.
- A quarantine rate above a threshold raises an alarm. A client that has started
  emitting rows the server rejects is a release problem, and the first symptom
  should be an alert, not a customer phone call.

**The judgement here matters and should be stated plainly:** skipping a bad
event trades strict consistency for availability. That is the right trade for
*this* ledger because the events are append-only business facts, not
transactions with mutual dependencies — a skipped sale is one missing row that
can be replayed, whereas a wedged licence is a shop that cannot sync at all.
It would be the wrong trade for anything where a skipped row silently changes
the meaning of the rows around it.

**Ship this BEFORE Phase 5 opens the allowlist**, not alongside. The whole point
is to already be in place when the volume arrives.

---

## Sequencing

```
registry v4 identity rebind ──┐
owner_sync_events pruning ────┼──> Phase 5 may open the allowlist
poison-event quarantine ──────┘
```

The three are independent of each other and can be built in parallel. All three
must land before the allowlist tuple in `sync_service.py` is widened in either
of the two places it appears.

## Estimate

Not in the original plan, so it is added time, honestly:

| | |
|---|---|
| registry v4 rebind | ~1 day, most of it the window case and the tests |
| pruning | ~0.5 day, plus the alarm |
| quarantine | ~1 day, including the Owner-side visibility |

Roughly **2–3 days ahead of Phase 5**, and they parallelise.
