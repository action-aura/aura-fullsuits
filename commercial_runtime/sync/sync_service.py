"""SyncService -- drains the local `sync_outbox` table to Owner's relay
(push) and applies Owner-relayed events from other devices to local tables
(pull). Background-loop shape (`start`/`stop`/`_schedule_next`) mirrors
`commercial_runtime/licensing_contracts/checkin_scheduler.py`'s
`LicenseCheckInScheduler` exactly: a self-rescheduling `threading.Timer`,
daemon thread, `threading.Event` stop flag.

Scope note: `RETAIL_SYNC_ENTITY_TYPES` -- ("category", "product", "customer",
"supplier", "reorder_request", "sale", "sale_item", "payment", "return",
"return_item", "inventory_movement", "branch") -- is the set `_apply_event`
knows how to write, and the DEFAULT set a `SyncService` instance ever
applies. The first five are from earlier sub-projects (multi-device-sync-
foundation, retail-catalog-party-sync-expansion, reorder-automation-
foundation), the next five from launch-readiness Phase 5 (money-moving
sync), and the last two from Phase 5 wave B (stock-moving sync:
`inventory_movement`/`branch`). A future entity type this build has never
heard of at all -- arriving from Owner, or from a future product's own
stream -- is silently skipped, not an error: forward compatibility for a
relay that may carry entity types this particular product build doesn't know
how to apply yet.

Two-stream design (Phase 5 wave B2, docs/launch-readiness/
phase5-waveb2-user-sync.md §Decision 6): `users`/`user_permissions` live in
`registry.db`, a DIFFERENT database file from `retail.db`'s `sync_outbox`/
`sync_cursor`, so wave B2 gives registry.db its own outbox/cursor/quarantine
(registry_sync_schema.py + registry_quarantine_schema.py,
REGISTRY_SCHEMA_VERSION=6) and runs a SECOND `SyncService` instance whose
`get_conn` returns registry.db -- rather than `ATTACH`-ing the two databases
into one transaction (ruled out in that document: both run WAL mode, and
SQLite gives no cross-database atomic commit once either side is in WAL).
Both instances pull from the SAME relay, so the retail instance WILL receive
`user` events and the registry instance WILL receive `sale`/
`inventory_movement` events -- `_apply_event` dispatches on `entity_type`
alone, with no other signal for which stream an event belongs to.

Stage 2a (this file, `_apply_event`'s `user` branch below +
`REGISTRY_SYNC_ENTITY_TYPES`) wired the APPLY side only: a registry-
configured instance can correctly apply a `user` create/update event handed
to it directly (by a test, or by a real pull). Stage 2b (commercial_runtime/
identity/user_accounts.py's `_queue_user_sync_event` + its call sites in
onboarding_routes.py/auth_routes.py/mt_auth.py) is the EMIT side, and has
since landed: every allowlisted write to `users` now queues a `user` event
into registry.db's own `sync_outbox`, in the same transaction as the row
write.

Stage 3 (`_apply_event`'s `user_permission` branch below +
`user_accounts._queue_user_permission_sync_event` + its call sites in
onboarding_routes.py/account_schema.py) closes the gap those two stages
deliberately left open: `users` alone syncing meant a cashier created on
one till arrived on another with NO permissions there. `user_permissions.
user_id` is a LOCAL `users.id`, minted fresh per device by the `user`
branch's own INSERT -- never the wire identity -- so a `user_permission`
payload carries the owning user's `uid` instead, and this branch resolves
it to THIS device's own local `users.id` via `_local_id_by_uid` (the same
helper `sale_item` uses for its parent) before writing anything. An
unresolved uid is QUARANTINED, never guessed at -- resolving to the wrong
local row here would be a SILENT PRIVILEGE CHANGE, the most dangerous
shape in this whole wave.

This is why the entity-type allowlist above is an ENFORCED constructor
argument (`handled_entity_types`, defaulting to `RETAIL_SYNC_ENTITY_TYPES` so
every existing call site -- products/retail/backend/app.py's Windows AND
Android instances -- is unaffected) rather than a comment-documented implicit
tuple. If a branch this module knows how to write were reachable from a
stream it does not belong to -- a `user` branch reached from the retail
instance, say -- it would try to write `users` into `retail.db`, which has
no such table: `sqlite3.OperationalError: no such table: users` escaping
`pull_once`, the cursor never advancing, and ALL sync from ALL devices
stopping forever. That is wave A's defect #1 exactly (an apply branch
reachable from a stream it does not belong to), reproduced by construction
the moment a second stream exists. `_apply_event`'s gate below therefore
checks `entity_type in self._handled_entity_types` -- an event whose type
is outside the CONSTRUCTING instance's own set is SKIPPED, same posture as
a type this build has never heard of at all (the paragraph above): not an
error and never quarantined, because a foreign-stream event is not poison,
it simply belongs to the other stream -- and the cursor still advances past
it.

Wave B note (`inventory_movement` / `branch`): these two are architecturally
DIFFERENT FROM EACH OTHER, not a matched pair, and mixing up which rule
applies to which is exactly how stock or its history gets rewritten from
another device:

  * `inventory_movement` is an IMMUTABLE ledger fact -- stock that moved,
    like a sale. `ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING`, never
    DO UPDATE, and any `event_type` other than `"create"` is silently
    ignored, identical posture to sale/sale_item/return/return_item above.
    Applying it ALSO updates `inventory_balances` for the same
    `(product_id, branch_id)` key, atomically with the movement insert, and
    ONLY when the movement insert actually happened (see that branch's own
    comment -- HAZARD 1 of the phase brief: adjusting the balance on a
    `DO NOTHING` no-op is a silent stock-doubling bug with no error). The
    balance row is created via upsert when it does not yet exist -- a
    receiver that has never stocked this product at this branch must not
    silently drop the movement's effect.
  * `branch` is a MUTABLE record, like the five original catalogue types
    (category/product/customer/supplier/reorder_request) -- a branch renamed
    on one device should converge on every other, so `ON CONFLICT(uid)
    WHERE uid IS NOT NULL DO UPDATE`. Unlike the five original catalogue
    types, though, `branches.id` is NOT the wire identity (`uid` is,
    identical to sale/return/inventory_movement) -- `branches` sits in
    RETAIL_UID_TABLES for exactly this reason. The receiver's own
    `company_id` is authoritative, never the payload's, identical to every
    other branch below. No entity type here ever touches `cash_sessions` --
    a new or renamed branch arriving must never disturb a Phase 4 open cash
    drawer, and the apply code for `branch` simply has no code path that
    could.

Phase 5's five are architecturally DIFFERENT from the first five, and that
difference is the whole design of their branches below: a category/product/
customer/supplier/reorder_request row is a MUTABLE record with its own `id`
AS its wire identity, upserted with `ON CONFLICT(id) DO UPDATE` because the
row is meant to converge to whichever device edited it last. A sale,
sale_item, payment, return or return_item is an IMMUTABLE business fact --
money that moved -- with a separate local autoincrement `id` (private to
this install) and a `uid` (the wire identity, v13's partial-unique-indexed
column). These are applied `ON CONFLICT(uid) WHERE uid IS NOT NULL DO
NOTHING` -- the WHERE clause repeats `idx_<table>_uid`'s own partial-index
predicate verbatim, which SQLite's UPSERT syntax requires to match a partial
index at all (omitting it does not fall back to matching the index; it
raises `OperationalError` on every apply -- see each branch's own comment
for the precise verification). Never DO UPDATE,
and `event_type` OUTSIDE `"create"` is silently ignored for FOUR of the
five (sale, sale_item, return, return_item): a completed sale is corrected
by a return, never edited or deleted in place, and accepting an
update/delete event for money would let one device rewrite what another
device's till actually rang (see each branch's own comment).

`payment` is the ONE documented exception (AUDIT-032A, launch-readiness
Phase 5B): retail_api.py's `POST /payments/<id>/void` mutates
`payments.status` in place -- 'active' -> 'voided' -- and that has always
been true (see that route's own "CREDIT & PAYMENTS" section header: "never
edited -- only voided/reversed (status flag)"), so a blanket "money is
immutable, corrected only by a new row" premise was never actually correct
for this one table. What was missing was not the premise but the sync
event: a void queued nothing at all, so a receipt cancelled on one device
stayed live money on every other device forever. The fix is a `"payment"` /
`"update"` branch that accepts EXACTLY ONE transition -- `status` ->
`'voided'`, nothing else, and NEVER `amount` or any other column -- so a
device still cannot use this path to rewrite money it did not ring; see
that branch's own comment for the full reasoning, including why widening it
to a general `update` was rejected. Every other event_type on `payment`
(anything that is not `"create"` or this one narrow `"update"`) is still
silently ignored, same as the other four.
Three more differences worth stating once here rather than five times below:

  * `company_id` is still always the RECEIVING device's own (never the
    payload's) -- identical reasoning to the "Cross-device company_id bug
    fix" note below, just for five more tables.
  * `session_id` (sales.session_id / returns.session_id, a local
    `cash_sessions.id` FK) is NEVER preserved or remapped on apply -- always
    written NULL. `cash_sessions` is not one of these five synced entity
    types, so no matching row can ever exist on the receiving device, and
    Phase 4 (retail v16, docs/launch-readiness/phase4-terminal-drawer.md)
    made a cash drawer's expected-cash arithmetic sum strictly by
    `session_id` for exactly this reason: a pulled sale with `session_id`
    NULL can never be counted into ANY local drawer, which is what keeps a
    synced sale from being re-attributed to the receiving device's own open
    till. `actor_user_uid` / `terminal_id` / `created_at_utc`, by contrast,
    ARE always preserved from the payload -- a pulled sale keeps its
    ORIGINATING actor and terminal, never the receiving device's own.
  * A sale_item/return_item/payment(sale) needs its parent (sale/return)
    resolved to a LOCAL row by `uid` before it can be inserted (its FK
    points at the parent's local autoincrement `id`, which differs per
    device). When the parent cannot be found -- most notably because
    Owner's own push-side quarantine (681b0fa) can skip a malformed PARENT
    event while still relaying its already-valid children -- the child is
    parked in `sync_apply_quarantine` (visible, replayable) rather than
    dropped or allowed to raise and wedge the whole pull batch. See
    `_quarantine_apply_event` / `_retry_quarantined_events` below.
  * `sales.branch_id` / `returns.branch_id` are NEVER the payload's raw
    integer (AUDIT-032C, launch-readiness Phase 5B) -- `branches.id` is a
    plain per-device autoincrement, and `branch` is deliberately NOT one of
    these five synced entity types (wave B), so no branch row is guaranteed
    to exist locally under that same number, or to mean the same physical
    place if it does. Resolved instead via `_resolve_branch_id()` from the
    payload's `branch_uid` (v13's `branches.uid`, carried by the emitting
    device) to THIS device's own local `branches.id` -- falling back,
    visibly (a logged warning, never a silent guess), to this device's own
    default branch when the uid is absent or does not resolve locally.

`reorder_request` (feat/reorder-automation-foundation) is a narrower case
than the other four: its `id` is a real client-generated UUID (unlike
`purchase_orders`, which stays local-only and is never pushed through this
outbox at all -- see products/retail/backend/database/schema.py's
`_migrate_add_reorder_automation_foundation` docstring for why that
distinction matters to Owner's relay), and it only ever arrives as
`create` (the post-sale hook opening a new request) or `update` (an
accept/decline status change) -- there is no `delete` event type for this
entity, so `_apply_event`'s reorder_request branch has no delete case.

Cross-device `company_id` bug fix (2026-08-07, found in live device
testing): a pulled event's `payload["company_id"]` is the SENDING device's
own `company_id` -- NOT a shared/authoritative tenant id. `company_id` is
derived once, locally, at onboarding, as `md5(admin_email)`
(`commercial_runtime/identity/onboarding_routes.py::create_admin`), so two
devices activated with different admin emails on the SAME license end up
with two different `company_id` values. Blindly writing the payload's
`company_id` into a pulled row makes it permanently invisible to the
RECEIVING device's own `WHERE company_id=?` queries (e.g.
`retail_api.py::list_categories`), which always filter on that device's OWN
`company_id` (`_cid()` -> `session['company_id']`, set at login from that
device's own `users` row). `_apply_event` therefore IGNORES
`payload["company_id"]` entirely and stamps the RECEIVING device's own
locally-authoritative `company_id` (read fresh from the registry DB's
`company_settings` table -- see `local_company_id_from_registry` below) onto
every pulled row instead. `_apply_event` runs on a background thread with no
Flask request/session context, so it cannot call `_cid()`/read
`session.get('company_id')` -- it must read the value directly from disk.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_MAX_SECONDS = 300.0
_BACKOFF_JITTER_FRACTION = 0.2
_BACKOFF_MAX_EXPONENT = 32

# Upper bound on how many events a single push request may carry. Owner's
# relay hard-rejects any batch above its own `_MAX_PUSH_BATCH = 200`
# (owner/app/sync/routes.py) with INVALID_BATCH -- and that rejection is
# all-or-nothing, not partial, so a device that accumulated MORE than the
# cap offline could previously never push again: every attempt sent the
# ENTIRE outbox in one request, every attempt was rejected, and the outbox
# never drained (AUDIT 2026-08-19) -- every other device silently stopped
# receiving this device's data, with only a log line and a health flag to
# show for it. push_once() therefore drains in chunks of at most this many
# events. A separate literal rather than an import of Owner's constant on
# purpose: Owner is a separately deployed service this client can never
# import at runtime. If Owner's cap ever changes, this must stay at or
# below it (below is always safe; above wedges exactly as described).
_PUSH_CHUNK_SIZE = 200

#: The entity types retail's `SyncService` instances apply -- see the module
#: docstring's "Scope note" and "Two-stream design" paragraphs. This is the
#: DEFAULT for `SyncService.__init__`'s `handled_entity_types` argument, used
#: whenever a caller does not supply one explicitly -- both of retail's own
#: call sites (products/retail/backend/app.py's Windows and Android
#: instances) construct their `SyncService` this way, so their behavior is
#: unchanged by this constant's introduction. A second stream (e.g. a future
#: registry.db-backed instance syncing `users`) is constructed with a
#: DIFFERENT, disjoint set instead -- see `_apply_event`'s gate below, which
#: is what actually enforces this rather than merely documenting it.
RETAIL_SYNC_ENTITY_TYPES = frozenset({
    "category", "product", "customer", "supplier", "reorder_request",
    "sale", "sale_item", "payment", "return", "return_item",
    "inventory_movement", "branch",
})

#: Phase 5 wave B2 (docs/launch-readiness/phase5-waveb2-user-sync.md) -- the
#: entity types the REGISTRY-configured `SyncService` instance applies (a
#: SECOND instance, wired in products/retail/backend/app.py alongside the
#: retail one, whose `get_conn` returns registry.db -- see the module
#: docstring's "Two-stream design" paragraph and `_apply_event`'s `user`
#: branch below). Deliberately DISJOINT from `RETAIL_SYNC_ENTITY_TYPES`:
#: `user` is never in the retail set, and none of the retail set is ever in
#: this one -- the whole point of `handled_entity_types` (added stage 1) is
#: that each stream only ever writes to the database it actually owns.
#: `user_permission` (registry.db `user_permissions`, keyed `user_id`+
#: `subsystem`) joined this set in Phase 5 wave B2 stage 3 -- see
#: `_apply_event`'s `user_permission` branch below and
#: `commercial_runtime/identity/user_accounts.py`'s
#: `_queue_user_permission_sync_event` for the emit side. Before stage 3, a
#: cashier synced to a second till arrived with NO permissions there --
#: an account that logs in and can do nothing, which read to the shop as
#: "the system is broken". That gap is what stage 3 closes.
REGISTRY_SYNC_ENTITY_TYPES = frozenset({"user", "user_permission"})

#: Of `RETAIL_SYNC_ENTITY_TYPES` (or whatever set a given instance is
#: actually configured with), the subset that ever stamps a company_id onto
#: the row `_apply_event` writes -- see `apply_pull_result`'s own eager-fetch
#: comment. `sale_item`/`return_item` are the only two exclusions: both
#: resolve their parent by `uid` and carry no `company_id` column of their
#: own (schema.py's `sale_items`/`return_items` tables), so eagerly resolving
#: `local_company_id` for a batch containing only these two would be correct
#: but pointless -- and, for a `SyncService` wired with no
#: `local_company_id_provider` at all, would raise for no reason. Named and
#: subtracted from the handled set explicitly, rather than re-hardcoding a
#: second, independent tuple of "the ones that need it" -- exactly the
#: "field present on one path, absent on another" duplication this file's
#: own module docstring now calls out as a bug shape this codebase has hit
#: before.
_ENTITY_TYPES_WITHOUT_COMPANY_ID = frozenset({"sale_item", "return_item"})


def local_company_id_from_registry() -> Optional[str]:
    """Default `local_company_id_provider`: reads THIS device's own single,
    locally-authoritative `company_id` directly from the registry database
    -- never from a pulled event's payload (see module docstring above).

    Every install has exactly one `company_id`: `create_admin` (the only
    account-creation path with no existing-admin requirement) writes the
    `users` row and the `company_settings` row with the SAME `company_id` in
    one transaction, and is only reachable while no valid admin exists yet
    -- so `company_settings` always holds exactly this device's one real
    `company_id` once onboarding has completed. Falls back to the admin
    `users` row (the same source `_cid()`/`create_session` populate the
    session from) if `company_settings` is somehow missing, and returns
    `None` (never a fabricated default) if neither exists yet -- e.g. a pull
    landing before this device has completed its own onboarding, which
    `_apply_event` treats as "cannot apply yet", not "guess company_id=1".
    """
    from commercial_runtime.identity.registry_db import get_conn as _registry_get_conn

    conn = _registry_get_conn()
    try:
        row = conn.execute("SELECT company_id FROM company_settings LIMIT 1").fetchone()
        if row and row["company_id"]:
            return row["company_id"]
        row = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()
        return row["company_id"] if row else None
    finally:
        conn.close()


class SyncService:
    def __init__(
        self,
        client_factory: Callable[[], "SyncRelayClient"],
        get_conn: Callable[[], "sqlite3.Connection"],
        local_company_id_provider: Optional[Callable[[], Optional[str]]] = None,
        local_ensure_schema: Optional[Callable[["sqlite3.Connection"], None]] = None,
        handled_entity_types: Optional[Iterable[str]] = None,
    ):
        """`client_factory` is called fresh on every push_once()/pull_once()
        attempt, not once at construction time -- deliberately, mirroring
        `commercial_runtime/licensing_contracts/routes.py`'s own
        `_build_context()`, which rebuilds its object graph on every request
        rather than caching it at blueprint-creation time. A SyncRelayClient
        built once at process start would freeze whatever `installation_id`
        was on disk at that moment (commonly None, before the operator has
        even activated the license yet); rebuilding on every attempt means
        activation completing later in the same process is picked up on the
        very next tick with no restart required.

        `local_company_id_provider`, when supplied, is called fresh on every
        `apply_pull_result()` batch that actually needs it (never cached) --
        see the module docstring's "Cross-device `company_id` bug fix" note.
        `_apply_event` uses its return value, never the pulled payload's own
        `company_id`, when writing a pulled row locally. Deliberately no
        default here (unlike `local_company_id_from_registry` being a free
        function callers may pass in): a `SyncService` constructed without
        one raises loudly the first time a category event actually needs it,
        rather than silently touching a real on-disk registry DB a test (or
        some future caller) never intended to read.

        `local_ensure_schema`, when supplied, is called with the local
        connection on every `apply_pull_result()` batch that touches a Phase
        5 money-moving entity (create/update sale/return/payment; see that
        method's own eager-fetch comment for the exact condition, shared
        with `local_company_id_provider`'s own fetch) -- BEFORE any of those
        branches writes a row. This exists for one reason: several columns
        `_apply_event`'s sale/return/payment branches write to
        (`sales.due_date`, most of `payments`) are added by a LAZY,
        route-triggered migration in this product (`_ensure_credit_schema`,
        products/retail/backend/api/retail_api.py) that has always assumed
        some HTTP request reaches the app before anything else needs those
        columns. The sync background loop's first pull tick fires on its own
        timer, completely independent of any route ever being hit -- so a
        brand-new device that has never served a single request could
        receive its first pulled sale before those columns exist. Reproduced
        directly against a real install: `sqlite3.OperationalError: table
        sales has no column named due_date`. Deliberately a CONSTRUCTOR
        HOOK, not a hardcoded import of retail_api's own migration function:
        this module is shared with any future product wiring a SyncService
        (clinic does not today), and must never hardcode one product's lazy-
        migration function by name. Optional and a no-op when absent (unlike
        `local_company_id_provider`, which raises when needed-but-missing --
        see `_get_local_company_id`): a product with no such lazy migration
        at all, or a test that only ever exercises the pre-existing five
        catalogue entity types, has nothing to call.

        `handled_entity_types`, when supplied, is the set of `entity_type`
        strings THIS instance applies -- everything else pulled from the
        relay is skipped by `_apply_event`'s gate, with the cursor still
        advancing past it (see the module docstring's "Two-stream design"
        paragraph for the exact hazard this exists to design out). Defaults
        to `RETAIL_SYNC_ENTITY_TYPES` when omitted -- the historical,
        implicit behavior every existing call site relied on before this
        argument existed, so neither of retail's own two `SyncService`
        constructions (products/retail/backend/app.py's Windows and Android
        instances) needs to change. Stored as a `frozenset` regardless of
        what iterable was passed in, so membership checks in the hot apply
        path below are O(1) and the set can never be mutated out from under
        a running instance after construction."""
        self._client_factory = client_factory
        self._get_conn = get_conn
        self._local_company_id_provider = local_company_id_provider
        self._local_ensure_schema = local_ensure_schema
        self._handled_entity_types = (
            frozenset(handled_entity_types) if handled_entity_types is not None
            else RETAIL_SYNC_ENTITY_TYPES
        )
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()
        # Serializes push_once/pull_once against each other -- the 10s timer
        # tick and an immediate route-triggered nudge() can otherwise land on
        # two different threads at once, both reading/draining the same
        # sync_outbox rows.
        self._lock = threading.Lock()
        self._interval_seconds = 10.0
        self._current_interval_seconds = 10.0
        # Deliberately a SEPARATE lock from self._lock above, not a reuse of
        # it: self._lock is held across the entire network round-trip in
        # push_once()/pull_once() (up to timeout_seconds * max_retries), so a
        # get_health() call from a Flask request thread must never risk
        # blocking behind a dead relay -- that would defeat the entire point
        # of a health-status endpoint. Health is always recorded from
        # run_once()/nudge() OUTSIDE any self._lock block, so the two locks
        # are never nested and there is no lock-ordering hazard.
        self._health_lock = threading.Lock()
        self._health = {
            "push": self._fresh_half_health(),
            "pull": self._fresh_half_health(),
        }

    def push_once(self) -> None:
        """Drains sync_outbox to Owner in sequential chunks of at most
        _PUSH_CHUNK_SIZE events (see that constant's comment: Owner rejects
        any larger batch outright, so an unchunked push of a big offline
        backlog could never succeed and the outbox never drained). Each
        chunk's rows are deleted and committed only after Owner acknowledged
        exactly that chunk, and BEFORE the next chunk is attempted -- so a
        failure partway through (network or a real Owner rejection) raises
        back to the caller with every not-yet-acked event still queued, in
        order, for the next attempt. Chunks Owner already accepted stay
        acked (re-pushing them would only duplicate work); nothing new that
        arrived concurrently is at risk, since only the specific ids just
        pushed are ever deleted."""
        with self._lock:
            conn = self._get_conn()
            try:
                events = self.read_outbox(conn)
                if not events:
                    return
                # Built once per attempt (not once per chunk) -- "rebuilt on
                # every attempt" is the freshness guarantee __init__'s
                # docstring establishes, and one attempt is one push_once().
                client = self._client_factory()
                for start in range(0, len(events), _PUSH_CHUNK_SIZE):
                    chunk = events[start:start + _PUSH_CHUNK_SIZE]
                    client.push(chunk)  # raises on failure -- this chunk's ack below never runs
                    # Ack + COMMIT per chunk, not once at the end: the commit
                    # is what makes a mid-way failure unable to lose or skip
                    # anything -- everything before this point is durably
                    # acked because Owner durably stored it, and everything
                    # from the failing chunk onward is still in sync_outbox
                    # untouched.
                    self.ack_outbox(conn, [e["id"] for e in chunk])
                    conn.commit()
            finally:
                # conn.close() with no prior commit() discards any
                # uncommitted work on this connection -- if push() raised,
                # the DELETE for that chunk never ran, so there is nothing
                # to lose; this only guarantees the connection itself is
                # never leaked.
                conn.close()

    def pull_once(self) -> None:
        """Pulls events newer than the local cursor and applies them, then
        advances the cursor to Owner's returned cursor value -- all in the
        one local transaction, so a mid-apply failure leaves the cursor
        exactly where it was (next attempt re-pulls the same range, not a
        gap)."""
        with self._lock:
            conn = self._get_conn()
            try:
                since = self.read_cursor(conn)
                result = self._client_factory().pull(since)  # raises on failure
                self.apply_pull_result(conn, result)
                conn.commit()
            finally:
                conn.close()

    def read_outbox(self, conn) -> list:
        """Reads (never deletes) the current sync_outbox rows, in the same
        shape push() expects. Split out of push_once() (multi-device-sync-
        foundation, Task 9) so Android's local `/_internal` sync routes can
        read the batch Kotlin is about to push WITHOUT this Python process
        ever calling `self._client_factory().push()` itself -- Android's
        Python never holds the signing key (see
        commercial_runtime/licensing_contracts/android_bridge_identity.py),
        so Kotlin makes the actual signed HTTP call and this method only
        hands it the rows to sign and send.

        ORDER BY rowid -- sqlite's insertion sequence for this table -- and
        deliberately NOT created_at (AUDIT 2026-08-19): created_at is
        local-wall-clock ISO text with no tiebreaker, and wall clocks both
        tie (two writes inside the same clock tick) and step backwards (NTP
        correction, manual change, DST mishandling). Either inversion can
        replay a child ahead of the parent it references (supplier before
        the product pointing at it; product before the reorder_request whose
        product_id is NOT NULL) -- and receivers apply with PRAGMA
        foreign_keys=ON, so the child raises on apply, the receiving cursor
        never advances, and EVERY other device re-pulls the same failing
        batch forever. rowid is assigned monotonically at INSERT and, in
        this table, is never disturbed afterwards: ack_outbox only ever
        deletes already-pushed (oldest) rows, so a surviving row can never
        be out-ranked by a later insert reusing a freed higher rowid."""
        rows = conn.execute("SELECT * FROM sync_outbox ORDER BY rowid").fetchall()
        return [
            {
                "id": r["id"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def ack_outbox(self, conn, ids: list) -> None:
        """Deletes exactly the given outbox row ids -- the second half of
        the read_outbox()/ack_outbox() split push_once() now composes
        itself, and what Android's `/_internal/sync/outbox/ack` route calls
        once Kotlin's push to Owner has genuinely succeeded. Caller commits."""
        if not ids:
            return
        conn.execute(
            "DELETE FROM sync_outbox WHERE id IN ({})".format(",".join("?" * len(ids))), ids
        )

    def read_cursor(self, conn) -> int:
        cursor_row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
        return cursor_row["last_seq"] if cursor_row else 0

    def apply_pull_result(self, conn, result: dict) -> None:
        """Applies a pull result (the exact shape SyncRelayClient.pull()
        returns: `{"events": [...], "cursor": N}`) to local tables and
        advances the cursor. Split out of pull_once() (Task 9) so it can be
        called two ways with identical semantics: pull_once() calls it right
        after fetching the result itself (Windows), and Android's local
        `/_internal/sync/pull-apply` route calls it with a result Kotlin
        already fetched directly from Owner (Android's Python never makes
        that signed HTTP call itself). Caller commits."""
        events = result.get("events", [])
        # Fetched at most once per batch (not once per event) -- it is the
        # SAME value (this device's own company_id) for every event in the
        # batch. Needed for a category/sale/return/payment create or update
        # (the writes that stamp a company_id -- sale_item/return_item carry
        # no company_id column of their own, so they never need this) -- a
        # batch of pure deletes (keyed by categories.id alone) or unknown
        # entity types never touches the provider at all, so a SyncService
        # with no provider configured can still apply those without raising.
        # Also fetched whenever a PREVIOUSLY quarantined event is about to be
        # retried below -- a sale_item alone in this batch (its parent
        # already resolved and its own company_id-less INSERT needs no
        # provider) must not force a provider requirement, but a quarantined
        # SALE waiting for retry does, and there is no cheap way to know in
        # advance which kind is sitting in sync_apply_quarantine without
        # reading it -- so this is a small, deliberate over-fetch rather than
        # a second, more precise scan of the quarantine table.
        local_company_id = None
        touches_pending_or_quarantine = self._has_quarantined_events(conn)
        # `self._handled_entity_types - _ENTITY_TYPES_WITHOUT_COMPANY_ID`,
        # not a second independently-hardcoded tuple: both this check and
        # `_apply_event`'s own gate below derive from the SAME
        # `self._handled_entity_types` (see `__init__`'s docstring and the
        # module docstring's "Two-stream design" paragraph) -- a future
        # entity type added to the handled set is automatically covered here
        # too unless it is also added to `_ENTITY_TYPES_WITHOUT_COMPANY_ID`,
        # rather than silently needing a SECOND edit to a SECOND tuple that
        # could drift from the first without either side raising. An event
        # whose type is not in `self._handled_entity_types` at all (a
        # foreign-stream event, or a type this build has never heard of)
        # never triggers this fetch either way -- `_apply_event` is about to
        # skip it without ever touching `local_company_id`.
        if any(
            ev.get("entity_type") in self._handled_entity_types - _ENTITY_TYPES_WITHOUT_COMPANY_ID
            and ev.get("event_type") in ("create", "update")
            for ev in events
        ) or touches_pending_or_quarantine:
            local_company_id = self._get_local_company_id()
        # `local_ensure_schema` only ever needs to run for sale/return/
        # payment specifically (see this hook's own constructor docstring --
        # `sales.due_date`, `returns.idempotency_key`, most of `payments`).
        # category/product/customer/supplier/reorder_request never touch any
        # column it would create, so calling it for a pure-catalogue batch
        # would be correct but pointless; scoped narrower than the
        # company_id fetch just above on purpose.
        if self._local_ensure_schema is not None and (
            any(
                ev.get("entity_type") in ("sale", "return", "payment")
                and ev.get("event_type") in ("create", "update")
                for ev in events
            ) or touches_pending_or_quarantine
        ):
            self._local_ensure_schema(conn)
        # Collects every branch_uid this batch discarded (see
        # `_resolve_branch_id`'s `fallback_sink` paragraph) instead of
        # letting each one log its own WARNING -- on a busy multi-device
        # install this is a steady per-row WARNING (measured: ~5,000/day/
        # device), which is the same as no log. One consolidated line below
        # keeps the exact same information -- every discarded uid, how many
        # times, where it landed -- at batch granularity instead.
        branch_fallbacks: list = []
        # launch-readiness Phase 6 stage 6a-ii: every catalogue row
        # `_apply_event`'s reject-stale gate DISCARDS this batch lands here,
        # not just a WARNING -- see `_record_sync_conflict`'s own docstring
        # for the full reasoning. Collected the identical way
        # `branch_fallbacks` is collected just above (same `apply_pull_
        # result` batch, same "one WARNING per batch, not per row" motive)
        # so a busy multi-device install discarding many stale rows in one
        # pull tick logs once, not once per row.
        conflicts: list = []
        for ev in events:
            self._apply_event(conn, ev, local_company_id, branch_fallback_sink=branch_fallbacks,
                               conflict_sink=conflicts)
        # Retried AFTER this batch's own events, not before: the whole point
        # of a batch containing the once-missing parent (e.g. an operator
        # replayed a quarantined sale from Owner's console) is that its
        # previously-parked children can resolve in the SAME tick they
        # arrive in, rather than waiting one more pull cycle.
        self._retry_quarantined_events(conn, local_company_id, branch_fallback_sink=branch_fallbacks)
        if branch_fallbacks:
            self._log_branch_fallback_summary(branch_fallbacks)
        if conflicts:
            self._log_sync_conflict_summary(conflicts)
        conn.execute("UPDATE sync_cursor SET last_seq=? WHERE id=1", (result["cursor"],))

    @staticmethod
    def _log_branch_fallback_summary(branch_fallbacks: list) -> None:
        """One WARNING for the whole batch, replacing what used to be one
        WARNING per row -- see `_resolve_branch_id`'s `fallback_sink`
        paragraph for why. Keeps the SAME information a per-row line would
        have: every discarded `branch_uid`, how many rows it hit this batch,
        which company_id, and which local branch id absorbed them -- grouped
        so a uid appearing 500 times in one batch is one entry with count
        500, not 500 identical lines. `did not resolve` and the discarded
        uid(s) both still appear in the message verbatim, matching the
        original per-row wording, so anything that used to grep/assert on
        those substrings for a single-row batch keeps working unchanged."""
        counts: dict = {}
        for branch_uid, local_company_id, default_id in branch_fallbacks:
            key = (branch_uid, local_company_id, default_id)
            counts[key] = counts.get(key, 0) + 1
        parts = [
            f"branch_uid={branch_uid!r} x{count} (company_id={local_company_id!r}, "
            f"filed under default branch id={default_id!r})"
            for (branch_uid, local_company_id, default_id), count in counts.items()
        ]
        logger.warning(
            "sync: %d pulled row(s) this batch had a branch_uid that did not resolve to any "
            "local branch; each was filed under this device's default branch instead. %s",
            len(branch_fallbacks), "; ".join(parts),
        )

    @staticmethod
    def _local_row_version(conn, table: str, row_id) -> Optional[int]:
        """Returns THIS device's own current `row_version` for `table`'s row
        with this PRIMARY KEY `id`, or None when no such row exists locally
        yet. Used ONLY on the discard path of the five catalogue types'
        reject-stale gate below -- the WHERE-gated UPSERT/UPDATE has already
        decided whether to apply (SQLite leaves every column untouched when
        an UPSERT's own WHERE evaluates false, or when a bare UPDATE's WHERE
        does -- proved directly against sqlite3, not assumed; see the
        `category`/`product` branches' own comments), so this is purely to
        recover the LOCAL value a stale write was compared against, for
        `sync_conflicts.local_row_version`.

        A `cursor.rowcount == 0` from that gated statement is ambiguous on
        its own -- it means EITHER "a local row existed and the incoming
        version lost" OR, for a `delete` (a bare UPDATE, not an UPSERT --
        there is no local-row-always-inserted half), "no local row exists
        at all, nothing to soft-delete". Only the first is a conflict worth
        a `sync_conflicts` entry; the second is the same silent no-op a
        delete for an unseen id has always been, before this stage, and
        stays one. Callers distinguish the two by checking this method's
        return value: None means "no local row, not a conflict, do not
        log"; a real integer means "a local row existed and rejected this
        write".

        `table` is always a literal string this module passes ("categories",
        "products", "customers", "suppliers", "reorder_requests"), never
        external input -- see `_row_exists`'s own comment for why the
        f-string carries no injection risk (SQLite cannot parameterize a
        table name). A falsy `row_id` returns None immediately, matching
        `_row_exists`'s own posture on an absent key."""
        if not row_id:
            return None
        row = conn.execute(f'SELECT row_version FROM "{table}" WHERE id=?', (row_id,)).fetchone()
        return row[0] if row is not None else None

    @staticmethod
    def _record_sync_conflict(conn, conflict_sink: Optional[list], *, local_company_id,
                               entity_type: str, entity_id, event_type: str,
                               local_row_version: int, incoming_row_version, payload: dict) -> None:
        """Writes ONE visible `sync_conflicts` row for a catalogue write
        `_apply_event`'s reject-stale gate just discarded, instead of the
        silent drop launch-readiness Phase 6 exists to replace (design §6:
        "a visible `sync_conflicts` table instead of silent drops";
        docs/launch-readiness/phase6-catalogue-correctness.md Task B).
        Columns match `_migrate_add_sync_conflicts_and_drop_quantity_
        reserved`'s own CREATE TABLE exactly (retail schema.py, v17) --
        which row (`entity_type`/`entity_id`), which company, what kind of
        write lost (`event_type`), the version comparison that caused the
        rejection, when, and the full incoming payload so the conflict is
        actually actionable and not just a number.

        Storing `payload` verbatim is safe here specifically because none of
        the five catalogue types' payloads ever carries a credential -- that
        is only ever true of the registry-stream `user`/`user_permission`
        events (a completely different sync stream, see the module
        docstring's "Two-stream design" paragraph), never anything
        `RETAIL_SYNC_ENTITY_TYPES` applies. Never call this for a `user` or
        `user_permission` event.

        `conflict_sink`, when given (always, from `apply_pull_result`'s own
        loop; None only when a test calls `_apply_event` directly and does
        not care), also collects `(entity_type, entity_id)` for `apply_pull_
        result`'s own one-per-batch WARNING -- see `_log_sync_conflict_
        summary` and `_resolve_branch_id`'s `fallback_sink` paragraph for
        why a per-ROW log line is the wrong granularity here: the detail
        this sink's caller needs already lives in the `sync_conflicts` row
        this method just wrote."""
        conn.execute(
            "INSERT INTO sync_conflicts (id, company_id, entity_type, entity_id, event_type, "
            "local_row_version, incoming_row_version, incoming_payload, detected_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), local_company_id, entity_type, entity_id, event_type,
             local_row_version, incoming_row_version, json.dumps(payload),
             datetime.now(timezone.utc).isoformat()),
        )
        if conflict_sink is not None:
            conflict_sink.append((entity_type, entity_id))

    @staticmethod
    def _log_sync_conflict_summary(conflicts: list) -> None:
        """One WARNING for the whole batch, not one per discarded catalogue
        row -- identical reasoning to `_log_branch_fallback_summary` above:
        a busy multi-device install could discard many stale rows in a
        single pull tick (every till that was offline while a price changed
        elsewhere pushes its whole stale catalogue on reconnect), and a
        WARNING repeated that many times is read by nobody, which is the
        same as no log at all. The exact local/incoming `row_version` and
        the discarded payload live in `sync_conflicts` itself (see
        `_record_sync_conflict`) -- this line exists only so an operator
        scanning logs can tell AT ALL that conflicts happened this batch,
        grouped by `entity_type` so "12 product conflicts, 1 customer
        conflict" reads at a glance instead of 13 identical lines."""
        counts: dict = {}
        for entity_type, _entity_id in conflicts:
            counts[entity_type] = counts.get(entity_type, 0) + 1
        parts = [f"{entity_type}={count}" for entity_type, count in counts.items()]
        logger.warning(
            "sync: %d catalogue row(s) this batch were discarded as stale (incoming row_version "
            "not strictly greater than local); see sync_conflicts for detail. %s",
            len(conflicts), ", ".join(parts),
        )

    def _get_local_company_id(self) -> str:
        """Returns THIS device's own locally-authoritative company_id --
        never the pulled payload's. Raises rather than silently applying a
        pulled row under the wrong (or no) company_id: a misconfigured
        SyncService (no provider wired) or a device that hasn't finished its
        own onboarding yet (provider returns None/empty) must surface loudly
        here, exactly like a network failure -- pull_once()'s caller already
        swallows this per-tick (see run_once()'s docstring) and retries."""
        if self._local_company_id_provider is None:
            raise RuntimeError(
                "SyncService has no local_company_id_provider configured; "
                "cannot apply a pulled category event without knowing this "
                "device's own company_id."
            )
        company_id = self._local_company_id_provider()
        if not company_id:
            raise RuntimeError(
                "local_company_id_provider returned no company_id for this "
                "device (onboarding not yet complete?); cannot apply pulled "
                "category events until it does."
            )
        return company_id

    def _apply_event(self, conn, ev: dict, local_company_id: Optional[str] = None,
                      branch_fallback_sink: Optional[list] = None,
                      conflict_sink: Optional[list] = None) -> bool:
        """Applies one pulled event to a local table. Returns True when the
        event is FULLY handled -- inserted, upserted, a legitimate no-op
        (unknown entity_type, a non-`create` event on one of the five
        money-moving types, other than `payment`'s one narrow `update` void
        transition -- AUDIT-032A, see that branch's own comment) -- and
        False only when a Phase 5 money-moving event was PARKED in
        `sync_apply_quarantine` because a row it depends on could not be
        resolved locally (see `_quarantine_apply_event`): a sale_item/
        return/return_item/payment(sale) whose PARENT hasn't arrived yet, or
        a payment `update` (void) whose OWN `create` hasn't arrived yet. The
        pre-Phase-5 five entity types never return False; every one of their
        branches falls through to the unconditional `return True` at the
        bottom, unchanged from before this return value existed. Callers
        that only care about "did this raise" (the main loop in
        apply_pull_result) can ignore the return value entirely; only
        `_retry_quarantined_events` uses it, to decide whether a previously
        parked row may now be deleted.

        `conflict_sink`, when given, is forwarded to `_record_sync_conflict`
        for the five catalogue branches below (`category`/`product`/
        `customer`/`supplier`/`reorder_request`) -- launch-readiness Phase 6
        stage 6a-ii's reject-stale gate. A row discarded as stale writes a
        `sync_conflicts` entry unconditionally (see that method's docstring
        for why storing the payload is safe) and, when a sink is given,
        appends to it for `apply_pull_result`'s own one-per-batch WARNING.
        None is the correct default for a test or caller that only wants the
        `sync_conflicts` row and does not care about the batch summary."""
        entity_type = ev.get("entity_type")
        # `self._handled_entity_types`, not a hardcoded tuple -- see the
        # module docstring's "Two-stream design" paragraph and `__init__`'s
        # own docstring for `handled_entity_types`. An event whose type is
        # outside THIS instance's own set is skipped exactly like a type
        # this build has never heard of at all: never an error, never
        # quarantined (a foreign-stream event is not poison -- it belongs to
        # the other stream), and the cursor still advances past it, because
        # this method returning True is what `apply_pull_result`'s caller
        # reads as "fully handled, nothing left to retry".
        if entity_type not in self._handled_entity_types:
            return True
        p = ev.get("payload") or {}
        event_type = ev.get("event_type")
        if entity_type == "category":
            if event_type in ("create", "update"):
                # `local_company_id` -- THIS device's own company_id -- not
                # `p.get("company_id")`, which is the SENDING device's company_id
                # and is never valid to write into a local row here. See the
                # module docstring's "Cross-device company_id bug fix" note.
                #
                # launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
                # `row_version`/`updated_at_utc` are CARRIED here -- the
                # sender's values overwrite this row's own, exactly like every
                # other column above. `p.get("row_version") or 1` falls back
                # to 1 for a payload that predates this column on the wire
                # (an older emitter, or replayed history) -- never NULL into
                # a NOT NULL column, and `or` (not a bare `.get(..., 1)`)
                # also catches an explicit `0`/`None` value some future
                # hand-built payload might carry. `updated_at_utc` has no
                # such guard: the column is nullable and a missing payload
                # value is honestly "this row's timestamp is not known from
                # the wire", not a value to invent.
                #
                # stage 6a-ii (this stage): `WHERE excluded.row_version >
                # categories.row_version` on the DO UPDATE -- IDENTICAL
                # posture to wave B2's `user` branch below (see that
                # branch's own comment for the full reasoning: reject-stale,
                # never last-write-wins, a lower-or-equal incoming version
                # makes the whole UPDATE a no-op, proved directly against
                # sqlite3). This has no effect on the INSERT half: a
                # genuinely new id always lands, there is no local row to be
                # "stale" against.
                #
                # stage 6a-ii follow-up (missing vs stale, 2026-08-26): a
                # payload that predates 6a-i entirely -- already sitting in
                # SOME device's outbox, in flight to the relay, or queued on
                # a device that upgrades mid-backlog -- carries NO
                # `row_version` key at all. The ORIGINAL gate coalesced that
                # to `p.get("row_version") or 1`, which made a legacy event
                # indistinguishable from a genuinely stale one at version 1:
                # `1 > local` is false against any local row past its first
                # write, so the legacy write was silently DISCARDED --
                # visible in `sync_conflicts`, never applied. Missing is not
                # stale; missing means legacy, and legacy must still apply
                # (a shop upgrading mid-backlog must not lose real catalogue
                # changes). `raw_row_version` below is `p.get("row_version")`
                # verbatim -- genuinely `None` when the key is absent --
                # kept SEPARATE from `insert_row_version` (`or 1`), which is
                # still needed for the bare INSERT path: `row_version` is
                # `NOT NULL`, so a brand-new row (no conflict at all) cannot
                # bind `None` into it, and 1 is the correct starting value
                # for a row this device has never seen, legacy or not.
                # `raw_row_version` is bound TWICE more, only inside the
                # WHERE clause -- `? IS NULL OR ? > categories.row_version`
                # -- so a legacy event (`raw_row_version is None`) always
                # passes the gate and its OTHER columns (name/description/
                # updated_at_utc) apply via `excluded.*` as normal.
                #
                # `row_version=MAX(categories.row_version, excluded.row_version)`
                # -- not plain `excluded.row_version` -- is what keeps the
                # counter itself from REGRESSING on that legacy path:
                # `excluded.row_version` there is `insert_row_version` (1,
                # the "predates this column" fallback), and a local row that
                # had legitimately reached row_version 5 must not be pulled
                # back down to 1 by an old, un-versioned event applying its
                # OTHER fields. For a genuinely modern write the WHERE
                # clause has already proven `raw_row_version >
                # categories.row_version`, so `MAX` there is a no-op (it
                # always resolves to `excluded.row_version` anyway) --
                # `MAX` only ever changes behaviour on the legacy path,
                # which is exactly where it has to. Proved directly against
                # sqlite3 both ways (this stage's own report has the
                # transcript): with `MAX`, a legacy event on a row at
                # row_version 5 leaves it at 5; with `MAX` replaced by plain
                # `excluded.row_version`, it regresses to 1.
                #
                # `cur.rowcount == 0` after this statement can now ONLY mean
                # a MODERN, genuinely stale write (a real `raw_row_version`
                # that is <= local) -- a legacy write (`raw_row_version is
                # None`) always satisfies the WHERE and so always reports
                # rowcount 1, exactly like a brand-new id's plain INSERT.
                # `sync_conflicts` therefore only ever records true
                # conflicts, never a legacy event that was actually applied.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO categories (id, company_id, name, description, row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description, "
                    "row_version=MAX(categories.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > categories.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("description", ""),
                     insert_row_version, p.get("updated_at_utc"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "categories", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="category", entity_id=p.get("id"), event_type=event_type,
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
            elif event_type == "delete":
                # Hard DELETE -- the row is gone, so there is no row_version
                # left to carry (tombstones/soft-delete-with-version are
                # stage 6b, not this fix).
                conn.execute("DELETE FROM categories WHERE id=?", (p.get("id"),))
        elif entity_type == "product":
            if event_type in ("create", "update"):
                # AUDIT-follow-up (2026-08-10): supplier_id and status were both
                # missing here even though retail_api.py's outbox payload already
                # carries both -- supplier_id never propagated cross-device at
                # all, and a soft-delete/restore (products.status) round-trip
                # via an "update" event silently never took effect on other
                # devices. `status` is parameterized (never a hardcoded 'active'
                # literal) so `excluded.status` reflects the SENDING device's
                # actual current status -- a create event's payload never
                # carries status, so p.get(..., "active") preserves the
                # previous create-time default exactly.
                # reorder_method (feat/reorder-automation-foundation): same
                # p.get(..., "none") shape as status/reorder_level above -- a
                # payload that predates this column (an older device's outbox
                # entry, or a hand-built test payload) is treated as the
                # column's own default, never as NULL.
                # launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
                # row_version/updated_at_utc carried through. stage 6a-ii
                # (this stage): `WHERE excluded.row_version >
                # products.row_version` gate, NULL-tolerant for a legacy
                # (pre-6a-i) payload, `MAX`-protected against regressing the
                # counter -- see the category branch's comment above for the
                # full reasoning (identical shape, identical `cur.rowcount
                # == 0` discard signal, identical missing-vs-stale fix).
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO products (id, company_id, sku, barcode, name, category_id, supplier_id, "
                    "cost_price, sell_price, tax_rate, unit, reorder_level, reorder_method, status, "
                    "row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET sku=excluded.sku, barcode=excluded.barcode, name=excluded.name, "
                    "category_id=excluded.category_id, supplier_id=excluded.supplier_id, "
                    "cost_price=excluded.cost_price, sell_price=excluded.sell_price, "
                    "tax_rate=excluded.tax_rate, unit=excluded.unit, reorder_level=excluded.reorder_level, "
                    "reorder_method=excluded.reorder_method, status=excluded.status, "
                    "row_version=MAX(products.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > products.row_version",
                    (p.get("id"), local_company_id, p.get("sku"), p.get("barcode", ""), p.get("name"),
                     p.get("category_id"), p.get("supplier_id"), p.get("cost_price", 0), p.get("sell_price", 0),
                     p.get("tax_rate", 0), p.get("unit", "pcs"), p.get("reorder_level", 5),
                     p.get("reorder_method", "none"), p.get("status", "active"),
                     insert_row_version, p.get("updated_at_utc"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "products", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="product", entity_id=p.get("id"), event_type=event_type,
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
            elif event_type == "delete":
                # A delete event also stamps row_version/updated_at_utc on
                # its sending device (retail_api.py's delete_product) -- carry
                # them here too, or a delete/restore cycle would leave B's
                # counter behind exactly like an update would.
                #
                # stage 6a-ii: this soft-delete is a bare UPDATE, not an
                # UPSERT (there is no "local row didn't exist" INSERT half
                # to worry about) -- `WHERE id=? AND (? IS NULL OR ? >
                # row_version)` gates it the same way the create/update
                # branch above does: NULL-tolerant for a legacy delete (no
                # `row_version` key at all -- see that branch's own comment
                # for the full "missing is not stale" reasoning), a stale OR
                # EQUAL MODERN version leaves the row untouched. `row_version
                # =MAX(row_version, ?)` (bound to `insert_row_version`, the
                # `or 1`-equivalent fallback) is the same never-regress
                # protection: a legacy delete applying `status='inactive'`
                # on a row already at row_version 5 must not pull the
                # counter back down to 1.
                #
                # `cur.rowcount == 0` is ambiguous between "stale delete,
                # local row exists" and "no local row at all, nothing to
                # soft-delete" -- the SAME ambiguity the category/product
                # create/update branches never have (their INSERT half
                # always reports rowcount 1 for an unseen id, and a legacy
                # delete now ALSO always reports rowcount 1 when the row
                # exists, per the NULL-tolerant WHERE above). Resolved via
                # `_local_row_version`'s own return value: None means no
                # local row (the pre-existing, unlogged no-op for a delete
                # of an unseen id -- unchanged by this stage), a real
                # integer means a genuine MODERN stale-delete conflict worth
                # an entry.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "UPDATE products SET status='inactive', "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (insert_row_version, p.get("updated_at_utc"), p.get("id"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "products", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="product", entity_id=p.get("id"), event_type="delete",
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
        elif entity_type == "customer":
            if event_type in ("create", "update"):
                # `status` parameterized for the same reason as the product
                # upsert above -- see that block's comment.
                # launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
                # row_version/updated_at_utc carried through. Note this is
                # DELIBERATELY distinct from total_spent/loyalty_points,
                # which never appear in this payload at all -- see
                # retail_api.py's create_sale comment on that accumulator.
                # stage 6a-ii (this stage): `WHERE excluded.row_version >
                # customers.row_version` gate, NULL-tolerant + `MAX`-protected
                # -- see the category branch's comment above for the full
                # reasoning (missing-vs-stale, why `MAX` and not plain
                # `excluded.row_version`).
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO customers (id, company_id, name, phone, email, address, status, "
                    "row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address, status=excluded.status, "
                    "row_version=MAX(customers.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > customers.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active"),
                     insert_row_version, p.get("updated_at_utc"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "customers", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="customer", entity_id=p.get("id"), event_type=event_type,
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
            elif event_type == "delete":
                # See the product delete branch's comment above -- a delete
                # event carries row_version/updated_at_utc too, and stage
                # 6a-ii gates it the identical way: NULL-tolerant WHERE,
                # `MAX`-protected row_version, `_local_row_version`
                # disambiguating "stale" from "no local row".
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "UPDATE customers SET status='inactive', "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (insert_row_version, p.get("updated_at_utc"), p.get("id"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "customers", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="customer", entity_id=p.get("id"), event_type="delete",
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
        elif entity_type == "supplier":
            if event_type in ("create", "update"):
                # `status` parameterized for the same reason as the product
                # upsert above -- see that block's comment.
                # launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
                # row_version/updated_at_utc carried through. Distinct from
                # credit_balance, which never appears in this payload -- see
                # retail_api.py's `_adjust_credit`.
                # stage 6a-ii (this stage): `WHERE excluded.row_version >
                # suppliers.row_version` gate, NULL-tolerant + `MAX`-protected
                # -- see the category branch's comment above for the full
                # reasoning.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO suppliers (id, company_id, name, phone, email, address, status, "
                    "row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address, status=excluded.status, "
                    "row_version=MAX(suppliers.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > suppliers.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active"),
                     insert_row_version, p.get("updated_at_utc"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "suppliers", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="supplier", entity_id=p.get("id"), event_type=event_type,
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
            elif event_type == "delete":
                # See the product delete branch's comment above -- a delete
                # event carries row_version/updated_at_utc too, gated the
                # identical NULL-tolerant, `MAX`-protected way.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "UPDATE suppliers SET status='inactive', "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (insert_row_version, p.get("updated_at_utc"), p.get("id"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "suppliers", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="supplier", entity_id=p.get("id"), event_type="delete",
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
        elif entity_type == "reorder_request":
            # feat/reorder-automation-foundation. Only create/update ever
            # arrive for this entity -- there is no delete event type (see
            # this module's docstring) -- so unlike the four branches above,
            # this one has no `elif event_type == "delete"` case at all.
            # `status` is parameterized exactly like the other entities'
            # soft-delete flag, so an accept/decline relayed from another
            # device (an "update" event changing status from 'pending' to
            # 'accepted'/'declined') actually takes effect here, not just on
            # the device that made the decision.
            if event_type in ("create", "update"):
                # launch-readiness Phase 6 stage 6a-i (2026-08-26 follow-up):
                # row_version/updated_at_utc carried through. stage 6a-ii
                # (this stage): `WHERE excluded.row_version >
                # reorder_requests.row_version` gate, NULL-tolerant +
                # `MAX`-protected -- see the category branch's comment above
                # for the full reasoning.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO reorder_requests (id, company_id, branch_id, product_id, status, "
                    "draft_message, resolved_at, row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
                    "draft_message=excluded.draft_message, resolved_at=excluded.resolved_at, "
                    "row_version=MAX(reorder_requests.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > reorder_requests.row_version",
                    (p.get("id"), local_company_id, p.get("branch_id"), p.get("product_id"),
                     p.get("status", "pending"), p.get("draft_message"), p.get("resolved_at"),
                     insert_row_version, p.get("updated_at_utc"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "reorder_requests", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="reorder_request", entity_id=p.get("id"), event_type=event_type,
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
        elif entity_type == "sale":
            # Money is an immutable business fact, not a mutable row (see the
            # module docstring's Phase 5 note) -- a completed sale is
            # corrected by a RETURN, never edited or deleted in place.
            # event_type outside "create" is silently ignored: create_sale
            # never emits an update/delete event for this entity type today,
            # so reaching this branch with one means either a future bug on
            # the write side or a malicious/corrupt relay payload -- either
            # way, refusing to apply it is the safe default.
            if event_type != "create":
                return True
            # `session_id` is hardcoded NULL, never taken from the payload
            # (there is no `session_id` key in it at all -- see create_sale's
            # own emission comment) -- see the module docstring's Phase 5
            # note for why this is hazard #2's fix, not an omission.
            # `idempotency_key` is likewise never carried onto a pulled row
            # (also absent from the payload) -- its bare UNIQUE constraint
            # exists for THIS device's own resubmission detection, and `uid`
            # is the correct, collision-safe idempotency key for a pulled
            # copy (see ON CONFLICT(uid) below).
            #
            # `ON CONFLICT(uid) WHERE uid IS NOT NULL` -- the WHERE clause is
            # NOT decorative. `idx_sales_uid` (v13, _ensure_unique_uid_index)
            # is a PARTIAL unique index (`... WHERE uid IS NOT NULL`, so
            # un-backfilled legacy rows with no uid yet don't collide with
            # each other), and SQLite's UPSERT syntax requires the conflict
            # target to repeat a partial index's own WHERE clause verbatim to
            # match it at all -- omitting it does not fall back to matching
            # the index anyway; it raises `OperationalError: ON CONFLICT
            # clause does not match any PRIMARY KEY or UNIQUE constraint` on
            # EVERY apply, unconditionally (verified directly against
            # sqlite3, not assumed from documentation). Same reasoning on
            # sale_item/return/return_item/payment below -- all five share
            # the identical partial-index shape.
            # AUDIT-032C (DEFECT 3): `p.get("branch_id")` is the SENDING
            # device's own local integer -- resolved to THIS device's own
            # local branches.id via the payload's `branch_uid` instead. See
            # `_resolve_branch_id`'s own docstring and the module docstring's
            # matching bullet above.
            resolved_branch_id = self._resolve_branch_id(
                conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
            conn.execute(
                "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, cashier, "
                "subtotal, discount_amount, tax_amount, total, amount_paid, change_amount, "
                "payment_method, status, idempotency_key, notes, created_at, due_date, session_id, "
                "uid, actor_user_uid, terminal_id, created_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,NULL,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("sale_number"), resolved_branch_id, p.get("customer_id"),
                 p.get("cashier", "POS"), p.get("subtotal", 0), p.get("discount_amount", 0),
                 p.get("tax_amount", 0), p.get("total", 0), p.get("amount_paid", 0),
                 p.get("change_amount", 0), p.get("payment_method", "cash"), p.get("status", "completed"),
                 p.get("notes", ""), p.get("created_at"), p.get("due_date"),
                 p.get("uid"), p.get("actor_user_uid"), p.get("terminal_id"), p.get("created_at_utc")),
            )
        elif entity_type == "sale_item":
            if event_type != "create":
                return True
            sale_local_id = self._local_id_by_uid(conn, "sales", p.get("sale_uid"))
            if sale_local_id is None:
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:sale",
                    detail=f"sale_uid={p.get('sale_uid')!r} not found locally")
            # `sale_items` declares TWO real FKs, not one: sale_id -> sales(id)
            # AND product_id -> products(id). Resolving only the first leaves
            # the second as an uncovered wedge with the IDENTICAL cause the
            # quarantine table was built for -- Owner's push-side quarantine
            # (681b0fa) can skip a malformed PRODUCT event just as easily as a
            # malformed sale, and a device that sold a product it had itself
            # RECEIVED by sync emits no product event of its own, so nothing
            # downstream re-supplies it. Reproduced end to end against two real
            # installs (drop the product event, relay sale/sale_item/payment):
            # `sqlite3.IntegrityError: FOREIGN KEY constraint failed`, cursor
            # stuck at 0, nothing applied, and every later pull re-fails on the
            # same batch forever -- catalogue included, from every device on
            # the license, not just the one that caused it.
            #
            # Checked here rather than left to the FK because the whole point
            # of this phase's third hazard is that an unresolvable parent is
            # PARKED (visible, replayable) rather than allowed to raise. Uses
            # the same `_quarantine_apply_event` path, and the same
            # "missing_parent:<thing>" reason vocabulary, as the sale link
            # above.
            #
            # An ABSENT product_id is parked too, not waved through: this
            # column is `INTEGER NOT NULL` (schema.py), so a payload without
            # one raises `sqlite3.IntegrityError: NOT NULL constraint failed`
            # and wedges the batch just as surely as a dangling FK does.
            # Letting "None" past the guard on the theory that a NULL FK is
            # never a violation would have re-opened the very hole this check
            # closes, one row-shape to the left -- `_row_exists` returns False
            # for a falsy id precisely so both cases land here together.
            if not self._row_exists(conn, "products", p.get("product_id")):
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:product",
                    detail=f"product_id={p.get('product_id')!r} not found locally")
            conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, discount_pct, "
                "tax_rate, line_total, uid) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (sale_local_id, p.get("product_id"), p.get("quantity"), p.get("unit_price"),
                 p.get("discount_pct", 0), p.get("tax_rate", 0), p.get("line_total"), p.get("uid")),
            )
        elif entity_type == "return":
            if event_type != "create":
                # Same immutability posture as "sale" above -- a return is
                # itself the reversal; it is never reversed a second time by
                # editing this row.
                return True
            sale_local_id = self._local_id_by_uid(conn, "sales", p.get("sale_uid"))
            if sale_local_id is None:
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:sale",
                    detail=f"sale_uid={p.get('sale_uid')!r} not found locally")
            # AUDIT-032C (DEFECT 3) -- identical reasoning to the "sale"
            # branch's own `_resolve_branch_id` call above.
            resolved_branch_id = self._resolve_branch_id(
                conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
            conn.execute(
                "INSERT INTO returns (company_id, return_number, sale_id, branch_id, cashier, reason, "
                "refund_method, refund_amount, status, idempotency_key, created_at, session_id, "
                "uid, actor_user_uid, terminal_id, created_at_utc) "
                "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("return_number"), sale_local_id, resolved_branch_id,
                 p.get("cashier", "POS"), p.get("reason", ""), p.get("refund_method", "cash"),
                 p.get("refund_amount", 0), p.get("status", "completed"), p.get("created_at"),
                 p.get("uid"), p.get("actor_user_uid"), p.get("terminal_id"), p.get("created_at_utc")),
            )
        elif entity_type == "return_item":
            if event_type != "create":
                return True
            return_local_id = self._local_id_by_uid(conn, "returns", p.get("return_uid"))
            if return_local_id is None:
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:return",
                    detail=f"return_uid={p.get('return_uid')!r} not found locally")
            # `return_items.product_id -> products(id)` -- the identical second
            # FK, the identical `INTEGER NOT NULL` column, and the identical
            # uncovered wedge, as the sale_item branch above. See that
            # branch's own comment for the full reasoning.
            if not self._row_exists(conn, "products", p.get("product_id")):
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:product",
                    detail=f"product_id={p.get('product_id')!r} not found locally")
            conn.execute(
                "INSERT INTO return_items (return_id, product_id, quantity, unit_price, line_total, uid) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (return_local_id, p.get("product_id"), p.get("quantity"), p.get("unit_price"),
                 p.get("line_total"), p.get("uid")),
            )
        elif entity_type == "payment":
            if event_type == "update":
                # AUDIT-032A (DEFECT 2): the ONE narrow, explicit exception to
                # every other branch's "non-create is silently ignored" rule
                # -- see the module docstring's "`payment` is the ONE
                # documented exception" paragraph for the full reasoning.
                # retail_api.py's void_payment route queues EXACTLY this
                # shape (`{'uid': ..., 'status': 'voided'}`); anything else
                # reaching here is either a future bug on the write side or a
                # malicious/corrupt relay payload, and is refused exactly
                # like an unrecognised event_type on any other branch.
                # `amount` (and every other column) is DELIBERATELY absent
                # from both this check and the UPDATE below -- accepting a
                # general update would let one device rewrite money it did
                # not ring, precisely the hole the create-only rule protects
                # sale/return/sale_item/return_item from; this branch exists
                # only because it does NOT reopen that hole.
                if p.get("status") != "voided" or not p.get("uid"):
                    return True
                cursor = conn.execute(
                    "UPDATE payments SET status='voided' WHERE uid=?", (p.get("uid"),)
                )
                if cursor.rowcount == 0:
                    # The void arrived before (or without) its own payment's
                    # `create` event -- the identical orphan shape as
                    # sale_item/return/return_item above (Owner's push-side
                    # quarantine can split a parent from a later event just
                    # as easily as from a child); "parent" here means "the
                    # row this update is about". Parked, not dropped or
                    # allowed to raise and wedge the batch.
                    return self._quarantine_apply_event(
                        conn, ev, reason="missing_parent:payment",
                        detail=f"uid={p.get('uid')!r} not found locally to void")
                return True
            if event_type != "create":
                return True
            # Only a payment FK-tied to a sale (`related_type == "sale"`) has
            # a parent to resolve at all -- `payments.sale_id` is the ONE
            # real FK this table declares (see create_sale/create_return's
            # own emission comments). Every other payment (a customer/
            # supplier account payment, a PO down-payment) has no parent
            # here and applies directly.
            sale_local_id = None
            if p.get("related_type") == "sale" and p.get("sale_uid"):
                sale_local_id = self._local_id_by_uid(conn, "sales", p.get("sale_uid"))
                if sale_local_id is None:
                    return self._quarantine_apply_event(
                        conn, ev, reason="missing_parent:sale",
                        detail=f"sale_uid={p.get('sale_uid')!r} not found locally")
            conn.execute(
                "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, "
                "currency, fx_rate, method, related_type, related_id, sale_id, notes, status, "
                "created_by, device, created_at, uid) "
                "VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("reference"), p.get("party_type"), p.get("party_id"),
                 p.get("direction"), p.get("amount", 0), p.get("currency", "USD"), p.get("method", "cash"),
                 p.get("related_type"), p.get("related_id"), sale_local_id, p.get("notes", ""),
                 p.get("status", "active"), p.get("created_by"), p.get("device"), p.get("created_at"),
                 p.get("uid")),
            )
        elif entity_type == "inventory_movement":
            # Wave B (stock-moving sync). Immutable ledger fact -- see the
            # module docstring's wave B note for why this posture matches
            # sale/sale_item/return/return_item and NOT branch below.
            # event_type outside "create" is silently ignored: no write site
            # ever emits an update/delete for this entity type today, and a
            # future one arriving here would either be a write-side bug or a
            # malicious/corrupt relay payload -- refusing it is the safe
            # default, identical to every other immutable branch above.
            if event_type != "create":
                return True
            # HAZARD 2 (dependency/resolution): `inventory_movements`
            # declares exactly ONE real FK -- `product_id -> products(id)`
            # (see schema.py's `CREATE TABLE inventory_movements`; unlike
            # `sale_items`/`return_items`, there is no second FK to guard --
            # `branch_id` carries no FOREIGN KEY constraint at all, so an
            # unresolvable branch is never a reason to quarantine, only to
            # fall back via `_resolve_branch_id` below, exactly like sale/
            # return already do). A missing product is parked here rather
            # than left to raise and wedge the whole pull batch -- identical
            # reasoning, and identical "missing_parent:product" vocabulary,
            # to the sale_item/return_item branches above.
            if not self._row_exists(conn, "products", p.get("product_id")):
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:product",
                    detail=f"product_id={p.get('product_id')!r} not found locally")
            # Now that `branch` itself syncs (wave B), tier 1 of
            # `_resolve_branch_id` actually resolves in the ordinary case --
            # see that method's own docstring, updated for wave B, and the
            # test that proves the fallback stops firing once the branch
            # this movement's `branch_uid` names has itself arrived.
            resolved_branch_id = self._resolve_branch_id(
                conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
            # `ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING` -- same
            # partial-index shape, same reasoning, as every other
            # RETAIL_UID_TABLES immutable-fact branch above. `created_at` is
            # deliberately OMITTED from both the column list and the payload
            # (falls through to the column's own `DEFAULT CURRENT_TIMESTAMP`
            # at apply time) -- unlike `created_at_utc`, it is not in
            # RETAIL_ACTOR_TABLES' preserved set and no writer in
            # retail_api.py/import_api.py ever sets it explicitly either
            # (see each emission site's own comment); `actor_user_uid` /
            # `terminal_id` / `created_at_utc` ARE always carried from the
            # payload -- `inventory_movements` is in RETAIL_ACTOR_TABLES, and
            # attributing another till's stock adjustment to the local
            # cashier would be a fabricated audit fact.
            cur = conn.execute(
                "INSERT INTO inventory_movements (company_id, product_id, branch_id, movement_type, "
                "quantity, unit_cost, reference, notes, created_by, uid, actor_user_uid, terminal_id, "
                "created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("product_id"), resolved_branch_id, p.get("movement_type"),
                 p.get("quantity"), p.get("unit_cost", 0), p.get("reference"), p.get("notes"),
                 p.get("created_by", "System"), p.get("uid"), p.get("actor_user_uid"),
                 p.get("terminal_id"), p.get("created_at_utc")),
            )
            # HAZARD 1, THE DECISIVE LINE: the balance update happens IF AND
            # ONLY IF the movement row was ACTUALLY inserted -- `cur.rowcount`
            # is 0 for a `DO NOTHING` conflict (a replayed batch, a re-pulled
            # cursor range, a retried quarantine row that resolved on an
            # earlier pass) and 1 for a genuine new row. Adjusting the
            # balance unconditionally here -- "the event was seen, so credit
            # it" -- is precisely the bug the phase brief's mutation proof
            # #3 names: a `DO NOTHING` that silently skips the ledger insert
            # while the balance is bumped anyway doubles stock with no error
            # anywhere. Skipping the balance update on a real insert (the
            # inverse mistake) is mutation proof #1: `compute_drift` goes
            # non-zero the moment a movement lands with no matching balance
            # change.
            #
            # `INSERT ... ON CONFLICT(company_id,product_id,branch_id) DO
            # UPDATE SET quantity_on_hand = quantity_on_hand +
            # excluded.quantity_on_hand` -- the conflict target is
            # `inventory_balances`' own `UNIQUE(company_id,product_id,
            # branch_id)` (schema.py's `CREATE TABLE inventory_balances`,
            # not a partial index, so no WHERE clause is needed to match it).
            # This is a single atomic upsert-with-add: when the balance row
            # does not exist yet (a receiver that has never stocked this
            # product at this branch -- explicitly required by the phase
            # brief, "the balance row may NOT EXIST ... create it rather than
            # silently doing nothing"), the INSERT half creates it seeded at
            # exactly this movement's signed quantity; when it already
            # exists, the DO UPDATE half adds the SAME signed quantity to
            # whatever is there. Both branches move the cache by precisely
            # the ledger row just inserted -- same invariant `adjust_stock`/
            # create_sale/create_return/receive_purchase_order/bulk-import
            # already keep locally, now kept across devices too.
            if cur.rowcount:
                conn.execute(
                    "INSERT INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(company_id, product_id, branch_id) "
                    "DO UPDATE SET quantity_on_hand = quantity_on_hand + excluded.quantity_on_hand",
                    (local_company_id, p.get("product_id"), resolved_branch_id, p.get("quantity")),
                )
        elif entity_type == "branch":
            # Wave B. Mutable, like the five original catalogue types -- a
            # branch renamed on the device that owns it should converge on
            # every other device, so `create` and `update` both take the
            # identical upsert path below (there is no `delete` case: no
            # write site in this product ever removes a branch, only the
            # five original catalogue types get soft-delete semantics).
            # `branches.id` is NOT the wire identity though (unlike category/
            # product/customer/supplier/reorder_request, whose OWN `id` is
            # the payload's conflict target) -- `uid` is, identical to sale/
            # return/inventory_movement -- because `branches` is in
            # RETAIL_UID_TABLES for exactly this reason: the receiver's own
            # `branches.id` is a private per-device autoincrement that means
            # nothing on the wire. `ON CONFLICT(uid) DO UPDATE` maps wire
            # `uid` -> local `id` in one statement: inserts a fresh local row
            # (self-heals a real one, exactly like `_default_branch`/
            # `_resolve_branch_id`'s own self-heal) the first time this uid
            # is ever seen, and updates that SAME local row -- never a
            # second one -- on every later rename. `local_company_id` is
            # this receiving device's own, never the payload's, identical
            # reasoning to every entity type above. No cash_sessions table
            # is touched anywhere in this branch, by construction -- a new
            # or renamed branch arriving cannot disturb a Phase 4 open cash
            # drawer because there is no code path here that writes to it.
            if event_type not in ("create", "update"):
                return True
            conn.execute(
                "INSERT INTO branches (company_id, name, address, phone, status, uid) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO UPDATE SET "
                "name=excluded.name, address=excluded.address, phone=excluded.phone, status=excluded.status",
                (local_company_id, p.get("name"), p.get("address", ""), p.get("phone", ""),
                 p.get("status", "active"), p.get("uid")),
            )
        elif entity_type == "user":
            # Phase 5 wave B2 stage 2a (docs/launch-readiness/
            # phase5-waveb2-user-sync.md). Only ever reachable from the
            # REGISTRY-configured instance (`conn` is a registry.db
            # connection there -- see `handled_entity_types`'s gate above and
            # REGISTRY_SYNC_ENTITY_TYPES); `users` does not exist in
            # retail.db at all, which is exactly why that gate has to hold.
            #
            # `event_type` outside create/update is silently ignored, same
            # posture as `branch` above -- there is no `delete` case here:
            # `users` uses a soft `deleted_at_utc` tombstone (design §4), and
            # neither that column nor a delete event is in this stage's
            # scope (stage 2a is apply-side only; no write site emits any
            # `user` event yet -- that is stage 2b).
            if event_type not in ("create", "update"):
                return True
            # `uid` -- NOT `id` -- is the wire identity (settled in the
            # design doc, "by reading the code"): registry v3's
            # `idx_users_uid` is a PARTIAL unique index
            # (`ON users(uid) WHERE uid IS NOT NULL`), and the conflict
            # target below repeats that WHERE clause VERBATIM for the exact
            # reason every other partial-indexed table in this file does --
            # SQLite's UPSERT syntax requires it to match the index at all;
            # omitting it raises `OperationalError` on EVERY apply, not just
            # a collision (verified directly against sqlite3, not assumed).
            # A brand-new local `id` is minted here (str(uuid.uuid4())),
            # never the sending device's own `id` -- `users.id` is a
            # private per-device detail (account_schema.py's own docstring:
            # "the local id ... stays a private detail of this install"),
            # identical reasoning to `branches.id` never being carried
            # across the wire.
            #
            # Conflict resolution is `row_version`, NEVER last-write-wins
            # (Decision 2) -- a device that was offline while a password
            # changed elsewhere must not resurrect the old one on
            # reconnect, and a `status='suspended'` set on the admin's till
            # must not be silently undone by a till that has not heard
            # about it yet. The `WHERE excluded.row_version >
            # users.row_version` clause on the DO UPDATE is what enforces
            # this: a lower OR EQUAL incoming row_version makes the whole
            # UPDATE a no-op (proved directly against sqlite3: SQLite does
            # not touch a single column when an UPSERT's own WHERE
            # evaluates false) -- not an error, not a quarantine, just
            # nothing happening. This has no effect on the INSERT half
            # (a genuinely new uid always lands; there is no local row to
            # be "stale" against).
            #
            # Exactly the named allowlist below is ever written -- never
            # `SELECT *`, never `excluded.*` -- so a future column added to
            # `users` cannot silently start replicating cross-device
            # (Decision 3). Three columns are DELIBERATELY absent from both
            # the column list and the SET clause, each for a stated reason,
            # never "forgotten":
            #   * `clinic_role` -- a Clinic-owned column; Clinic is out of
            #     scope for features and must not regress from a Retail
            #     sync stream writing it.
            #   * `failed_login_count`, `locked_until` -- device-local
            #     security state, not a shared fact. A lockout describes
            #     THIS device being attacked; a stale row arriving from an
            #     idle till syncing this user's OTHER fields must not clear
            #     a live lockout mid-attack (ENGINEERING.md Sec1(4)'s named
            #     failure shape: a legitimately-stored value arriving
            #     through a normal path and defeating the guard it sits
            #     behind).
            # `session_version` is likewise absent from the plain allowlist
            # -- it is the revocation counter Phase 5's prerequisites bump
            # to force re-login, and must NEVER move downward regardless of
            # delivery order. Applied as `MAX(local, incoming)` instead of a
            # plain `excluded.session_version` -- COALESCE'd against NULL on
            # both sides (matching the `COALESCE(row_version, 1)+1` idiom
            # `onboarding_routes.py`/`user_accounts.py` already use for this
            # exact column) purely as defence against a legacy/hand-built
            # row that somehow has no value yet; every real write site
            # always supplies one. `local_company_id` is this receiving
            # device's own, never the payload's -- identical reasoning to
            # every other entity type in this file.
            uid = p.get("uid")
            try:
                conn.execute(
                    "INSERT INTO users (id, company_id, uid, employee_id, email, role, status, "
                    "require_password_change, language, password_hash, pin_hash, row_version, "
                    "updated_at_utc, session_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid) WHERE uid IS NOT NULL DO UPDATE SET "
                    "email=excluded.email, employee_id=excluded.employee_id, role=excluded.role, "
                    "status=excluded.status, require_password_change=excluded.require_password_change, "
                    "language=excluded.language, password_hash=excluded.password_hash, "
                    "pin_hash=excluded.pin_hash, row_version=excluded.row_version, "
                    "updated_at_utc=excluded.updated_at_utc, "
                    "session_version=MAX(COALESCE(users.session_version,0), COALESCE(excluded.session_version,0)) "
                    "WHERE excluded.row_version > users.row_version",
                    (str(uuid.uuid4()), local_company_id, uid, p.get("employee_id"), p.get("email"),
                     p.get("role", "cashier"), p.get("status", "active"),
                     p.get("require_password_change", 1), p.get("language", "en"),
                     p.get("password_hash"), p.get("pin_hash"), p.get("row_version", 1),
                     p.get("updated_at_utc"), p.get("session_version", 1)),
                )
            except sqlite3.IntegrityError as exc:
                # Decision 4 -- THE defect that stopped all sync from all
                # devices permanently in wave A: `ON CONFLICT(uid)` only
                # ever suppresses a collision on the uid partial index. A
                # DIFFERENT unique index (`email`, or `UNIQUE(company_id,
                # employee_id)`) still raises a plain IntegrityError that
                # escapes this statement entirely -- two admins creating
                # the same person on two offline tills produces exactly
                # this, same email, two different uids. Caught BY NAME
                # (matched against SQLite's own constraint-failure message,
                # verified directly against sqlite3 -- "UNIQUE constraint
                # failed: users.email" / "UNIQUE constraint failed:
                # users.company_id, users.employee_id" -- never widened to
                # swallow IntegrityError generally, which wave A
                # deliberately rejected: that would silently park a NOT
                # NULL/FK violation too, converting a real bug into quiet
                # data loss instead of a loud, fixable one). `detail` below
                # names the conflicting email/employee_id -- identifiers, a
                # real operator needs them to resolve a duplicate person --
                # and NEVER `password_hash`/`pin_hash`: SQLite's own
                # constraint message already contains no data values at
                # all (verified directly), and neither `reason` nor
                # `detail` here ever interpolates either credential field.
                msg = str(exc)
                if "users.email" in msg:
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_email",
                        detail=f"email={p.get('email')!r} already registered to a different "
                               f"account locally (incoming uid={uid!r})")
                if "users.company_id, users.employee_id" in msg:
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_employee_id",
                        detail=f"employee_id={p.get('employee_id')!r} already registered under "
                               f"company_id={local_company_id!r} (incoming uid={uid!r})")
                raise
        elif entity_type == "user_permission":
            # Phase 5 wave B2 stage 3 (docs/launch-readiness/
            # phase5-waveb2-user-sync.md, Decision 5). Only ever reachable
            # from the REGISTRY-configured instance, identical reasoning to
            # the `user` branch above -- `user_permissions` does not exist
            # in retail.db at all.
            #
            # An event_type outside create/update/delete is silently
            # ignored -- same posture as every other branch's "unrecognised
            # shape" fallback.
            if event_type not in ("create", "update", "delete"):
                return True
            # THE TRAP (see this module's own docstring above, and
            # user_accounts._queue_user_permission_sync_event's docstring):
            # `user_permissions.user_id` is a LOCAL `users.id`, minted
            # FRESH per device by the `user` branch's own INSERT above --
            # never the wire identity. The payload therefore never carries
            # `user_id`, only the owning user's `uid`, and it MUST be
            # resolved to THIS device's own local `users.id` before a
            # single byte is written -- exactly the way `sale_item` resolves
            # `sale_uid` via `_local_id_by_uid` for its own parent. Getting
            # this wrong -- applying to the wrong local user, or to "the
            # only user around" when the real owner has not arrived yet --
            # is a SILENT PRIVILEGE CHANGE: the single most dangerous shape
            # in this entire wave, and it would raise nothing. An
            # unresolvable uid is QUARANTINED, never guessed at and never
            # dropped, exactly like sale_item's own missing parent.
            user_uid = p.get("user_uid")
            subsystem = p.get("subsystem")
            local_user_id = self._local_id_by_uid(conn, "users", user_uid)
            if local_user_id is None:
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:user",
                    detail=f"user_uid={user_uid!r} not found locally")
            # A malformed payload with no subsystem at all (unreachable from
            # any real write site -- every one of them supplies it) is not a
            # "missing parent that might resolve later" case, so there is
            # nothing to gain from quarantining it for a retry that can
            # never fix a payload shape. `subsystem` is NOT NULL in the
            # schema, so writing NULL here would raise IntegrityError and
            # wedge the whole batch -- skipped instead, same posture as an
            # unrecognised event_type just above.
            if not subsystem:
                return True
            if event_type == "delete":
                # Design Decision 4 -- a revoke must travel, or a device
                # that already granted this (user, subsystem) pair keeps it
                # forever after another device revoked it: the worst
                # outcome this wave names. A row that does not exist
                # locally (already applied, never arrived, or this
                # receiver never had it to begin with) is a NO-OP, never an
                # error -- matching the module's general "absent row on
                # delete is fine" posture.
                conn.execute(
                    "DELETE FROM user_permissions WHERE user_id=? AND subsystem=?",
                    (local_user_id, subsystem),
                )
                return True
            # create/update both upsert identically (design Decision 2):
            # the conflict target is `UNIQUE(user_id, subsystem)` using the
            # LOCALLY RESOLVED `local_user_id` above -- never the payload's
            # own `id` (there isn't one in the payload at all; see
            # _queue_user_permission_sync_event's docstring for why this
            # table's `id` "means nothing on the wire"). A fresh local `id`
            # is minted here on insert, identical reasoning to the `user`
            # branch minting a fresh local `users.id`.
            #
            # Plain last-write-wins, NOT `row_version`-gated (design
            # Decision 5): `user_permissions` carries no version column,
            # and adding one is out of scope for this wave. Two admins
            # editing the SAME user's SAME subsystem on two devices at the
            # same moment can resolve either way -- documented as a known,
            # accepted residual risk (this product's single-admin model,
            # rare/deliberate permission edits, and the fact that every
            # permission change already bumps `session_version`, forcing a
            # re-login through which the admin sees the resulting state),
            # never silently.
            conn.execute(
                "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, subsystem) DO UPDATE SET access_level=excluded.access_level",
                (str(uuid.uuid4()), local_user_id, subsystem, p.get("access_level", "none")),
            )
        return True

    @staticmethod
    def _local_id_by_uid(conn, table: str, uid: Optional[str]):
        """Resolves a Phase 5 money-moving parent's LOCAL autoincrement `id`
        from its wire `uid` -- the receiving device's own row, never the
        sending device's local id (which means nothing here). Returns None
        both when `uid` itself is falsy (a malformed/absent payload field)
        and when no local row carries it yet (the genuine orphan case this
        phase's third hazard is about) -- callers treat both identically.
        `table` is always one of the two literal strings this module passes
        ("sales", "returns"), never external input, so the f-string below
        carries no injection risk despite not being parameterized -- SQLite
        has no way to parameterize a table name at all."""
        if not uid:
            return None
        row = conn.execute(f'SELECT id FROM "{table}" WHERE uid=?', (uid,)).fetchone()
        return row["id"] if row else None

    @staticmethod
    def _row_exists(conn, table: str, row_id) -> bool:
        """Does `table` hold a row with this PRIMARY KEY id, on THIS device?

        Unlike `_local_id_by_uid` above, no remapping is involved: `products`
        is one of the pre-existing synced entity types whose own `id` IS its
        cross-device wire identity (see the module docstring), so a pulled
        line item's `product_id` is already the value this device would use --
        the only question is whether the row has ARRIVED yet.

        Exists so a missing one can be PARKED rather than left to raise a real
        FK violation and wedge the batch; see the sale_item branch's own
        comment for the failure it closes. `table` is always a literal string
        this module passes ("products"), never external input, so the f-string
        carries no injection risk -- SQLite cannot parameterize a table name.
        A falsy `row_id` returns False (nothing to find), matching
        `_local_id_by_uid`'s own posture on an absent key; callers that must
        distinguish "absent" from "missing" check for None themselves before
        calling."""
        if not row_id:
            return False
        return conn.execute(
            f'SELECT 1 FROM "{table}" WHERE id=?', (row_id,)
        ).fetchone() is not None

    @staticmethod
    def _resolve_branch_id(conn, local_company_id: Optional[str], branch_uid: Optional[str],
                            fallback_sink: Optional[list] = None) -> Optional[int]:
        """Resolves a pulled sale/return's branch to one of THIS device's OWN
        local `branches.id` values (AUDIT-032C, launch-readiness Phase 5B) --
        never the sending device's raw integer, which means nothing here.
        `branches.id` is a plain per-device autoincrement; `branches` sits in
        RETAIL_UID_TABLES for its `uid` column alone, but `branch` is
        deliberately NOT one of Phase 5's five synced entity types (wave B,
        see the module docstring), so no row bearing this exact uid is
        guaranteed to exist locally at all, let alone under the same integer
        id the sending device used.

        Two tiers:

          1. Look up `branch_uid` (the payload's `branches.uid`, carried by
             the emitting device -- see create_sale/create_return's own
             comment) against THIS device's own `branches` table, scoped to
             `local_company_id` for the same reason every other lookup in
             this file is. When it resolves, the pulled row lands under the
             CORRECT physical branch on this device.
          2. Otherwise -- `branch_uid` absent (an older emitter, or a
             hand-built payload in a test), or present but no local row
             carries it (the two devices' branch rows were never
             reconciled, which is always true today since `branch` itself
             never syncs) -- fall back to THIS device's own default/first
             branch, self-healing one if none exists yet at all. Identical
             rule to `_default_branch()` in retail_api.py, duplicated here
             rather than imported: this module is shared with any future
             product wiring a SyncService (clinic does not today) and must
             never hardcode one product's route-file import.

        Falling back is logged at WARNING when a real (non-empty)
        `branch_uid` was actually discarded to reach it -- so a pulled sale
        silently filed under the wrong branch leaves a visible trail rather
        than a silent guess (the phase brief's explicit DEFECT 3
        requirement). Never logged when `branch_uid` was already absent --
        an absent uid is an older emitter or a hand-built test payload, not a
        resolution FAILURE, so it is not worth a warning.

        Be clear-eyed about the volume this implies, because an earlier
        revision of this docstring got it backwards. `branch_uid` is NOT
        ordinarily absent: create_sale/create_return resolve and send it on
        EVERY sale and EVERY return. And tier 1 ordinarily does NOT resolve,
        because `branch` is not a synced entity type (wave B) -- device A's
        "Main Branch" and device B's "Main Branch" are separate rows with
        separate uids that were never reconciled. So on a plain two-till shop
        the steady state is: every pulled sale takes tier 2 and logs one
        WARNING. Measured directly -- two devices, each with only its own
        self-healed Main Branch, three sales pushed from A: three warnings on
        B, all three rows filed under B's own default branch.

        That is the correct behaviour (there is no better answer available
        until branch rows reconcile) and the visibility is deliberate, but it
        was originally a per-pulled-sale WARNING, not a rare one -- and on a
        busy multi-device install (measured: ~5,000 such events/day/device)
        that steady per-row volume is the same as no log: nobody reads a
        WARNING line repeated thousands of times a day. `fallback_sink`
        below exists to fix exactly that, without losing the information --
        see its own paragraph.

        Neither the fallback ITSELF nor its visibility is what changed here
        -- a silent wrong-branch filing is exactly what DEFECT 3 was, and
        cross-device branch attribution is still effectively "the receiving
        device's default branch" until `branch` itself syncs (a reason for
        wave B to make branch reconciliation real, unrelated to logging
        volume).

        WAVE B UPDATE: `branch` now IS one of the synced entity types (see
        this module's docstring and `_apply_event`'s `branch` elif branch).
        Everything above this paragraph describes the PRE-wave-B steady
        state and is left standing because it explains WHY the fallback
        exists and WHY it was worth quieting, not because it is still an
        accurate description of a healthy two-till shop today. Once a
        device's own branch row has been pushed and pulled at least once
        (ordinary background sync, no operator action required), tier 1
        above resolves for every subsequent pulled sale/return/
        inventory_movement naming that same branch_uid, and the tier-2
        fallback returns to being the genuinely rare case it reads as here:
        a batch that arrives before the branch event does (replay/ordering),
        or a payload from an emitter that predates wave B. Proved directly:
        two devices, A creates a branch and rings a sale on it, both synced
        to B in the same pull batch (branch event ordered before the sale
        event -- see `read_outbox`'s own rowid-ordering docstring) --
        tier 1 resolves on the very first pull, zero fallback warnings.

        `fallback_sink`, when given a list, gets the discarded `branch_uid`
        appended to it INSTEAD OF this function logging anything itself --
        the caller (`apply_pull_result`, via `_apply_event`) collects one
        list per BATCH and logs a single consolidated WARNING once, after
        every event in the batch (including retried quarantine rows) has
        been applied, rather than once per row. `fallback_sink=None` (the
        default) preserves the original per-call WARNING -- every existing
        direct caller of this function (and of `_apply_event` outside
        `apply_pull_result`, e.g. tests) keeps its old behavior unchanged."""
        if branch_uid:
            row = conn.execute(
                "SELECT id FROM branches WHERE uid=? AND company_id=?",
                (branch_uid, local_company_id),
            ).fetchone()
            if row:
                return row["id"]
        row = conn.execute(
            "SELECT id FROM branches WHERE company_id=? ORDER BY id LIMIT 1",
            (local_company_id,),
        ).fetchone()
        if row is not None:
            default_id = row["id"]
        else:
            # No branch at all yet on this device (never onboarded, or a
            # bare test fixture) -- self-heal exactly like _default_branch()
            # does, with the same fresh v13 `uid` a self-healed branch gets
            # there, so it is named identically on the wire however it came
            # into existence.
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                (local_company_id, "Main Branch", "", "", str(uuid.uuid4())),
            )
            default_id = cur.lastrowid
        if branch_uid:
            if fallback_sink is not None:
                # Same three facts a per-row WARNING would have carried --
                # the discarded uid, the company scope, and where the row
                # actually landed -- just deferred to the batch-level
                # summary instead of logged immediately.
                fallback_sink.append((branch_uid, local_company_id, default_id))
            else:
                logger.warning(
                    "sync: pulled row's branch_uid=%r did not resolve to any local branch for "
                    "company_id=%r; filed under this device's default branch id=%r instead.",
                    branch_uid, local_company_id, default_id,
                )
        return default_id

    @staticmethod
    def _quarantine_apply_event(conn, ev: dict, reason: str, detail: str) -> bool:
        """Parks a Phase 5 money-moving child event in `sync_apply_quarantine`
        instead of dropping it or letting an FK violation raise and wedge the
        whole pull batch -- see schema.py's `CREATE TABLE sync_apply_quarantine`
        for the full reasoning (the client-side twin of Owner's own push-side
        quarantine, 681b0fa). `INSERT OR IGNORE` keyed on `(entity_id,
        event_type)` -- NOT `entity_id` alone, see that CREATE TABLE's own
        comment for why -- makes re-parking the SAME still-blocked event on
        every retry tick a no-op rather than an ever-growing pile of
        duplicate rows -- `quarantined_at` on an already-parked row therefore
        stays at the FIRST time it was seen, which is exactly what "how long
        has this been stuck" should show. Always returns False -- the event
        was NOT applied -- so `_apply_event`'s callers can tell this case
        apart from a genuine success."""
        conn.execute(
            "INSERT OR IGNORE INTO sync_apply_quarantine "
            "(entity_id, entity_type, event_type, payload, reason, detail) VALUES (?,?,?,?,?,?)",
            (ev.get("entity_id"), ev.get("entity_type"), ev.get("event_type"),
             json.dumps(ev.get("payload") or {}), reason, detail),
        )
        return False

    @staticmethod
    def _is_missing_table_error(exc: sqlite3.OperationalError) -> bool:
        return "no such table" in str(exc).lower()

    def _has_quarantined_events(self, conn) -> bool:
        """False both when the table is genuinely empty AND when it does not
        exist at all yet. The second case is real, not hypothetical: this
        method is called on EVERY `apply_pull_result()`, including from test
        fixtures (and, in principle, a not-yet-upgraded production database)
        that hand-build a minimal schema with only the tables their own
        scenario needs -- `sync_apply_quarantine` is additive and created
        unconditionally by `init_retail()`'s base executescript for every
        REAL install, but a fixture that never calls `init_retail()` at all
        should not be forced to know this table exists purely because Phase
        5 added an unconditional check here. `OperationalError` is caught
        narrowly (message-matched), not swallowed wholesale -- a locked
        database or genuine corruption must still raise."""
        try:
            row = conn.execute("SELECT 1 FROM sync_apply_quarantine LIMIT 1").fetchone()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return False
            raise
        return row is not None

    def _retry_quarantined_events(self, conn, local_company_id: Optional[str] = None,
                                   branch_fallback_sink: Optional[list] = None) -> None:
        """Re-attempts every row currently parked in `sync_apply_quarantine`,
        oldest first, and deletes exactly the ones that resolve this pass.
        Safe to call every `apply_pull_result()` -- an empty quarantine table
        is a cheap SELECT and an empty loop; a row that is STILL blocked is
        re-parked by `_quarantine_apply_event` (INSERT OR IGNORE onto its own
        PRIMARY KEY) rather than duplicated, so this is idempotent to call as
        often as convenient. `local_company_id` is only actually consumed by
        the "sale"/"return"/"payment" branches inside `_apply_event`; a batch
        of purely still-orphaned sale_items would never need it, but the
        caller (`apply_pull_result`) already resolves it eagerly whenever the
        quarantine table is non-empty at all -- see that method's comment.

        Missing-table tolerant for the identical reason `_has_quarantined_
        events` is -- see that method's docstring.

        `branch_fallback_sink`, when given, is forwarded to `_apply_event`
        unchanged -- a retried quarantined sale/return can ALSO fall back to
        this device's default branch, and that fallback belongs in the same
        one-per-batch summary `apply_pull_result` logs for its own loop, not
        a second, separate WARNING."""
        try:
            rows = conn.execute(
                "SELECT entity_id, entity_type, event_type, payload FROM sync_apply_quarantine "
                "ORDER BY quarantined_at"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return
            raise
        for row in rows:
            ev = {
                "entity_id": row["entity_id"],
                "entity_type": row["entity_type"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]),
            }
            if self._apply_event(conn, ev, local_company_id, branch_fallback_sink=branch_fallback_sink):
                # Keyed on BOTH columns -- entity_id alone would risk
                # deleting a DIFFERENT still-blocked event_type that happens
                # to share this entity_id (see the table's own CREATE
                # comment for why the primary key itself is composite).
                conn.execute(
                    "DELETE FROM sync_apply_quarantine WHERE entity_id=? AND event_type=?",
                    (row["entity_id"], row["event_type"]),
                )

    def run_once(self) -> None:
        """The one entry point the timer tick (and the manual/CLI caller)
        uses. Never lets a relay/network failure escape -- an unreachable or
        rejecting relay is an expected, ordinary condition (offline device),
        not a reason to crash the host app. The next scheduled tick (or the
        next route-triggered nudge) simply retries.

        push and pull are each wrapped in their OWN try/except -- found via
        a real test (test_run_once_swallows_push_failure_and_still_attempts_
        pull), not by inspection: a single shared try/except around both
        calls (the shape this originally mirrored from the brief's own
        draft) means a push failure -- which is not always "offline"; it can
        be a real, persistent RelayRejected against one bad outbox row
        (e.g. a malformed event a human has to go fix) -- would silently
        skip pull() forever until that one row is resolved. This device
        should keep receiving OTHER devices' updates regardless of whether
        its own outbox is currently able to drain."""
        try:
            self.push_once()
            # An empty outbox returns early without contacting the relay --
            # still recorded as success: push health means "nothing is stuck
            # in the outbox", and the pull half below is what detects a real
            # outage on a device that happens to have nothing to send.
            self._record_sync_success("push")
        except Exception as exc:
            failures = self._record_sync_failure("push", exc)
            logger.error(
                "Sync push FAILED (reason=%s, consecutive failures=%d); the outbox was "
                "left untouched and will be retried on the next tick.",
                self._failure_reason(exc), failures, exc_info=True,
            )
        try:
            self.pull_once()
            self._record_sync_success("pull")
        except Exception as exc:
            failures = self._record_sync_failure("pull", exc)
            logger.error(
                "Sync pull FAILED (reason=%s, consecutive failures=%d); the local cursor "
                "was not advanced and the same range will be re-pulled on the next tick.",
                self._failure_reason(exc), failures, exc_info=True,
            )

    @staticmethod
    def _fresh_half_health() -> dict:
        return {
            "healthy": True, "last_success_at": None, "last_failure_at": None,
            "last_failure_reason": None, "consecutive_failures": 0,
        }

    @staticmethod
    def _failure_reason(exc: BaseException) -> str:
        """Short, human-safe failure label -- NEVER str(exc): a requests
        NetworkError message embeds the full relay URL. SyncRelayClientError
        subclasses already carry the exact reason_code Owner returned."""
        return getattr(exc, "reason_code", None) or type(exc).__name__

    def _record_sync_success(self, half: str) -> None:
        with self._health_lock:
            h = self._health[half]
            h["healthy"] = True
            h["consecutive_failures"] = 0
            h["last_success_at"] = datetime.now(timezone.utc).isoformat()

    def _record_sync_failure(self, half: str, exc: BaseException) -> int:
        with self._health_lock:
            h = self._health[half]
            h["healthy"] = False
            h["consecutive_failures"] += 1
            h["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            h["last_failure_reason"] = self._failure_reason(exc)
            return h["consecutive_failures"]

    def _pending_outbox_count(self) -> int:
        """Rows currently sitting in `sync_outbox`, not yet relayed to
        Owner -- feeds the frontend's calm "Synced Ns ago -- N pending"
        state (app-shell.js's _renderSyncCalmState(), feat/sync-freshness-
        indicator). read_outbox()/ack_outbox() above already establish the
        invariant this relies on: a row lives in sync_outbox from the
        moment a route commits it (Task 4's category/product/customer/
        supplier/reorder_request routes) until push_once() confirms Owner
        accepted it and deletes exactly that row -- so the table holds
        ONLY unsynced writes. There is no status column on sync_outbox at
        all (see schema.py's `CREATE TABLE sync_outbox`), so a plain
        COUNT(*) is already exactly "pending"; no WHERE clause needed."""
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS n FROM sync_outbox").fetchone()
            return row["n"] if row else 0
        finally:
            conn.close()

    def get_health(self) -> dict:
        with self._health_lock:
            push = dict(self._health["push"])
            pull = dict(self._health["pull"])
        return {
            "configured": True,
            "running": self._timer is not None and not self._stopped.is_set(),
            "healthy": push["healthy"] and pull["healthy"],
            "interval_seconds": self._interval_seconds,
            "retry_interval_seconds": self._current_interval_seconds,
            # feat/sync-freshness-indicator: last_success_at was already
            # present on push/pull below but the frontend previously threw
            # it away (consecutive_failures/last_failure_reason only) -- no
            # indicator rendered at all while sync was healthy. pending_count
            # is the other half of the calm-state label; read fresh on every
            # call (not cached) so it's always current as of this request.
            "pending_count": self._pending_outbox_count(),
            "push": push,
            "pull": pull,
        }

    def _next_interval_seconds(self) -> float:
        with self._health_lock:
            failures = max(
                self._health["push"]["consecutive_failures"],
                self._health["pull"]["consecutive_failures"],
            )
        if failures == 0:
            return self._interval_seconds
        exponent = min(failures - 1, _BACKOFF_MAX_EXPONENT)
        delay = self._interval_seconds * (_BACKOFF_MULTIPLIER ** exponent)
        jitter = delay * _BACKOFF_JITTER_FRACTION * secrets.randbelow(100) / 100.0
        return min(delay + jitter, _BACKOFF_MAX_SECONDS)

    def start(self, interval_seconds: float = 10.0) -> None:
        self._interval_seconds = interval_seconds
        self._stopped.clear()
        self._schedule_next(interval_seconds)  # first tick always at base interval

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: Optional[float] = None) -> None:
        """Recomputes the delay from current health on EVERY reschedule (the
        interval is no longer captured once in start()) -- this is the whole
        backoff mechanism. A nudge() that succeeds resets the failure counter
        but deliberately does NOT cancel/reschedule the pending timer: doing
        so from the nudge thread would race _tick()'s own _schedule_next()
        call. Worst case is one extra backoff-capped wait (5 min) after
        recovery before the shorter interval takes effect."""
        if self._stopped.is_set():
            return
        delay = self._next_interval_seconds() if interval_seconds is None else interval_seconds
        self._current_interval_seconds = delay

        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next()

        self._timer = threading.Timer(delay, _tick)
        self._timer.daemon = True
        self._timer.start()


# ── Module-level "nudge" registration ───────────────────────────────────────
# Task 4's category routes call nudge() right after committing a row into
# sync_outbox, so a push happens as close to immediately as possible rather
# than waiting for the next 10s timer tick -- without giving retail_api.py
# (or any other route module) a direct import-time dependency on however
# app.py happened to construct the SyncService (base URL, signer,
# installation id, ...). app.py calls register_active_service() once, at
# import time, only when SYNC_RELAY_BASE_URL is configured; when it is not,
# nothing ever registers and nudge() is a no-op.
_active_service: Optional[SyncService] = None


def register_active_service(service: SyncService) -> None:
    global _active_service
    _active_service = service


def unregister_active_service() -> None:
    global _active_service
    _active_service = None


def get_active_health() -> dict:
    """Health of the currently registered SyncService, or
    {"configured": False} when none is registered -- mirroring nudge()'s own
    "no-op when inert" precedent exactly. Returns "not configured" on two
    distinct real installs: SYNC_RELAY_BASE_URL unset (app.py never
    constructs a service), and Android (app.py deliberately never calls
    register_active_service() there -- Kotlin's SyncCoordinator owns
    Android's push/pull loop and its own health state)."""
    service = _active_service
    if service is None:
        return {"configured": False}
    return service.get_health()


def nudge() -> None:
    """Best-effort, non-blocking push attempt. Safe to call unconditionally
    from a route handler: a no-op when no service is registered (sync
    inert), and never lets a slow/unreachable relay add latency to the
    caller's HTTP response -- push_once() runs on its own short-lived daemon
    thread, with any failure swallowed exactly like a normal missed timer
    tick (the next scheduled run_once() retries)."""
    service = _active_service
    if service is None:
        return

    def _push():
        try:
            service.push_once()
            service._record_sync_success("push")
        except Exception as exc:
            failures = service._record_sync_failure("push", exc)
            logger.error(
                "Sync nudge push FAILED (reason=%s, consecutive failures=%d); the "
                "next timer tick will retry.",
                service._failure_reason(exc), failures, exc_info=True,
            )

    threading.Thread(target=_push, daemon=True).start()
