# Phase 5, wave B2 — syncing `user`

Design record, written 2026-08-26, BEFORE any code. Wave B2 was deliberately
split out of wave B1 rather than treated as "a third table", and this document
is why. Nothing here is implemented yet.

Read alongside:

* `docs/launch-readiness/multi-device-design.md` — the programme design.
* `commercial_runtime/sync/sync_service.py` module docstring — the apply-side
  rules waves A and B1 established, and which of them do **not** transfer here.
* The `wip(phase5b1)` / `feat(retail): the money ledger crosses between devices`
  commit messages — the defect shapes that keep recurring.

## Why this is not just another entity type

Every entity type synced so far — `category`, `product`, `customer`,
`supplier`, `reorder_request`, the five money types of wave A, and wave B1's
`inventory_movement` / `branch` — lives in **`retail.db`**, the same database
as `sync_outbox`. That single fact is what makes the whole outbox pattern
work: a write site inserts its row **and** queues its event on the same
connection, in one transaction, so the row and the event either both land or
both roll back.

`users` lives in **`registry.db`** (`commercial_runtime/identity/registry_db.py`),
a different database file, shared with Clinic. None of the guarantees above
are available for free.

Three further differences, each of which has its own section below:

* it carries **credentials** (`password_hash`, `pin_hash`) and
  **security counters** (`failed_login_count`, `locked_until`);
* it carries a **Clinic-owned column** (`clinic_role`) in a table Retail would
  be writing;
* it has **two UNIQUE constraints that are not the sync key** — the exact
  shape that wedged sync permanently in wave A.

## Decision 1 — registry.db gets its own outbox. ATTACH is ruled out.

Three options were considered.

**(a) Give `registry.db` its own `sync_outbox` / `sync_cursor`.** Identity
stays product-agnostic, atomicity is trivially preserved because the row and
its event are in the same file, and Clinic can adopt the same stream later
without Retail being involved.

**(b) `ATTACH` registry.db to the retail connection and write both in one
transaction.** — **RULED OUT, and not on taste.** Both databases run in WAL
mode (`registry_db.py:90` and `products/retail/backend/database/schema.py:484`
both execute `PRAGMA journal_mode=WAL`). SQLite's documented behaviour is that
a transaction spanning multiple attached databases is atomic **within each
database individually, but not across them as a whole, once any one of them is
in WAL mode**. So option (b) does not actually deliver the atomicity that is
its entire justification. It would look correct, pass every ordinary test, and
lose the event exactly when the process died between the two commits.

**(c) Write user events into retail.db's outbox from the identity write
sites.** Rejected twice over: it is the same non-atomic two-database write as
(b) with none of the appearance of safety, and it would make
`commercial_runtime/identity` — code Clinic also runs — depend on a Retail
table. The `local_ensure_schema` constructor hook added in wave A exists
precisely so `commercial_runtime` does not grow product-specific knowledge.

**Chosen: (a).** Note what it does *not* require: the relay and the Owner
Control Center store events opaquely, keyed by `entity_type`, so `user` is
just another string to them. **No Owner-side change is expected.** That claim
must be verified against `owner/app/sync/` before implementation starts, not
assumed.

## Decision 2 — conflict resolution is `row_version`, never last-write-wins

`users` **already carries `row_version`, `updated_at_utc` and `uid`**, added by
earlier registry migrations (`commercial_runtime/identity/account_schema.py`;
`user_accounts.py:382` is the bump site). It also already has a TEXT
`id` primary key, so — unlike `branches` — `id` is a real global identity and
no new wire key is needed.

This matters more here than anywhere else in Phase 5. The five catalogue types
use `ON CONFLICT(id) DO UPDATE` — plain last-write-wins — which is acceptable
for a product name. Applied to a credential it is not:

* a device that was offline while a password was changed elsewhere would, on
  reconnecting, push its stale row and **resurrect the old password**;
* a `status='suspended'` set on the admin's till could be silently undone by a
  till that had not yet heard about it.

**Rule: apply only when the incoming `row_version` is strictly greater than
the local one. A lower or equal `row_version` is discarded, not applied, and
not an error.** Ties break on `updated_at_utc`, and a tie there is discarded
too — converging on "no change" is safe; guessing is not.

## Decision 3 — the field allowlist, and what is deliberately excluded

Wave B2 syncs a **named subset of columns**, never `SELECT *` and never
`excluded.*`. Adding a column to `users` must not silently start replicating
it.

**Synced:** `email`, `employee_id`, `role`, `status`,
`require_password_change`, `language`, `password_hash`, `pin_hash`,
`row_version`, `updated_at_utc`.

**Excluded, each for a stated reason:**

* **`clinic_role`** — a Clinic-owned column. Clinic is out of scope for
  features by the owner's instruction and must not regress. Retail's sync
  stream writing it would be a cross-product regression with no upside to a
  Retail shop. *Consequence, stated rather than discovered later: a clinic
  role assigned on one device does not propagate. Clinic is single-device
  today, so this costs nothing now, and it is Clinic's own wave to claim.*
* **`failed_login_count`, `locked_until`** — these are **device-local security
  state**, not shared facts. A lockout describes the device being attacked. If
  they synced, a stale row arriving from an idle till would **clear a live
  lockout**, converting a defence into a nuisance for the attacker. Note this
  is the failure shape ENGINEERING.md §1(4) names: a legitimately stored value
  arriving through a normal path and defeating the guard.
* **`session_version`** — excluded from the plain field list because it must
  **never move downward**. It is the revocation counter Phase 5's prerequisites
  bump to force re-login, and `onboarding_routes.py` bumps it on nearly every
  mutation. It is applied as **`MAX(local, incoming)`**, so a role change on
  one till still revokes sessions on the others, while a stale row can never
  un-revoke a session. Downward movement is a security regression, not a merge
  conflict.

## Decision 4 — the two UNIQUE constraints WILL wedge sync unless handled

`users` declares `email TEXT UNIQUE NOT NULL` and `UNIQUE(company_id,
employee_id)`. **Neither is the sync key.**

This is precisely wave A's defect #1, which stopped all sync from all devices
permanently: `ON CONFLICT(id)` suppresses only the `id` index; a *different*
unique index still fires, the `IntegrityError` escapes `pull_once`, the cursor
is never advanced, and every event behind it — from every device, catalogue
included — stops arriving forever, with the operator seeing only an
"IntegrityError" banner.

Two admins creating the same person on two tills while offline produces
exactly this: same email, two different `id`s.

**Requirement: both constraints must be caught and the event quarantined via
`_quarantine_apply_event`, never allowed to raise.**

**This design missed a prerequisite, found during stage 2a and recorded here
rather than left in a commit message.** `_quarantine_apply_event` writes to
whatever connection it is handed — `registry.db`, for the registry-configured
instance — and `sync_apply_quarantine` existed only in `retail.db`. So
quarantining a user event required a registry-side quarantine table, which is
**registry v6**. The moment a synced table carries a second unique constraint
that is not its wire identity, the quarantine path becomes reachable; the
table has to exist before it is.

It is a SEPARATE migration rather than an addition to v5's function, and that
distinction is load-bearing: `ensure_schema_version` never re-enters a
database already at its target version, so silently editing v5's function
would never reach any install that had already upgraded to v5. New table, new
version, always. A duplicate person is a
visible, recoverable data-entry problem; a permanently wedged sync stream is
not. Widening the quarantine to swallow `IntegrityError` generally was
considered and rejected in wave A for good reason — catch these two
constraints by name.

## Decision 5 — `user_permissions` is in scope, not deferred

`user_permissions` (`registry_db.py`, keyed `user_id` + `subsystem`) is a
second table. Syncing `users` alone would give a cashier who exists on the
second till **no permissions there** — an account that logs in and can do
nothing, which reads to the shop as "the system is broken".

It ships in the same wave, or wave B2 does not ship. If it is ever split out,
that consequence must be written down first.

**The trap stage 3 must not walk into, identified while reviewing stage 2a.**
`user_permissions.user_id` references `users.id` — the LOCAL TEXT primary key,
which the apply path MINTS FRESH on the receiving device
(`str(uuid.uuid4())` in the `user` INSERT). It is not the wire identity; `uid`
is. So a permission row arriving with the SENDING device's `user_id` names a
row that either does not exist on the receiver or, worse, belongs to a
DIFFERENT person.

A permission attached to the wrong user is a silent privilege change — the
most dangerous shape in this whole wave, and it would raise nothing.

So `user_permission` payloads must carry the owning user's **`uid`**, and the
apply path must resolve it to the local `users.id` the same way wave A's
`sale_item` resolves its parent through `_local_id_by_uid`. If the uid does
not resolve yet, the event is QUARANTINED like any other orphan, never
guessed at and never dropped.

Prove it explicitly with two users present on the receiver, so a
resolve-to-the-wrong-row bug cannot hide behind there only being one
candidate.

## Reserved before any agent is dispatched

`REGISTRY_SCHEMA_VERSION` is **4** today (`registry_db.py:52`). Wave B2 needs
**registry v5** for the registry-side outbox and cursor tables.

**That reservation goes into `ROADMAP.md` BEFORE any implementation agent
starts** — a schema version is a single-writer resource, and this project has
already had two branches silently claim the same one, because the migration
gated on live shape rather than on the number.

## Acceptance — what would make this provably done

1. A user created, renamed, suspended, and password-changed on device A is
   correct on devices B and C, with `row_version` monotonic on all three.
2. **The allow half, proven separately from the deny half.** A stale row is
   discarded *and* a genuinely newer row still applies. A guard that denies
   everything passes every deny test; this project has shipped that once
   already.
3. `session_version` never decreases on any device, under any delivery order,
   including a replayed and a reversed batch.
4. Duplicate `email` and duplicate `(company_id, employee_id)` from two devices
   are **quarantined**, the cursor still advances, unrelated events in the same
   batch still land, and the quarantined event drains once resolved.
5. A lockout applied on one device is **not** cleared by a sync from another.
6. `clinic_role` is byte-unchanged on every device after a full user sync
   round, and the Clinic suite is green.
7. The row and its event are proven atomic **within registry.db** by killing
   the process between them.
8. Credentials never appear in the outbox payload in plaintext, never in a log
   line, and never in a quarantine `detail` string.

Each of these must be **mutation-proven** — break the guard, watch the test go
red, restore, quote both directions — per ENGINEERING.md §1. The delivered
suites for waves A and B1 were blind to their own defects twice, both times
because a fixture built the second device as a pure receiver. The multi-device
harness that actually exercises both devices is a precondition here, not a
deliverable.

## Decision 6 — TWO SyncService instances, each owning one database

`SyncService.__init__` is already fully injected — `client_factory`,
`get_conn`, `local_company_id_provider`, `local_ensure_schema` — and retail
already builds two instances of it (`products/retail/backend/app.py:524` and
`:560`). So the registry stream is a SECOND instance whose `get_conn` returns
`registry.db`, with its own `sync_outbox` and its own `sync_cursor` in that
same file. No constructor change is needed.

**Why two streams rather than one stream that routes by entity type:** a
single stream would have one cursor, and a batch mixing retail and user
events could not be committed atomically, because the two databases are both
in WAL and SQLite gives no cross-database atomic commit (Decision 1). With two
streams, each batch commits atomically inside the one database it belongs to,
and each cursor advances independently. The cost is that every device pulls
the relay stream twice, once per service. For a shop with two or three tills
that is nothing.

**The hazard this creates, which must be designed out rather than tested for
later.** Both instances pull from the SAME relay, so the retail instance WILL
receive `user` events and the registry instance WILL receive `sale` and
`inventory_movement` events. `_apply_event` dispatches on `entity_type`
alone. If the `user` branch were reachable from the retail instance it would
try to write `users` into `retail.db`, which has no such table —
`sqlite3.OperationalError: no such table: users` escaping `pull_once`, the
cursor never advancing, and **all sync from all devices stopping forever**.
That is wave A's defect #1 exactly, reproduced by construction.

So `_apply_event` must know WHICH STREAM IT IS. Add an explicit
constructor-supplied set of handled entity types (defaulting to the retail
set, so existing call sites are unchanged), and make an event whose type is
not in that set a **skip that still advances the cursor** — the same posture
the module already takes for an unknown future entity type from Owner. This
turns today's implicit, comment-documented allowlist into an enforced one,
which is worth doing on its own merits.

Both directions need proving, not just the deny half: a `user` event must be
IGNORED by the retail instance while still being APPLIED by the registry one,
and a `sale` event the reverse. And in both cases the cursor must still move.

## Settled before implementation (2026-08-26), by reading the code

**The wire identity is `users.uid`, NOT `users.id`.** Registry v3 already added
`uid` (`account_schema.py:87`) and, critically, a PARTIAL unique index:
`CREATE UNIQUE INDEX idx_users_uid ON users(uid) WHERE uid IS NOT NULL`
(`:114`). Two consequences, both load-bearing:

* The conflict target must repeat that clause **verbatim** —
  `ON CONFLICT(uid) WHERE uid IS NOT NULL` — or SQLite raises on EVERY apply.
  This is not theoretical: it is exactly the bug wave A hit against retail
  v13's identically-shaped index, and it fails on the first event, not the
  hundredth.
* `uid` is written as `str(uuid.uuid4())` at every insert site
  (`onboarding_routes.py:179`, `:661`), so it is a real UUID.

That last point matters more than it looks, because **Owner parses
`entity_id` as a UUID** — `entity_id = uuid.UUID(str(raw["entity_id"]))`,
`owner/app/sync/routes.py:192`. Anything that is not a UUID is rejected as
`InvalidEventError("malformed event")`. `users.id` is a TEXT primary key whose
format is not guaranteed to be a UUID, so using it as the wire identity would
have every user event refused at the relay. Use `uid`.

`users` also already carries `row_version` and `updated_at_utc`, both
populated at insert (`1` and `now_utc_iso()`), so the version-based conflict
resolution in Decision 2 works on day one with no backfill.

**Owner needs no change.** `owner/app/sync/routes.py:199` validates
`entity_type` only as a string of bounded length — there is no allowlist — and
`_ALLOWED_EVENT_TYPES = ("create", "update", "delete")` (`:67`) already covers
what this wave emits. Still push a real `user` event through before believing
it.

**The write-site count is 26, not nineteen** — `users` and `user_permissions`
together, across `account_schema.py`, `auth_routes.py`, `mt_auth.py`,
`onboarding_routes.py`, `user_accounts.py` and `verification.py` (that last
file was missing from the earlier estimate, which is exactly why the
enumeration has to be exhaustive rather than ranked).

## Known cost to Clinic, decided rather than discovered

Clinic registers `auth_bp` and `onboarding_bp` (`products/clinic/backend/app.py:82-97`)
and runs the same shared registry migration chain, so it gets the v5
`sync_outbox` table and every emission site in this wave is reachable from
Clinic's own routes. Clinic's backend never imports `commercial_runtime.sync`,
never constructs a `SyncService`, and never calls `push_once` — grep-verified.
**So a Clinic account action queues a row that nothing will ever drain.**

That is a real cost, not nothing, and it is written down here rather than
left to be found. Three things bound it:

* **It is the pre-existing pattern, not a new violation.** Retail's own
  `_queue_sync_event` (`retail_api.py:568`) is likewise UNGATED — it writes to
  `sync_outbox` whether or not a relay is configured, so a Retail install with
  sync switched off accumulates rows exactly the same way. Introducing a
  bespoke gate for the registry stream alone would make the two products
  behave differently for no principled reason.
* **The rate is bounded by design.** Every high-frequency path — ordinary
  login, failed-login counting, lockout, and the hash-upgrade re-encoding — is
  deliberately excluded and emits nothing. Only deliberate account management
  (a hire, a role change, a password reset, a PIN change) leaves a row. For a
  clinic that is a handful of rows a year.
* **The rows are not worthless if Clinic ever syncs.** An outbox that has been
  accumulating is history the first sync can carry, which is why retail does
  not gate either.

It still breaks this codebase's stated "invisible unless opted in" principle
in the strict sense, so it belongs in `ROADMAP.md` as outbox retention for
installs with no drain path — an item that applies to BOTH products, and to
whoever owns Clinic, rather than something this wave should solve unilaterally.

## Open questions remaining
* There are roughly **nineteen** write sites against `users` across
  `account_schema.py`, `auth_routes.py`, `mt_auth.py`, `onboarding_routes.py`
  and `user_accounts.py` — far more than wave B1's six. Every one needs an
  outbox event or a written reason it does not. Enumerate them exhaustively
  first; a ranked search will miss one, and a missed site is an account change
  that silently never reaches the other till.
* Does any Clinic code path read `users` in a way that a newly-arrived row
  could disturb? Clinic does not appear to **write** `users` at all
  (`products/clinic/backend/app.py` reads only), which lowers the risk, but
  read paths still need checking.
