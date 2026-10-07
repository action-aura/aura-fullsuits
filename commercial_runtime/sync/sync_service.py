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
    silently drop the movement's effect. Since launch-readiness Phase 7
    stage 7d-i (retail v20, `stock_exceptions`), the SAME `if cur.rowcount:`
    block also checks the resulting balance and records/refreshes an OPEN
    `stock_exceptions` row when it is negative -- detection only for THIS
    branch: nothing here relaxes `create_sale`'s own oversell refusal or
    auto-resolves an exception once recorded. (Stage 7d-iii later made
    `create_sale`'s OWN refusal conditional and gave it its own writer into
    the same table -- see `_record_or_refresh_stock_exception`'s own
    CORRECTION paragraph and products/retail/backend/database/schema.py's
    `record_or_refresh_stock_exception` for the full split; this branch's
    behaviour is unchanged by that.) See `_record_or_refresh_stock_
    exception` and that branch's own comment.
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

Schema-version skew guard (2026-09-22, retail-hardware-viewports): the hole
this closes was found the hard way at retail schema v39 -- three columns
(`products.avg_cost`, `sale_items.unit_cost`, `sales.points_redeemed_amount`)
were put on the wire before every apply branch below knew their names, and a
device still running the pre-v39 apply code applied those events anyway,
silently dropping the unknown columns (`p.get(...)` simply never asked for
them). That specific gap is now closed (see each branch's own v39 comment),
but the STRUCTURAL hole is not: nothing anywhere in this file, before this
paragraph, ever compared versions at all. A device on schema v38 and a device
on v39 would sync "happily" forever, each apply silently losing whatever
columns the receiving build's code had never heard of, with no error, no log
line, and no failing test -- exactly the "two replicas quietly disagree
forever" shape this file otherwise goes to great lengths to avoid (see
`_record_sync_conflict`, `_quarantine_apply_event`: this codebase has never
tolerated a SILENT discard anywhere else in this apply path).

WHAT IS COMPARED, and why not a hardcoded retail constant: this module is
shared with Clinic (`RETAIL_SYNC_ENTITY_TYPES` above is retail's OWN entity
set; a Clinic-configured `SyncService` would carry a different one), so
`RETAIL_SCHEMA_VERSION` (products/retail/backend/database/schema.py) can
never be imported here directly -- doing so would be the identical layering
violation `local_ensure_schema`/`stock_exception_recorder`'s own docstrings
already refuse for exactly this reason. The version compared is therefore
whatever `local_schema_version_provider` (new constructor parameter, __init__
docstring below) returns -- a single opaque, product-supplied integer
representing "the version of THIS device's own apply logic for the entity
types it handles". No SEPARATE sync-protocol version was added alongside it:
the wire ENVELOPE itself (id/entity_type/event_type/payload/created_at,
`_changed_fields`) has always evolved compatibly -- an unrecognised
`entity_type` is skipped (see the "Two-stream design" section above), a
missing `_changed_fields` key means "legacy, full snapshot" (see
`_delta_set_clause`) -- so there is nothing today a second version axis would
protect that a single one does not already cover. A product that ever needs a
compound version is free to have its provider return a tuple (Python compares
tuples lexicographically, and the parser below only ever needs `>`/`<=`); YAGNI
argues against inventing that shape before a real second axis exists.

WHICH DIRECTION IS REFUSED: only the LOSSY one. An event whose sender's
version is STRICTLY GREATER than this device's own is refused -- that is
exactly the v39 incident above, an older build applying a newer build's row
and silently dropping columns it was never taught to write. An event whose
sender's version is LESS THAN OR EQUAL is applied normally, unchanged from
today: a newer device pulling an older device's row cannot lose data it never
had a column for (`p.get(col, default)` already handles a payload's genuine
absence of a column this build knows about -- see each branch's own comment),
so gating that direction too would only turn "usually fine" into "never
syncs", which is a worse outcome than the bug this guard exists to close.

HOW A REFUSAL SURFACES: the SAME visible, replayable queue this file already
uses for "cannot apply yet" (`sync_apply_quarantine`, see
`_quarantine_apply_event`) -- never a silent drop, and never `sync_conflicts`
(that table means "a write LOST to a newer one on the SAME schema"; this is
"cannot safely evaluate this write at all on the CURRENT schema", a different
claim). Reason codes `schema_version_too_new` (a real, resolved skew) and
`schema_version_unverifiable` (either side's version could not be trusted --
see the fail-closed paragraph below) are distinct so an operator reading
`sync_apply_quarantine.reason` can tell "my build is behind" from "something
is sending garbage" at a glance. `apply_pull_result` also logs one
consolidated WARNING per batch (`_log_schema_version_skew_summary`, mirroring
`_log_sync_conflict_summary`/`_log_branch_fallback_summary` immediately above
it) rather than one line per row, and `get_health()` surfaces a live count
(`schema_version_quarantine_count`) through the SAME dict the frontend's calm
sync-state indicator already reads -- so a shop stuck mid-rollout has a
number to look at, not just a log file.

UPGRADE ORDERING / NOT BRICKING A SHOP MID-ROLLOUT: the whole guard is
OFF -- not merely permissive, entirely inert, byte-for-byte today's behavior
-- for a `SyncService` built with no `local_schema_version_provider` at all,
which is every existing call site (products/retail/backend/app.py wires
neither Windows nor Android instance with one yet; that wiring, and the
Clinic-side equivalent, is deliberately left to whoever owns those files
next, not this change) and every test in this suite that predates this
guard. Once a product DOES wire the provider in, a till running the new
build will refuse (loudly, retryably -- see `_retry_quarantined_events`,
which re-attempts every parked row, including these, on every later pull) to
apply anything from a sibling till it cannot positively verify is not ahead
of it -- INCLUDING a sibling that has not yet been upgraded to a build that
stamps its own version at all, per the fail-closed rule immediately below.
That is a deliberate, accepted cost: the two degrade to "these two don't
sync with each other until both are updated", loudly and specifically, which
is the point -- not a fleet-wide outage, not silent data loss, and it heals
itself the moment the older till is upgraded too (no operator action beyond
that upgrade is needed; the next pull tick on the newer till re-attempts the
parked rows and they apply cleanly once versions line up).

FAIL-CLOSED ON UNPARSEABLE/ABSENT/GARBAGE, matching
`commercial_runtime/einvoicing/settings.py::is_enabled()`'s own documented
discipline (only an exact, trusted literal is ever accepted as an opt-in;
everything else -- absent, unrecognised, corrupt -- resolves to the SAME
"not proven safe" outcome, never a guess): `_parse_schema_version` trusts
ONLY a genuine non-negative `int` (explicitly not `bool`, which is an `int`
subclass in Python and must never silently compare as 0/1; not a numeric
string, no `int(...)` coercion, no leniency) from EITHER side of the
comparison. A payload with no `_sender_schema_version` key at all -- an event
already sitting in an outbox/relay when a device upgrades to carry this
guard, or one from a sibling that has not been upgraded to stamp it yet -- is
therefore indistinguishable, on purpose, from a payload carrying a corrupted
or hostile value: both parse to `None`, and `None` on either side refuses
the event (`schema_version_unverifiable`) rather than assuming "no stamp
must mean old, therefore safe". Deliberately the OPPOSITE of this same
file's OWN row_version convention for a legacy catalogue write (`_delta_set_
clause`'s "changed is None -> full snapshot", the category branch's "missing
row_version -> treat as legacy, still applies") -- those precedents exist for
a DIFFERENT question ("has this exact row been fully described"), answerable
safely by assuming "everything changed". "Is this row safe for MY code to
apply at all" is not answerable that way: guessing wrong in that direction is
exactly how the v39 incident happened, so this guard does not extend the
lenient convention to itself.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Iterable, NamedTuple, Optional

# `gl_journal`'s apply-side balance check (the `_apply_event` branch below,
# BOOKS-WAVE1 W1 §2.5) needs the SAME quantum table `general_ledger.py`'s own
# `_gl_epsilon`/`quantize_money` read -- imported from here, never from
# `products/retail/backend/core/retail/general_ledger.py` itself, because
# `commercial_runtime` must not depend on either product (see currency.py's
# own module docstring: "commercial_runtime cannot import products/, nothing
# in here may depend on either product"). The balance MATH is therefore
# necessarily duplicated rather than shared -- same layering trade-off
# `general_ledger.py`'s own `_gl_epsilon` already documents for its
# `api.retail_api._money_epsilon` twin -- see `_validate_gl_journal_lines`'s
# own docstring below for the parity this buys.
from commercial_runtime.currency import DEFAULT_BASE_CURRENCY, currency_quantum
from commercial_runtime.stock_cost import BLENDING_MOVEMENT_TYPES, blended_unit_cost

# Registry v9 (Track S): the pure/apply rules for the `user` v9 keys and the
# `device` / `admin_device` entities live next to the tables they qualify in
# commercial_runtime/identity (crew R); the sync engine only CALLS them, so the
# payload keys and the rules for reading them are edited together. Neither
# module imports the sync engine, so there is no cycle.
from commercial_runtime.identity.device_registry import (
    MAX_ADMIN_EPOCH,
    apply_admin_assignment,
    apply_device_event,
)
from commercial_runtime.identity.user_accounts import (
    ROLE_ADMIN,
    enforce_cash_pair_after_apply,
    normalize_role,
    sanitize_synced_user_fields,
    user_sync_apply_plan,
)

# AUDIT-033 / S-AUTH: signed authority events. The module imports `cryptography`
# only lazily inside the functions that verify or sign, so this import is as
# Android-safe as the identity imports above.
from commercial_runtime.sync import authority as _authority

logger = logging.getLogger(__name__)

#: How long a production `AuthorityEnv` (read from app data) is reused. See
#: `SyncService._authority_env`.
_AUTHORITY_ENV_TTL_SECONDS = 5.0

# What a registry-stream payload can make identity's helpers (or the bind to
# SQLite) raise WITHOUT the database itself being unhealthy. See
# `SyncService._apply_event_guarded`. OperationalError is absent on purpose.
_ISOLATED_APPLY_ERRORS = (
    ValueError, OverflowError, TypeError,
    sqlite3.IntegrityError, sqlite3.DataError,
    sqlite3.InterfaceError, sqlite3.ProgrammingError,
)

# Largest `admin_device` epoch a peer may announce; see the branch in
# `_apply_event` for why the ceiling exists.
# ONE constant, owned by identity (device_registry.MAX_ADMIN_EPOCH): this engine's
# pre-check and identity's own bound used to be two literals (2**31-1 here, 2**31
# there) and disagreed on exactly one value. The alias keeps the name the
# row_version/epoch bounds below already use.
_MAX_SYNCED_ADMIN_EPOCH = MAX_ADMIN_EPOCH

# ---------------------------------------------------------------------------
# Schema v42 branch-uid helpers.
#
# `database/schema.py` owns the canonical versions of these (this module must
# not import `products/`), so the three below are deliberate TWINS, pinned to
# the schema's by a parity test (test_branch_alias_till_allocation_apply.py).
# All of them NEVER raise: an apply path that can throw on a missing alias
# table would take a whole sync round down with it.
# ---------------------------------------------------------------------------
_DEFAULT_BRANCH_UID_NAMESPACE = uuid.UUID("6f1d0c52-8b3e-4f7a-9c41-2a5e7d90b3c8")
_ALIAS_MAX_HOPS = 4
_ALIAS_UID_MAX_LEN = 64
_TILL_STATUSES = frozenset({"active", "released", "superseded"})
# Twin of `database.schema.PLACEHOLDER_BRANCH_NAMES` (parity-tested): the two
# names an auto-created default branch carries. A branch event that reaches the
# canonical row THROUGH an alias may only carry one of them (P2S M3b).
_PLACEHOLDER_BRANCH_NAMES = ("Main Branch", "Main Store")


def _default_branch_uid(license_id: Any) -> Optional[str]:
    """The one deterministic uid every device derives for a shop's placeholder
    branch from the licence id alone. None for an empty id."""
    lid = str(license_id or "").strip()
    if not lid:
        return None
    return str(uuid.uuid5(_DEFAULT_BRANCH_UID_NAMESPACE, "aura-retail:default-branch:" + lid))


def _owner_licence_id() -> Optional[str]:
    """The Owner-issued licence id of THIS install, or None. Lazy import, and a
    module-level wrapper so tests can replace it (the real one reads the real
    app-data directory)."""
    try:
        from commercial_runtime.identity.company_rebind import owner_issued_company_id
        value = owner_issued_company_id()
    except Exception:  # noqa: BLE001 - "unknown" is a valid answer here
        return None
    value = str(value or "").strip()
    return value or None


def _canonical_branch_uid(conn: sqlite3.Connection, uid: Any, company_id: Any) -> Any:
    """Follow `branch_uid_aliases` (at most `_ALIAS_MAX_HOPS`) to the canonical
    uid. Returns `uid` unchanged when there is no alias table, no alias, or on
    any sqlite error. Company-scoped by string comparison; a None company is
    unscoped, exactly like schema.canonical_branch_uid (parity-tested)."""
    if not uid or not isinstance(uid, str):
        return uid
    current = uid
    try:
        for _ in range(_ALIAS_MAX_HOPS):
            if company_id is None:
                row = conn.execute(
                    "SELECT canonical_uid FROM branch_uid_aliases WHERE alias_uid = ?",
                    (current,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT canonical_uid FROM branch_uid_aliases "
                    "WHERE alias_uid = ? AND CAST(company_id AS TEXT) = ?",
                    (current, str(company_id)),
                ).fetchone()
            nxt = row[0] if row else None
            if not nxt or nxt == current:
                break
            current = nxt
    except sqlite3.Error:
        return uid
    return current


def _normalise_branch_uid_refs(conn: sqlite3.Connection, company_id: Any) -> None:
    """Rewrite every stored reference to an alias uid so it names the canonical
    one: `doc_series.branch_uid`, the `branch_code:<uid>` setting key, and the
    `branch_uid` inside each `numbering_till:%` JSON value. EMIT-FREE (no
    sync event, no row_version bump): every device gets the alias and derives
    the same rewrite, so echoing it would only spin. Twin of the schema's."""
    cid = str(company_id if company_id is not None else "")
    try:
        aliases = conn.execute(
            "SELECT alias_uid, canonical_uid FROM branch_uid_aliases "
            "WHERE CAST(company_id AS TEXT) = ?", (cid,)).fetchall()
        if not aliases:
            return
        for alias_uid, canonical_uid in aliases:
            if not alias_uid or not canonical_uid or alias_uid == canonical_uid:
                continue
            conn.execute(
                "UPDATE doc_series SET branch_uid = ? "
                "WHERE branch_uid = ? AND CAST(company_id AS TEXT) = ?",
                (canonical_uid, alias_uid, cid))
            conn.execute(
                "UPDATE retail_settings SET skey = ? "
                "WHERE skey = ? AND CAST(company_id AS TEXT) = ? "
                "AND NOT EXISTS (SELECT 1 FROM retail_settings n "
                "                WHERE n.skey = ? AND CAST(n.company_id AS TEXT) = ?)",
                ("branch_code:" + canonical_uid, "branch_code:" + alias_uid, cid,
                 "branch_code:" + canonical_uid, cid))
        amap = {a: c for a, c in aliases if a and c and a != c}
        rows = conn.execute(
            "SELECT skey, svalue FROM retail_settings "
            "WHERE skey LIKE 'numbering_till:%' AND CAST(company_id AS TEXT) = ?",
            (cid,)).fetchall()
        for skey, svalue in rows:
            try:
                state = json.loads(svalue)
            except (TypeError, ValueError):
                continue
            if isinstance(state, dict) and state.get("branch_uid") in amap:
                state["branch_uid"] = amap[state["branch_uid"]]
                conn.execute(
                    "UPDATE retail_settings SET svalue = ? WHERE skey = ? "
                    "AND CAST(company_id AS TEXT) = ?",
                    (json.dumps(state), skey, cid))
    except sqlite3.Error:
        logger.warning("branch alias normalisation failed", exc_info=True)


_BACKOFF_MULTIPLIER = 2.0
_BACKOFF_MAX_SECONDS = 300.0
_BACKOFF_JITTER_FRACTION = 0.2
_BACKOFF_MAX_EXPONENT = 32

# Launch-readiness Phase 7 stage 7c-i (docs/launch-readiness/
# phase7-offline-ux.md, "DO block: receiving a purchase order"): the ONE
# server-side value for "how stale is too stale to trust a locally-scoped
# guard" -- receive_purchase_order's double-receive check reads `status`
# from THIS device's own database, and PO status is never synced, so once
# this device has been behind by more than this many seconds it can no
# longer see whether another device already received the same PO.
#
# Deliberately the SAME NUMBER as the frontend's own single source of truth
# (products/retail/frontend/subsystem-retail.js's
# RetailSystem.SYNC_STALE_THRESHOLD_SECONDS, which stage 7b already uses to
# decide when the POS tile's stock figure is stale). There is no shared
# runtime between this Flask/Python backend and the vanilla-JS frontend to
# hold one literal for both languages, so this is the Python half of that
# one conceptual threshold -- change both together if it ever moves, and
# never let a second Python copy of this number exist; every server-side
# staleness decision imports THIS constant.
SYNC_STALE_THRESHOLD_SECONDS = 30 * 60

# Launch-readiness Phase 7 stage 7c-ii (docs/launch-readiness/
# phase7-offline-ux.md "Decision 2"; ROADMAP.md's 2026-08-28 "retail schema
# v19" entry): the threshold beyond which `create_sale` (retail_api.py)
# blocks NEW SALES behind a manager override, gated on CAP_CASH_APPROVE.
#
# BACKEND-ONLY, unlike SYNC_STALE_THRESHOLD_SECONDS just above -- there is
# deliberately no frontend counterpart and no entry added to
# retail_sync_threshold_parity_test.py for this constant. Decision 3
# ("enforced in the handler, explained in the UI") means the Flask route
# handler is the sole authority here; the UI only has to render whatever
# plain-language refusal the handler already returns, and needs no local
# copy of 72 hours to decide anything client-side the way the frontend's
# own SYNC_STALE_THRESHOLD_SECONDS decides when the POS tile stops stating
# a stock figure. If a client-side pre-emptive warning is ever added ahead
# of this constant's own frontend counterpart, extend that parity test to
# cover it too -- do not let a second silent copy of "72 hours" exist.
SYNC_SALES_STOP_THRESHOLD_SECONDS = 72 * 60 * 60

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
    # Shop-level settings (currency, tax mode, credit defaults, business day,
    # branding text) -- see retail_api.py's _queue_setting_sync_event.
    "retail_setting",
    # Aseel-parity wave A-PAR (schema v32) -- "cheque" is the IMMUTABLE
    # header, "cheque_event" the APPEND-ONLY transition log; see the
    # `_apply_event` branches below and core/retail/cheques.py's own module
    # docstring for why this pair carries no row_version / reject-stale gate
    # at all (both are append-only sets, converging by plain set union).
    "cheque", "cheque_event",
    # Aseel-parity wave A-PAR (schema v33) -- "quotation" is the ONLY entity
    # type this feature adds; there is no "quotation_line" type. UNLIKE
    # cheques, this IS a mutable header (draft -> sent -> accepted/declined
    # -> converted, or -> cancelled) and DOES carry row_version -- see the
    # `_apply_event` branch below and database/schema.py's
    # `_migrate_add_sales_quotations` docstring's "DRAFTS DO NOT SYNC"
    # section for why a create-only `sales_quotation_lines` table with no
    # delete/replace machinery is still correct: `sent` is immutable, so the
    # frozen line set travels ONCE, embedded in the `create` event's own
    # payload, and every later event is an `update` carrying header fields
    # only.
    "quotation",
    # BOOKS-WAVE1 W1 (schema v41) -- "gl_journal" is the ONLY entity type
    # this wave adds. It carries one already-posted `gl_journals` header
    # plus its balanced `gl_journal_lines` set, keyed by the ORIGINATING
    # device's own `uid` -- see `_apply_event`'s branch below for the full
    # apply-side contract (validate-before-write, `_resolve_branch_id`'s
    # null-stays-null carve-out, account seeding by code parents-first,
    # `ON CONFLICT(uid) ... DO NOTHING` gated the same HAZARD-1 way
    # `inventory_movement` above is). UNLIKE every other entity type in
    # this set, the RECEIVING device never derives or re-computes this
    # fact from anything else it has pulled -- see the plan's Decision 1,
    # "(a) Sync the journals", for why re-deriving from pulled sales/
    # returns/purchase-orders was rejected. Append-only, like `cheque`/
    # `cheque_event`/`inventory_movement` above: there is no `gl_journal`
    # "update" or "delete" event, ever -- a mistake is corrected by posting
    # a NEW reversing journal (`reversed_journal_uid`), never by rewriting
    # this one, enforced at the database level by `gl_journals`'s own
    # BEFORE UPDATE trigger (database/schema.py's
    # `_migrate_add_gl_journal_sync`).
    "gl_journal",
    # Aseel-parity wave A-PAR (schema v34) -- "doc_series" is the
    # DEFINITION only (id, doc_type, code, label, branch_uid,
    # allocator_terminal_uid, ...); the COUNTER it drives
    # (`doc_series_counter.last_no`) is device-local and never rides the
    # wire at all -- see database/schema.py's `_migrate_add_doc_series`
    # docstring for why that is structurally correct rather than merely
    # "not implemented yet". Same argument that forced `retail_setting`
    # into this set on 2026-09-05 ("one licence, two devices, and the
    # phone showed $ while the desktop showed JD"): a till that cannot see
    # another till's books cannot render a coherent numbering list, cannot
    # tell an owner which till a number came from, and cannot offer
    # "move this book here" when the owning till dies. A MUTABLE header
    # (label/status/allocator all change after creation), so -- like
    # `quotation`, unlike `cheque`/`cheque_event` -- it DOES carry
    # `row_version` and a reject-stale gate; see the `_apply_event` branch
    # below for the tie-break this table needs that `quotation` does not
    # (two offline devices can legitimately race to claim the SAME
    # unclaimed book, which two devices editing the same quotation cannot).
    "doc_series",
    # Track S / schema v42 -- two small company-scoped facts that must reach
    # every till of a shop:
    #   * "branch_alias": "this old branch uid is the same branch as that
    #     canonical one" (the default-branch convergence). Append-only, keyed
    #     on the alias uid; a receiver applies it emit-free.
    #   * "till_allocation": which till NUMBER a terminal holds in a branch,
    #     handed out only by the main computer. Two mains can race, so the
    #     apply carries a deterministic clash rule (see the branch below).
    # Both need `company_id` on the wire, so neither belongs in
    # `_ENTITY_TYPES_WITHOUT_COMPANY_ID`. A peer still on v41 does not know
    # them and parks them via the schema-skew gate (`schema_version_too_new`).
    "branch_alias",
    "till_allocation",
    # Schema v44 -- "party_balance_adjustment": the ONLY entity type v44 adds.
    # A customer's / supplier's balance is DERIVED from the shop's synced
    # sales/payments/returns/purchase orders (core/retail/party_balance.py);
    # this append-only fact carries what those rows cannot explain -- the
    # balance a single-computer shop already had when it upgraded -- so every
    # computer derives the same figure. CREATE-ONLY, keyed on `uid` (a fixed
    # uuid5 for the upgrade carry, so two computers that both carried the same
    # party collapse into one fact): there is no update or delete, ever. Needs
    # `company_id` (the receiver stamps ITS OWN), so it is NOT in
    # `_ENTITY_TYPES_WITHOUT_COMPANY_ID`. A v43 peer does not know the type and
    # parks it via the unknown-type schema-skew gate (`schema_version_too_new`).
    "party_balance_adjustment",
    # Schema v45 (W4a) -- purchase orders shared between computers. Two types:
    # "purchase_order" (the header: a `create`, then `update`s that only ever
    # carry {uid, status, received_at}) and "purchase_order_item" (one
    # create-only line, keyed on its own uid, parent named by `po_uid`).
    # Before W4a a PO lived only on the computer that typed it, so a sibling
    # could not see, receive or cancel it, and a sibling's instalment pointed
    # at an UNRELATED local PO through the writer's integer id. The header
    # needs `company_id` (the receiver stamps ITS OWN), so it is NOT in
    # `_ENTITY_TYPES_WITHOUT_COMPANY_ID`; the line is (parent by uid, no
    # company column). A v44 peer does not know either type and parks them via
    # the same unknown-type schema-skew gate (`schema_version_too_new`) as the
    # v41/v42/v44 types above -- never eats them. Also new on the wire, under
    # the existing "payment" type: a `po_uid` key on `create`, and a
    # `po_link` event_type that attaches a pre-v45 payment to its PO.
    "purchase_order",
    "purchase_order_item",
    # Schema v46 (W4a-2) -- a DELIVERY is an immutable fact: a
    # "purchase_order_receipt" header (value, date, line count; parent named by
    # `po_uid`) and one create-only "purchase_order_receipt_line" per line
    # (parent by `receipt_uid`, item by `po_item_uid`). The order's received
    # quantities and status are DERIVED from them, so two computers that
    # received different parts of one order offline converge on the same
    # state. The header needs `company_id` (receiver stamps its own); the line
    # has none (see `_ENTITY_TYPES_WITHOUT_COMPANY_ID`). A v45 peer does not
    # know either type and parks them via the unknown-type schema-skew gate.
    "purchase_order_receipt",
    "purchase_order_receipt_line",
    # Schema v47 (W3b) -- stock transfers shared between computers. Two types,
    # both METADATA: "stock_transfer" (the header: a `create` naming the two
    # branches by uid, then `update`s carrying the state and its stamps) and
    # "stock_transfer_item" (a `create` for the line, then `update`s carrying
    # the two facts a send and a receive learn: unit_cost, quantity_received).
    # Before W3b the movements and their 1250/5910 journals synced but the
    # header never did, so a transfer sent on one computer could not be
    # received on another. Applying either type NEVER moves stock and NEVER
    # posts books -- those travel only as inventory_movement / gl_journal
    # events, so a transfer is never counted twice. The header needs
    # `company_id` (the receiver stamps its own); the line has none (see
    # `_ENTITY_TYPES_WITHOUT_COMPANY_ID`). A v46 peer does not know either
    # type and parks them via the unknown-type schema-skew gate.
    "stock_transfer",
    "stock_transfer_item",
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
#: `device` and `admin_device` joined in Track S / registry v9: the shop's
#: device list and the "main computer" assignment now converge over sync
#: (`devices` / `admin_devices` in registry.db). Both need the receiver's own
#: `company_id` (the payloads carry none), so they stay OUT of
#: `_ENTITY_TYPES_WITHOUT_COMPANY_ID`.
REGISTRY_SYNC_ENTITY_TYPES = frozenset({"user", "user_permission", "device", "admin_device"})

#: `retail_settings` keys that are DEVICE-LOCAL and so are refused (skipped,
#: logged once per key) when a `retail_setting` event names them on apply.
#: Literals are retail_api.py's, hardcoded because sync_service must not
#: import retail_api -- the two must match (a test pins the match).
#:
#: Chosen by enumerating every `retail_settings` writer in retail_api.py
#: (2026-09-29): every writer except one goes through
#: `_queue_setting_sync_event` (shop-wide, correct to apply), so only that one
#: is listed:
#:   * `numbering_till:<terminal_uid>` (`_TILL_STATE_KEY_PREFIX`) -- this
#:     till's own document-number counter, written by `_write_local_setting`
#:     with emit deliberately off.
#: NOT listed, on purpose: `branch_code:<branch uid>` (`_BRANCH_CODE_KEY_PREFIX`)
#: is SHOP-WIDE and synced -- refusing it would break numbering across tills;
#: `blob_*` is refused by its own older guard; `commercial_runtime`'s
#: e-invoicing and notification settings live in their own tables, not here.
_DEVICE_LOCAL_SETTING_PREFIXES = ("numbering_till:",)

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
#: "cheque_event" joined this set in the Aseel-parity wave A-PAR (schema
#: v32) -- `cheque_events` carries no `company_id` column of its own
#: (scoped entirely through `cheque_id`, the `cash_movements`/
#: `stock_transfer_items` shape -- see database/schema.py's
#: `_migrate_add_cheques` docstring), so it is exactly the same shape as
#: `sale_item`/`return_item` above: resolves its parent by id, has nothing
#: of its own for `apply_pull_result`'s eager company fetch to stamp, and a
#: `SyncService` wired with no `local_company_id_provider` at all would
#: raise for no reason on a batch of only cheque_events if this entry were
#: missing.
#: "purchase_order_item" (W4a, schema v45) is the same shape: `purchase_order_
#: items` has no `company_id` column and resolves its parent by `po_uid`.
#: "purchase_order_receipt_line" (W4a-2, schema v46) likewise: `purchase_order_
#: receipt_lines` has no company column; its parent receipt carries it.
#: Purchase-order entity types whose apply reads lazily-created columns (see the
#: `local_ensure_schema` gate in `apply_pull_result`).
_PO_ENSURE_ENTITY_TYPES = frozenset({
    "purchase_order", "purchase_order_item",
    "purchase_order_receipt", "purchase_order_receipt_line",
})

#: "stock_transfer_item" (W3b, schema v47) likewise: `stock_transfer_items` has no
#: company column; its parent header carries it (the apply looks the parent up
#: under the receiver's own company, so a line can never attach across tenants).
#:
#: W3b D11 -- NOT added to `_PO_ENSURE_ENTITY_TYPES` / the `local_ensure_schema`
#: gate: applying a transfer or a line touches only `stock_transfers` and
#: `stock_transfer_items`, whose every column (v47's included) is created by
#: schema.py's migration chain, never lazily by retail_api. Nothing here reads
#: `payments` or `retail_settings`, the lazily-created things that gate exists for.
_ENTITY_TYPES_WITHOUT_COMPANY_ID = frozenset(
    {"sale_item", "return_item", "cheque_event", "purchase_order_item",
     "purchase_order_receipt_line", "stock_transfer_item"})

#: W4a-2: the namespace `core/retail/purchase_orders.py` mints its uuid5 uids
#: under. Needed here ONLY to synthesize the LEGACY receipt a v45 writer's
#: {status:'received'} header update stands for -- every computer must derive
#: the identical uid or the same delivery would count twice. commercial_runtime
#: cannot import products/, so the literal is duplicated and a test pins the
#: two equal.
_PO_RECEIPT_NAMESPACE = uuid.UUID('a674e3be-c149-497a-b0a7-f235e22c5ae0')

#: W4a-2 verify R2: quantities are REAL columns but are compared as DECIMALS
#: built from str(value) -- exactly what core/retail/purchase_orders.py does --
#: with NO slack. The old float SUM + 1e-6 slack was a second implementation of
#: the same rule that could disagree with the core (and 1 - (0.1+0.2) in floats
#: refused the last 0.7 of a fractional order). One rule, two copies, one table
#: test (retail_po_receipts_apply_test) holding them equal.
def _po_received_by_item(conn, company_id, po_id) -> dict:
    out = {}
    for row in conn.execute(
            "SELECT l.po_item_id, l.quantity FROM purchase_order_receipt_lines l "
            "JOIN purchase_order_receipts r ON r.id = l.receipt_id "
            "WHERE r.company_id=? AND r.po_id=?", (company_id, po_id)).fetchall():
        out[row[0]] = out.get(row[0], Decimal("0")) + Decimal(str(row[1] or 0))
    return out

#: W4a: a purchase order's header status only ever moves FORWARD through this
#: rank when merged from a peer. "received" outranks "cancelled" on purpose:
#: stock has physically arrived and been booked, which a cancel cannot undo --
#: so when two computers disagree, the receipt wins and the disagreement is
#: recorded as a conflict for a human (see the `purchase_order` apply branch).
#: Twin of `core/retail/purchase_orders.py`'s `PO_STATUS_RANK` (commercial_
#: runtime cannot import products/); a test pins the two equal.
PO_STATUS_RANK = {"pending": 0, "cancelled": 1, "received": 2}

#: W3b: a stock transfer's state only ever moves FORWARD through this rank when
#: merged from a peer. It is a function of the (status, recalled_at) PAIR, not
#: of status alone, because 'cancelled' means two things: cancelled before it
#: was ever sent (goods never left) and recalled after it was sent (goods came
#: back). A recall outranks the send it undoes; a plain cancel does not.
#: "received" outranks everything: the goods physically arrived and were booked.
#: Twin of `core/retail/stock_transfers.py`'s `transfer_rank` (commercial_runtime
#: cannot import products/); a test pins the two equal over every state.
_TRANSFER_RANK_PENDING = 0
_TRANSFER_RANK_CANCELLED_BEFORE_SEND = 1
_TRANSFER_RANK_IN_TRANSIT = 2
_TRANSFER_RANK_RECALLED = 3
_TRANSFER_RANK_RECEIVED = 4

#: The reasons a transfer merge records (once per transfer). Duplicated from the
#: core module for the same reason; the same test pins them equal.
TRANSFER_CONFLICT_REASONS = frozenset({
    "transfer_sent_after_cancel", "transfer_received_and_recalled",
    "transfer_sent_twice", "transfer_received_twice"})


def transfer_state_rank(status, recalled_at=None) -> int:
    """Merge rank of a transfer's (status, recalled_at); raises ValueError for
    a status this build does not know so a garbage event cannot out-rank real
    state (the apply catches it and ignores the status)."""
    if status == "pending":
        return _TRANSFER_RANK_PENDING
    if status == "in_transit":
        return _TRANSFER_RANK_IN_TRANSIT
    if status == "received":
        return _TRANSFER_RANK_RECEIVED
    if status == "cancelled":
        return _TRANSFER_RANK_RECALLED if recalled_at else _TRANSFER_RANK_CANCELLED_BEFORE_SEND
    raise ValueError(f"unknown transfer status {status!r}")


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


class SyncFreshnessStore(NamedTuple):
    """Optional `SyncService` collaborator (launch-readiness Phase 7 stage
    7a, docs/launch-readiness/phase7-offline-ux.md "FINDING 1"): persists
    `last_success_at` for both halves so the elapsed-since-last-sync clock
    survives an app restart, instead of resetting to None every launch the
    way `self._health` always has (`_fresh_half_health`).

    Optional and a no-op when absent -- default `None` on `SyncService.
    __init__`'s `local_freshness_store` parameter, checked explicitly,
    exactly like `local_ensure_schema` -- and deliberately NOT implemented
    as a try/except around a missing table. `SyncService` is constructed
    more than once in products/retail/backend/app.py (a retail.db instance
    and a registry.db instance; Android wires a second retail.db instance
    too), and only retail.db carries the `sync_freshness` table
    (products/retail/backend/database/schema.py, v18) -- the registry
    database is at its own schema version and out of scope for this table.
    Catching `sqlite3.OperationalError: no such table` to mean "not
    configured here" would be indistinguishable from catching a REAL schema
    fault on a retail database that failed to migrate -- ENGINEERING.md's
    rule against error sentinels that can also arrive legitimately applies
    here exactly as it does everywhere else in this codebase. So absence is
    expressed by passing `None` for this parameter (which the registry
    construction site does), never inferred from a caught exception.

    Every callable receives a connection `SyncService` already opened via
    its own `get_conn` -- exactly like `local_ensure_schema` receives one
    instead of opening its own -- so this collaborator never manages a
    connection of its own. `load` is called once, at construction, to seed
    `self._health` with whatever was last durably recorded; `record` is
    called from `_record_sync_success` on every recorded success, with
    `half` always exactly `'push'` or `'pull'`.

    `record_override` (launch-readiness Phase 7 stage 7c-ii, schema v19,
    docs/launch-readiness/phase7-offline-ux.md "Decision 2") -- persists a
    manager's approval to keep selling past the 72-hour hard stop, called
    from `record_offline_override` below when the /sync/offline-override
    route (retail_api.py) accepts one. Extends this SAME collaborator
    rather than opening a second one, for the identical reason `load`
    already returns `override_at` alongside `push`/`pull` instead of a
    separate lookup: the validity rule compares the override timestamp
    against the most recent success timestamp, and both live in the ONE
    `sync_freshness` row a single connection reads and writes."""
    load: Callable[[sqlite3.Connection], dict]
    record: Callable[[sqlite3.Connection, str, str], None]
    record_override: Callable[[sqlite3.Connection, str], None]


#: (entity_type, entity_id) pairs already warned about for a quarantine flood, so
#: the WARNING is one line per entity per process, not one per dropped event.
_FLOOD_WARNED: set = set()


class SyncService:
    def __init__(
        self,
        client_factory: Callable[[], "SyncRelayClient"],
        get_conn: Callable[[], "sqlite3.Connection"],
        local_company_id_provider: Optional[Callable[[], Optional[str]]] = None,
        local_ensure_schema: Optional[Callable[["sqlite3.Connection"], None]] = None,
        handled_entity_types: Optional[Iterable[str]] = None,
        local_freshness_store: Optional[SyncFreshnessStore] = None,
        stock_exception_recorder: Optional[Callable[..., None]] = None,
        local_schema_version_provider: Optional[Callable[[], Optional[int]]] = None,
        on_tick: Optional[Callable[[], None]] = None,
        authority_env_provider: Optional[Callable[[], "_authority.AuthorityEnv"]] = None,
    ):
        """`authority_env_provider` (AUDIT-033 / S-AUTH): zero-argument callable
        returning an `authority.AuthorityEnv` -- the receiver's Owner trust
        store, its own licence id, its own signer and device id. `None` (the
        default, and what production uses) resolves them from app data on every
        batch (`authority.default_authority_env`). It exists so a test can hand
        in a throwaway world; it is NOT a bypass: there is no setting that
        switches signature checking off, and an env with no trust store parks
        every authority event.

        `on_tick`, when supplied, is called once at the START of every
        scheduled tick (before `run_once()`), so work that must recur on the
        service's own cadence can ride its timer instead of growing a second
        thread. Its one production user is the registry stream's device
        HEARTBEAT (products/retail/backend/app.py): without it a till that
        stays up for a week never re-announces itself and every peer's
        Computers list shows it as silent. It is called on EVERY tick and must
        decide for itself whether anything is due (the announcer does). It can
        never break sync: an exception is logged and swallowed, and
        `run_once()` still runs. Called before, not after, `run_once()` so an
        event it queues rides THIS tick's push rather than the next one. `None`
        (the default) is exactly the pre-existing behaviour.

        `client_factory` is called fresh on every push_once()/pull_once()
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
        a running instance after construction.

        `local_freshness_store`: see `SyncFreshnessStore`'s own docstring
        just above this class for the full reasoning (why it exists, why it
        is optional, why absence must never be inferred from a caught
        exception). In short: when supplied, its persisted values are
        loaded into `self._health` LAZILY, on first use (see
        `_ensure_freshness_loaded`, called from `get_health()` and
        `_record_sync_success()`), so a restarted process reports the SAME
        `last_success_at` the previous one recorded instead of resetting to
        None; and `_record_sync_success` writes through to it on every
        recorded success. `None` (the default) reproduces this class's
        entire pre-Phase-7 behavior exactly: in-memory only, reset on every
        restart -- which is what the registry `SyncService` construction
        site still gets, deliberately, since registry.db has no
        `sync_freshness` table.

        `stock_exception_recorder`, when supplied, is called from the
        `inventory_movement` apply branch's `_record_or_refresh_stock_
        exception` (below) the moment a MERGED balance lands negative --
        launch-readiness Phase 7 stage 7d-i (retail v20, `stock_
        exceptions`; docs/launch-readiness/phase7-offline-ux.md "Decision
        4"). Shaped like `local_ensure_schema` above, not like
        `local_freshness_store`: a single bare callable, not a NamedTuple
        of several, because there is only ever one operation here (upsert
        the one open row for this key) rather than a load/record pair.
        Deliberately a CONSTRUCTOR HOOK, not a hardcoded import of
        anything under `products/retail` -- this module is shared with
        Clinic, and `stock_exceptions` is a retail-only table (exactly the
        same reasoning `local_ensure_schema`'s own paragraph above gives
        for not hardcoding retail's lazy-migration function by name).
        Optional and a no-op when absent, checked explicitly at the call
        site, NEVER inferred from a caught exception -- identical rule to
        `local_freshness_store`'s own paragraph above, for the identical
        reason. `None` in production only at the registry `SyncService`
        construction site (products/retail/backend/app.py), which is safe
        by construction rather than by luck: `inventory_movement` is not
        in `REGISTRY_SYNC_ENTITY_TYPES`, so that instance's apply loop
        never reaches the branch that would call this at all. The retail
        and Android construction sites both pass products/retail/backend/
        database/schema.py's `record_or_refresh_stock_exception` -- see
        that function's own docstring for the full upsert reasoning and
        for its SECOND caller (stage 7d-iii, `create_sale` itself).

        Deliberately NOT loaded here, inside `__init__`, even though that
        is where it conceptually belongs: `SyncService` is constructed at
        products/retail/backend/app.py's MODULE IMPORT time (the module-
        level `if _SYNC_RELAY_URL_IS_USABLE ...:` block), which runs BEFORE
        `init_app()` -- defined later in the same file -- ever calls
        `init_retail()`, the migration that creates `sync_freshness` on a
        fresh install. Reading the table inside `__init__` therefore raises
        `sqlite3.OperationalError: no such table: sync_freshness` on every
        fresh install, reproduced against a real app boot
        (retail_sync_starts_when_configured_test.py), not found by
        inspection -- the exact same shape of trap `local_ensure_schema`
        above already exists to design around for a different table, just
        arriving here even earlier (at construction, not first pull tick).
        Deferring the load to first USE, after `init_app()` has certainly
        run (`get_health()` is only ever reached by a live Flask route;
        `_record_sync_success` only ever runs from `run_once()`, itself
        only reachable via the timer `.start()` schedules inside
        `init_app()`, AFTER `init_retail()`), sidesteps the ordering problem
        without resorting to a try/except around the missing table -- which
        would be indistinguishable from swallowing a real migration
        failure, the exact error-sentinel shape ENGINEERING.md warns
        against.

        `local_schema_version_provider`, when supplied, is THIS device's own
        opt-in to the schema-version skew guard (module docstring's
        "Schema-version skew guard" section has the full design and the
        fail-closed reasoning -- read that first, this is the constructor-
        level summary only). Called fresh wherever it is needed, never
        cached -- same freshness discipline as `client_factory` above, and
        unlike `local_company_id_provider` there is no I/O-avoidance reason
        to fetch it only once per batch (a product's provider is expected to
        be `lambda: SOME_MODULE_LEVEL_INT_CONSTANT`, not a query), so
        `read_outbox()`/`_apply_event` each call it directly rather than
        threading a pre-fetched value through every signature the way
        `local_company_id` is threaded.

        `None` (the default) makes the ENTIRE guard inert -- not lenient,
        inert: `read_outbox()` stamps nothing onto pushed events and
        `_apply_event` never even evaluates the gate, so a `SyncService`
        built this way is byte-for-byte today's pre-guard behavior. This is
        every existing call site as of this parameter's introduction
        (products/retail/backend/app.py wires neither its Windows nor its
        Android instance with one yet -- that wiring belongs to whoever owns
        app.py next) and every pre-existing test in this suite. A product
        opts in by passing e.g. `lambda: RETAIL_SCHEMA_VERSION` (that
        constant lives in products/retail/backend/database/schema.py, which
        this shared module must never import directly -- see the module
        docstring's "WHAT IS COMPARED" paragraph)."""
        self._client_factory = client_factory
        self._get_conn = get_conn
        self._local_company_id_provider = local_company_id_provider
        self._local_ensure_schema = local_ensure_schema
        self._local_schema_version_provider = local_schema_version_provider
        self._on_tick = on_tick
        self._authority_env_provider = authority_env_provider
        self._authority_unsigned_logged = False
        self._authority_env_cache = None
        self._local_freshness_store = local_freshness_store
        self._stock_exception_recorder = stock_exception_recorder
        # See _ensure_freshness_loaded's own docstring for why this is a
        # lazy, first-use flag rather than work done inline below.
        self._freshness_loaded = False
        self._handled_entity_types = (
            frozenset(handled_entity_types) if handled_entity_types is not None
            else RETAIL_SYNC_ENTITY_TYPES
        )
        # W4a-2 / D13: the retail core's "re-derive this order's received
        # quantities, status and paid figure" function, installed by
        # retail_api.install_po_state_refresh (this module must not import
        # products/). Signature: hook(conn, company_id, po_id). When None the
        # built-in twin (`_refresh_po`) runs, so an instance wired without the
        # retail API still derives state.
        self.po_state_refresh: Optional[Callable] = None
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
            # Launch-readiness Phase 7 stage 7c-ii: a sibling of "push"/
            # "pull", not nested under either -- the override is a single
            # device-level fact, not a per-half one. None here means "no
            # standing override", the correct default before
            # _ensure_freshness_loaded has ever run and the correct
            # permanent value for a SyncService with no freshness store
            # (the registry construction site), matching that site's
            # existing "last_success_at stays None forever" behaviour.
            "offline_override_at": None,
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
        gap).

        Retail schema v31 (ROADMAP.md's 2026-09-14 "the sync cursor must know
        which relay it belongs to" entry): the client is built FIRST, before
        `read_cursor`, specifically so `ensure_cursor_matches_relay` can check
        the cursor against the SAME relay `client.pull()` below is about to
        hit -- not a relay resolved separately (and possibly differently) at
        some other moment. The check-and-maybe-reset sits inside this same
        transaction, ahead of the read, so a reset and the pull's own apply
        either both land in the one `conn.commit()` below or neither does --
        never a reset that commits alone while the pull that follows it
        fails, which would leave the cursor at 0 with nothing applied."""
        with self._lock:
            conn = self._get_conn()
            try:
                client = self._client_factory()
                # `getattr`, not `client.base_url`, because this class's
                # documented contract with its client is exactly `.push(events)`
                # and `.pull(since)` -- see relay_client.py's module docstring.
                # `base_url` is an ENRICHMENT on top of that contract, not part
                # of it: the real SyncRelayClient has it and therefore gets the
                # full protection below, while the several legitimate test
                # doubles built to the documented surface (FakeRelayClient,
                # FileRelay, InMemoryRelay) do not, and must not be forced to
                # grow an attribute they have no business having.
                #
                # A client that cannot say which relay it is talking to lands on
                # `ensure_cursor_matches_relay`'s own explicit "unknown relay ->
                # change nothing" branch, which is the safe direction: resetting
                # on an unknown URL would re-pull the entire history on every
                # single tick. Widening the client contract instead would have
                # been the tail wagging the dog.
                self.ensure_cursor_matches_relay(conn, getattr(client, "base_url", None))
                since = self.read_cursor(conn)
                result = client.pull(since)  # raises on failure
                self.apply_pull_result(conn, result)
                conn.commit()
            finally:
                conn.close()

    def read_outbox(self, conn, limit: Optional[int] = None) -> list:
        """Reads (never deletes) the current sync_outbox rows, in the same
        shape push() expects. Split out of push_once() (multi-device-sync-
        foundation, Task 9) so Android's local `/_internal` sync routes can
        read the batch Kotlin is about to push WITHOUT this Python process
        ever calling `self._client_factory().push()` itself -- Android's
        Python never holds the signing key (see
        commercial_runtime/licensing_contracts/android_bridge_identity.py),
        so Kotlin makes the actual signed HTTP call and this method only
        hands it the rows to sign and send.

        `limit` (F7, BOOKS-WAVE1-PLAN.md decision F7; v2 finding 7):
        `None` (the default, and the ONLY way every existing caller in this
        codebase -- `push_once()` above, and every pre-existing test in this
        suite -- has ever called this method) reads the WHOLE outbox,
        byte-for-byte today's behaviour; `push_once()` already chunks
        what it gets to `_PUSH_CHUNK_SIZE` before ever calling `client.
        push()`, so it has no reason to ask for a bounded page here, and
        this default keeps it, and the Windows push loop it drives, wholly
        unchanged by this parameter's introduction. A positive `limit`
        returns at most that many rows, OLDEST FIRST (the SAME `ORDER BY
        rowid` below -- see that ordering's own reasoning) -- this is what
        `commercial_runtime/sync/internal_routes.py`'s `/_internal/outbox`
        route now passes (capped and defaulted there, never here, so this
        method itself stays a plain, unopinionated page primitive): that
        endpoint used to hand Android's Kotlin the ENTIRE outbox in one
        HTTP response regardless of size -- a one-time historical backfill
        of order 20k events means order 20MB on a phone, in ONE response,
        every tick until it drains. Paging there, not here, is deliberate:
        it isolates the change to the ONE caller (an HTTP endpoint) that
        actually needed a wire-size bound, without touching push_once()'s
        own, already-correct chunking discipline.

        A page smaller than the true backlog is always safe to hand a
        caller that re-reads on its own next tick (rows are never deleted
        here, only `ack_outbox` removes them, and only the ones a caller
        names) -- see `internal_routes.py`'s own docstring for the ONE
        thing this changes for its existing Kotlin caller: SEE THAT FILE'S
        OWN F7 note before assuming a caller's size-of-the-page reading is
        still "the true complete backlog".

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
        be out-ranked by a later insert reusing a freed higher rowid.

        Schema-version skew guard (module docstring's dedicated section):
        when `local_schema_version_provider` is configured and returns a
        value `_parse_schema_version` trusts, every event's PAYLOAD (not a
        new top-level key on the event dict itself) gains a
        `_sender_schema_version` entry stamped with it here, at read time --
        never at write time in `sync_outbox` itself, so a row already
        sitting in the outbox before this device was upgraded to carry a
        provider is stamped with the CURRENT version at the moment it is
        actually sent, which is correct: the code doing the sending, not the
        code that originally queued the row, is what a receiver needs to
        reason about. Inside `payload`, not a sibling key on `ev`, for the
        same reason `_changed_fields` already lives there and not on `ev`:
        `_retry_quarantined_events` reconstructs a quarantined event from
        ONLY `entity_id`/`entity_type`/`event_type`/`payload` (the columns
        `sync_apply_quarantine` actually has) -- any metadata `_apply_event`
        needs to survive a quarantine-and-retry round trip has to live
        inside `payload`, or it is silently lost the first time this exact
        event gets parked. No provider configured, or the provider's return
        value does not parse, means nothing is stamped at all (not a
        fabricated 0) -- an unstamped payload reads on the receiving end
        exactly like one from a sender that never had this guard, which is
        the correct, honest signal: a device that cannot state its own
        version has no business asserting one."""
        sender_version = (
            self._parse_schema_version(self._local_schema_version_provider())
            if self._local_schema_version_provider is not None else None
        )
        query = "SELECT * FROM sync_outbox ORDER BY rowid"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = conn.execute(query, params).fetchall()
        events = []
        signer = None
        signer_resolved = False
        for r in rows:
            payload = json.loads(r["payload"])
            event = {
                "id": r["id"],
                "entity_type": r["entity_type"],
                "entity_id": r["entity_id"],
                "event_type": r["event_type"],
                "payload": payload,
                "created_at": r["created_at"],
            }
            # AUDIT-033 / S-AUTH: AUTHORITY events are signed here, at read time
            # (like the schema stamp, never at write time: a row queued before
            # this device had a signer -- or before an upgrade -- is signed with
            # the CURRENT key and lease at the moment it is actually sent). It
            # happens BEFORE `_sender_schema_version` is stamped, and the stamp
            # is excluded from the signed bytes, so the skew guard can never
            # break a signature. This covers BOTH streams: each `SyncService`
            # reads its own outbox through this method.
            if _authority.is_authority_event(event):
                if not signer_resolved:
                    signer = self._authority_env().signer
                    signer_resolved = True
                if signer is not None:
                    event = _authority.sign_event(event, signer)
                    payload = event["payload"]
                elif not self._authority_unsigned_logged:
                    self._authority_unsigned_logged = True
                    logger.warning(
                        "SyncService: this device cannot sign (no device key, no Owner-signed "
                        "lease, or no local device id yet), so its authority events go out "
                        "UNSIGNED and every up-to-date peer will park them. Logged once.")
            if sender_version is not None:
                payload = dict(payload, _sender_schema_version=sender_version)
                event["payload"] = payload
            events.append(event)
        return events

    def _authority_env(self):
        """The receiver's/sender's authority context, resolved fresh."""
        provider = self._authority_env_provider
        if provider is not None:
            return provider()
        # Production: resolved from app data (trust store file, licence lease in
        # licensing.db, DPAPI key, local device id). A pull page can carry
        # hundreds of authority events, so reuse one resolution for a few
        # seconds rather than re-reading all four per event -- short enough that
        # a rotated trust store or a refreshed lease is picked up within a tick.
        now = time.monotonic()
        cached = self._authority_env_cache
        if cached is not None and now - cached[0] < _AUTHORITY_ENV_TTL_SECONDS:
            return cached[1]
        env = _authority.default_authority_env()
        self._authority_env_cache = (now, env)
        return env

    def _payload_verifier(self, ev: dict):
        """`verify(payload) -> VerifiedSigner | Refusal` for another payload of the
        same entity/event identity, under the receiver's CURRENT trust context. Used
        by the old-shape quarantine fallback (review V3r G3) to ask whether a parked
        row's stored `_auth` actually verifies, instead of reading its unverified
        `inst` field."""
        identity = {k: ev.get(k) for k in ("entity_type", "entity_id", "event_type")}

        def verify(payload, entity_type=None):
            env = self._authority_env()
            ident = dict(identity, entity_type=entity_type) if entity_type is not None else identity
            return _authority.verify_event(
                dict(ident, payload=payload),
                trust_store=env.trust_store, own_licence=env.own_licence)
        return verify

    def _authority_gate(self, conn, ev: dict, p: dict, local_company_id: Optional[str],
                        entity_type: str, from_quarantine: bool = False):
        """AUDIT-033 / S-AUTH: `None` when this authority event is signed by a
        member of THIS licence who is allowed to send this kind of event, else the
        `authority.Refusal` it must be parked with. Verification first (derives
        the sender from the signature), then the signer binding (first binding
        wins, revoked signer refused), then the per-type rule. Fails CLOSED: a
        registry that cannot be read parks the event as `main_unknown` -- unlike
        `_till_registry_assignment`, which accepts unchecked, because that one was
        a bar-raiser and this one is the authentication."""
        env = self._authority_env()
        verdict = _authority.verify_event(ev, trust_store=env.trust_store, own_licence=env.own_licence)
        if isinstance(verdict, _authority.Refusal):
            return verdict
        cid = str(local_company_id or self._get_local_company_id())
        own_reg = entity_type not in REGISTRY_SYNC_ENTITY_TYPES
        reg = None
        try:
            if own_reg:
                from commercial_runtime.identity import registry_db
                reg = registry_db.get_conn()
            else:
                reg = conn
            _authority._ensure_signers_table(reg)
            if env.signer is not None:
                # Nobody may claim THIS device's own id: bind it before any
                # peer's first event is judged.
                _authority.ensure_self_bound(reg, cid, env.signer)
            revoked = reg.execute("SELECT status FROM devices WHERE id=?", (verdict.dev,)).fetchone()
            if revoked is not None and str(revoked[0] or "").lower() == "revoked":
                return _authority.Refusal(
                    _authority.REASON_SIGNER_REVOKED,
                    f"computer {verdict.dev!r} has been removed from this shop")
            clash = _authority.bind_signer(reg, cid, verdict)
            if clash is not None:
                return clash
            ctx = _authority.RuleContext(
                reg=reg, company_id=cid, local_dev=env.local_dev,
                retail=conn if own_reg else None)
            if not from_quarantine:
                # AUDIT-033 fix round (verifier V finding #6): proof of life comes
                # ONLY from an event delivered now, never from re-judging one that
                # was parked and is retried on every pull -- a main's old event
                # sitting in the quarantine would otherwise keep a dead main alive
                # for ever. `record_liveness` additionally ignores a signature it
                # has already counted and a lease older than 14 days. It runs BEFORE
                # the own-echo return below: a main's own echoes keep it alive.
                _authority.record_liveness(reg, verdict)
            # What the post-apply steps may trust about this event (set here, never
            # read from the wire: `_apply_event` clears them on entry).
            main = _authority._current_main(ctx)
            signer_is_main = main is not None and verdict.dev == main
            ev["_authority_signer_dev"] = verdict.dev
            ev["_authority_signer_inst"] = verdict.inst
            ev["_authority_signer_is_main"] = signer_is_main
            # AUDIT-033 fix round (verifier V finding #5): an event is this device's
            # OWN echo only when it was signed by THIS device's own signer -- same
            # device id, installation AND key. Matching the device id alone let any
            # signer who claimed the receiver's id (or, with no local signer at all,
            # anybody) skip every rule. With no local signer nothing is exempt; an
            # echo signed under an old key (a rotation) is judged by the rules.
            own = (env.signer is not None and verdict.dev == env.signer.dev
                   and verdict.inst == env.signer.inst
                   and verdict.fingerprint == hashlib.sha256(
                       base64.b64decode(env.signer.pk_b64)).hexdigest())
            if own:
                # THIS device's own event, handed back by the relay (a relay does not
                # drop a device's own events from its pull). Nobody else can sign as
                # this device, and the applier already treats a replay of its own row
                # as a no-op, so the per-type rules are not re-run: after a hand-over
                # the old main would otherwise park its own history as
                # `needs_main_computer` for ever.
                return None
            refusal = _authority.authority_refusal(ev, verdict, ctx)
            if refusal is not None:
                return refusal
            if (entity_type == "user" and not signer_is_main
                    and _authority.is_harmless_owner_edit(p, verdict, ctx)):
                # Finding #3: a non-main computer's harmless edit of an owner row is
                # applied WITHOUT touching row_version (see the user applier).
                ev["_authority_harmless_owner_edit"] = True
            return None
        except sqlite3.OperationalError as exc:
            logger.warning("SyncService: authority check could not read the registry (%s); parking %s",
                           exc, entity_type)
            return _authority.Refusal(
                _authority.REASON_MAIN_UNKNOWN, f"the device registry could not be read: {exc}")
        finally:
            if own_reg and reg is not None:
                try:
                    reg.commit()
                finally:
                    reg.close()

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

    def ensure_cursor_matches_relay(self, conn, relay_url) -> bool:
        """Retail schema v31 (ROADMAP.md's 2026-09-14 "the sync cursor must
        know which relay it belongs to" entry). `sync_cursor.last_seq` is a
        high-water-mark into ONE relay's own sequence space -- Owner's cloud
        relay counts per licence in Postgres, a LAN hub counts independently
        in its own SQLite `site_sync_events` -- so a cursor carried over from
        a relay switch is a number from the wrong universe: a till at seq 500
        against the cloud, re-pointed at a hub whose own log is at seq 12,
        would ask for `since=500` forever and receive nothing, with no error
        anywhere. This is the guard that closes that: called BEFORE every
        pull (see `pull_once`, which calls this ahead of `read_cursor` inside
        the SAME transaction) to reset the cursor the moment the effective
        relay stops matching the one it was last read against.

        Returns True when it reset the cursor, False when it left it alone --
        callers that only care about the log line above already exists don't
        need the return value, but the mutation-proof tests in
        retail_sync_cursor_relay_binding_test.py assert on it directly.

        `relay_url` blank or None means "the caller does not know which relay
        this is" (a pull path that has not been taught to pass one yet, or a
        transient failure resolving it) -- this does NOTHING and returns
        False rather than resetting. Fail SAFE toward not resetting: treating
        an unknown URL as "different" would reset on EVERY tick for any
        caller that never supplies one, which would re-pull the entire
        history over and over instead of the one-time catch-up this guard is
        meant to cost.

        A NULL/absent stored `relay_url` (every row written before v31, or a
        fresh row from `INSERT OR IGNORE ... VALUES (1, 0)`) counts as
        DIFFERENT from any real `relay_url` argument, never as "matches
        whatever we are pointed at" -- see the RETAIL_SCHEMA_VERSION v31
        comment in products/retail/backend/database/schema.py for why the
        opposite reading would preserve exactly the bug this version closes.
        That makes the first pull after a v31 upgrade reset once, harmlessly,
        even for a device whose relay never actually changed -- the
        documented, accepted cost of not being able to tell "unknown" from
        "same" for a value that did not exist before this column did.

        Does NOT commit -- matches `read_cursor`'s own read-only convention
        and `apply_pull_result`'s own cursor UPDATE just below, both of which
        leave the transaction to their caller. `pull_once` relies on this: the
        reset and the following pull's apply must land in one commit, or a
        reset that committed alone while the pull itself then failed would
        leave the cursor at 0 with nothing applied -- turning one bad tick
        into a full re-pull instead of a retry of the same range."""
        if not relay_url:
            return False
        cursor_row = conn.execute("SELECT relay_url FROM sync_cursor WHERE id=1").fetchone()
        stored_relay_url = cursor_row["relay_url"] if cursor_row else None
        if stored_relay_url == relay_url:
            return False
        conn.execute(
            "UPDATE sync_cursor SET last_seq=0, relay_url=? WHERE id=1",
            (relay_url,),
        )
        # WARNING, not INFO: a cursor reset re-pulls this device's entire
        # history from this relay. That is the correct, designed behaviour
        # (see the docstring above), but it is also a real, visible cost an
        # operator watching sync health should be able to attribute to a
        # relay change rather than mistake for a stuck or slow device.
        logger.warning(
            "sync: cursor relay_url changed from %r to %r -- resetting last_seq to 0 "
            "so this device re-pulls the new relay's full stream instead of silently "
            "stalling on a seq number from the old relay's sequence space.",
            stored_relay_url, relay_url,
        )
        return True

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
        # batch of pure product/customer/supplier deletes (keyed by id alone)
        # or unknown entity types never touches the provider at all, so a
        # SyncService with no provider configured can still apply those
        # without raising.
        # Also fetched whenever a PREVIOUSLY quarantined event is about to be
        # retried below -- a sale_item alone in this batch (its parent
        # already resolved and its own company_id-less INSERT needs no
        # provider) must not force a provider requirement, but a quarantined
        # SALE waiting for retry does, and there is no cheap way to know in
        # advance which kind is sitting in sync_apply_quarantine without
        # reading it -- so this is a small, deliberate over-fetch rather than
        # a second, more precise scan of the quarantine table.
        #
        # launch-readiness Phase 6 stage 6b-ii (tombstones) deliberately did
        # NOT extend this condition, and that is worth stating because the
        # obvious implementation does. A category `delete` now runs a cascade
        # (`UPDATE products SET category_id=NULL ...`, `_apply_event`'s
        # category branch) which needs a tenant to scope by -- and the first
        # cut of that cascade took it from `local_company_id`, which made a
        # delete-only batch require a provider for the first time.
        #
        # That is the wrong trade and it was reverted. `_get_local_company_id`
        # RAISES when no provider is wired or when onboarding has not
        # finished, so requiring it here would put a throw onto the code path
        # whose entire history is about not wedging the cursor -- an
        # exception in the apply loop aborts before the cursor advances, and
        # `run_once` swallows it (see `retail_category_delete_fk_sync_test.py`'s
        # module docstring for what that cost the last time it happened).
        # The cascade instead derives the tenant from the tombstoned category
        # row itself, which is guaranteed to still exist precisely because a
        # tombstone is not a `DELETE`. So a delete-only batch still needs no
        # provider, exactly as before this stage.
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
        #
        # P2S H3: this scan runs BEFORE the per-event savepoint, so it must
        # not be the thing a malformed event blows up on. `isinstance(str)`
        # guards a non-object event (`None`, a bare string -> AttributeError)
        # and an unhashable `entity_type` (a list -> `TypeError: unhashable
        # type` from the frozenset membership test).
        if any(
            isinstance(ev, dict)
            and isinstance(ev.get("entity_type"), str)
            and ev.get("entity_type") in self._handled_entity_types - _ENTITY_TYPES_WITHOUT_COMPANY_ID
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
        #
        # ...AND retail_setting, of ANY event type. The `retail_settings`
        # TABLE itself is created by the same lazy ensure, not by schema.py,
        # and this list forgot it. Measured 2026-09-25 on a real till that
        # joined a shop through Owner: the shop had set its currency, the
        # first pulled page carried that retail_setting next to 6 products,
        # `no such table: retail_settings` rolled the WHOLE page back, and the
        # new device received no products at all -- until a settings screen
        # happened to run a route that created the table. Delete counts too:
        # a delete-only first page reaches `DELETE FROM retail_settings` on
        # the same missing table. app.py's boot-time
        # `_ensure_runtime_schema_for_sync` is the first line of defence; this
        # is the one that still holds if that boot call ever failed.
        if self._local_ensure_schema is not None and (
            any(
                isinstance(ev, dict) and (
                    (ev.get("entity_type") in ("sale", "return", "payment")
                     and ev.get("event_type") in ("create", "update"))
                    # W4a / W4a-2 (v45/v46): applying a purchase order (or one of
                    # its receipts) re-derives its paid figure from `payments`
                    # (`_recompute_po_paid` reads `payments.related_type`, a lazy
                    # column), and a `po_link` attaches a payment to its order.
                    # A brand-new device whose first pulled page is only an order
                    # hit `no such column: related_type` and lost the whole page
                    # (retail_tombstone_test, 2026-10-07).
                    or (ev.get("entity_type") in _PO_ENSURE_ENTITY_TYPES
                        and ev.get("event_type") in ("create", "update"))
                    or (ev.get("entity_type") == "payment"
                        and ev.get("event_type") == "po_link")
                    or ev.get("entity_type") == "retail_setting")
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
        # Schema-version skew guard (module docstring's dedicated section):
        # identical one-sink-per-batch shape to `branch_fallbacks`/
        # `conflicts` just above, collected by `_apply_event`'s version gate
        # and consumed by `_log_schema_version_skew_summary` below. Empty
        # and never appended to for every call site that has not opted into
        # `local_schema_version_provider` -- the gate itself never runs.
        schema_skew: list = []
        for ev in events:
            self._apply_event_guarded(conn, ev, local_company_id, branch_fallback_sink=branch_fallbacks,
                                       conflict_sink=conflicts, schema_skew_sink=schema_skew)
        # Retried AFTER this batch's own events, not before: the whole point
        # of a batch containing the once-missing parent (e.g. an operator
        # replayed a quarantined sale from Owner's console) is that its
        # previously-parked children can resolve in the SAME tick they
        # arrive in, rather than waiting one more pull cycle. The same
        # reasoning now also covers a row parked purely for schema-version
        # skew: retrying it here means the very next pull tick after this
        # device is upgraded (no restart, no operator action) re-evaluates
        # it against the NEW local_schema_version_provider() value and
        # applies cleanly the moment versions line up.
        self._retry_quarantined_events(conn, local_company_id, branch_fallback_sink=branch_fallbacks,
                                        schema_skew_sink=schema_skew)
        if branch_fallbacks:
            self._log_branch_fallback_summary(branch_fallbacks)
        if conflicts:
            self._log_sync_conflict_summary(conflicts)
        if schema_skew:
            self._log_schema_version_skew_summary(schema_skew)
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
    def _doc_series_is_echo(conn, p: dict) -> bool:
        """True when this device's own `doc_series` row for `p["id"]` already
        equals the incoming payload in every field the tie-break and the book's
        meaning depend on (AUDIT-033 B2b). Never raises: a row it cannot read is
        simply "not an echo", which keeps the old behaviour (record a conflict)."""
        try:
            row = conn.execute(
                "SELECT row_version, claimed_at_utc, allocator_terminal_uid, status, label "
                "FROM doc_series WHERE id=?", (p.get("id"),)).fetchone()
        except sqlite3.Error:
            return False
        if row is None:
            return False
        incoming_version = p.get("row_version")
        try:
            same_version = incoming_version is not None and int(row[0] or 0) == int(incoming_version)
        except (TypeError, ValueError):
            return False
        return (same_version
                and (row[1] or None) == (p.get("claimed_at_utc") or None)
                and (row[2] or None) == (p.get("allocator_terminal_uid") or None)
                and (row[3] or "active") == (p.get("status") or "active")
                and (row[4] or None) == (p.get("label") or None))

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
    def _delta_set_clause(table, columns, changed):
        """Builds the `col=CASE WHEN ? THEN excluded.col ELSE table.col END`
        SET fragment for a delta-aware upsert, plus the 0/1 binds that drive it.

        `changed is None` -- the payload carries no `_changed_fields` key at
        all -- means "every column changed", which is exactly the full-snapshot
        behaviour that shipped before stage 6b-i. Every `create` event and
        every event emitted by a pre-6b-i device is in that case, so a shop
        upgrading with a backlog keeps applying its queued events unchanged.
        Same rule stage 6a-ii established for a missing `row_version`: an
        event emitted under the old contract is judged by the old contract.

        `changed` is wire data. `columns` is code-owned and is the ONLY source
        of the column names that reach the SQL string; the wire list is
        membership-tested against it and never interpolated. A payload naming
        a column that is not in `columns` is therefore ignored, not injected.
        """
        frag = ", ".join(f"{c}=CASE WHEN ? THEN excluded.{c} ELSE {table}.{c} END" for c in columns)
        binds = [1 if (changed is None or c in changed) else 0 for c in columns]
        return frag, binds

    @staticmethod
    def _parse_schema_version(value) -> Optional[int]:
        """The schema-version skew guard's one trust boundary (module
        docstring's "FAIL-CLOSED ON UNPARSEABLE/ABSENT/GARBAGE" section) --
        every comparison in `_apply_event`/`read_outbox` runs its raw value
        through this first, on BOTH sides of the comparison, and never
        compares a raw value directly.

        Trusts ONLY a genuine non-negative `int`. `isinstance(value, bool)`
        is checked and rejected explicitly and FIRST -- `bool` is a subclass
        of `int` in Python (`isinstance(True, int)` is `True`), so without
        this a stray `True`/`False` (a provider returning the wrong thing by
        mistake, or a hand-built test payload) would silently compare as
        1/0 instead of being refused as unparseable. No `int("39")`
        coercion of a numeric string, no truncation of a float that happens
        to be integral: matching `commercial_runtime/einvoicing/settings.
        py::is_enabled()`'s own documented discipline of trusting only an
        exact literal and treating every other shape -- absent, wrong type,
        out of range -- identically, as "not proven", never as "probably
        fine"."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if value >= 0 else None

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

    def _record_or_refresh_stock_exception(self, conn, *, local_company_id, product_id, branch_id,
                                            observed_quantity_on_hand) -> None:
        """Forwards to the OPTIONAL `stock_exception_recorder` collaborator
        (see `SyncService.__init__`'s own docstring for that parameter) --
        launch-readiness Phase 7 stage 7d-i (docs/launch-readiness/
        phase7-offline-ux.md "Decision 4"; database/schema.py's
        `_migrate_add_stock_exceptions`, retail v20).

        NO LONGER the canonical implementation. The upsert itself moved to
        products/retail/backend/database/schema.py's `record_or_refresh_
        stock_exception` in stage 7d-iii (ROADMAP.md's 2026-08-29
        "Correction to the v20 claim" entry) -- see that function's own
        docstring for the full upsert reasoning (why the ON CONFLICT's
        WHERE clause must repeat the partial index's predicate verbatim,
        why a RESOLVED row never blocks a fresh one). This module MUST NOT
        import that module directly: `sync_service.py` is shared with
        Clinic, and importing anything under `products/retail` from here
        would be a layering violation. So the collaborator is threaded
        through the constructor instead, exactly like `local_ensure_
        schema`/`local_freshness_store` above -- `None` by default, checked
        explicitly here, and NEVER inferred from a caught exception
        (ENGINEERING.md's rule against error sentinels that can also
        arrive legitimately: a genuine schema fault would look identical
        to "not wired" if this caught one instead of checking for None).
        `None` in production only at the registry `SyncService`
        construction site, which never reaches this method at all --
        `inventory_movement` is not in `REGISTRY_SYNC_ENTITY_TYPES`.

        Called ONLY from the `inventory_movement` branch's `if cur.rowcount:`
        block, and only when the resulting balance is negative -- see that
        branch's own comment for why both conditions matter (a replayed
        apply must not re-stamp `detected_at_utc`; a healthy merge that
        never goes negative must never record anything at all).

        CORRECTION (stage 7d-iii), recorded here rather than silently
        rewritten -- matching this project's own convention (see
        schema.py's "CORRECTION:" note on the v18 migration comment for
        the precedent). This docstring used to say, at this exact spot,
        that this apply branch was the ONLY writer, because "a local sale
        cannot drive its own balance negative -- its own `qty > on_hand`
        refusal still stands," and that writing from two places would
        invent a second source for one fact. That stopped being true the
        moment stage 7d-iii made `create_sale`'s refusal CONDITIONAL on the
        device being behind on sync (docs/launch-readiness/
        phase7-offline-ux.md "Correction to Decision 1"): a local sale
        allowed past a stale-LOW figure is now a SECOND genuine route to a
        negative balance, not a duplicate source for the same one -- the
        cross-device merge this branch handles and a same-device oversold
        sale are two different events that can each independently put a
        balance below zero. `create_sale` now records its own case through
        the exact same `record_or_refresh_stock_exception` function this
        method forwards to, which is why "two places, one shared
        implementation" was the right fix rather than "two places, two
        copies of the SQL."
        """
        if self._stock_exception_recorder is None:
            return
        self._stock_exception_recorder(
            conn, company_id=local_company_id, product_id=product_id,
            branch_id=branch_id, observed_quantity_on_hand=observed_quantity_on_hand,
        )

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

    @staticmethod
    def _log_schema_version_skew_summary(schema_skew: list) -> None:
        """One WARNING for the whole batch, not one per refused row --
        identical reasoning to `_log_branch_fallback_summary`/`_log_sync_
        conflict_summary` above. Grouped by `(entity_type, reason)` so "9
        product events too new, 2 sale events unverifiable" reads at a
        glance; the exact local/incoming schema_version and entity_id for
        each live in `sync_apply_quarantine` (see `_quarantine_apply_event`'s
        `detail` string), not repeated here. Unlike those two siblings, an
        empty batch here is not merely common -- it is the ENTIRE fleet's
        state until a product opts into `local_schema_version_provider` (see
        `__init__`'s docstring), so this line should be rare in practice
        and, when it does appear, names something an operator can act on:
        upgrade the behind device."""
        counts: dict = {}
        for entity_type, reason in schema_skew:
            key = (entity_type, reason)
            counts[key] = counts.get(key, 0) + 1
        parts = [f"{entity_type}/{reason}={count}" for (entity_type, reason), count in counts.items()]
        logger.warning(
            "sync: %d row(s) this batch were refused by the schema-version skew guard -- "
            "this device could not verify it is safe to apply them; see sync_apply_quarantine "
            "for detail. %s",
            len(schema_skew), ", ".join(parts),
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
                      conflict_sink: Optional[list] = None,
                      schema_skew_sink: Optional[list] = None,
                      *, from_quarantine: bool = False) -> bool:
        """Applies one pulled event to a local table. Returns True when the
        event is FULLY handled -- inserted, upserted, a legitimate no-op
        (unknown entity_type, a non-`create` event on one of the five
        money-moving types, other than `payment`'s one narrow `update` void
        transition -- AUDIT-032A, see that branch's own comment) -- and
        False when a Phase 5 money-moving event was PARKED in
        `sync_apply_quarantine` because a row it depends on could not be
        resolved locally (see `_quarantine_apply_event`): a sale_item/
        return/return_item/payment(sale) whose PARENT hasn't arrived yet, a
        payment `update` (void) whose OWN `create` hasn't arrived yet, OR
        (schema-version skew guard, module docstring's dedicated section)
        this device could not positively verify it is safe to apply this
        event's schema version at all. The pre-Phase-5 five entity types
        never return False for the missing-parent reasons above, but CAN
        now return False via the version gate -- it runs before any
        entity-specific branch, uniformly, for every handled entity type.
        Callers that only care about "did this raise" (the main loop in
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
        `sync_conflicts` row and does not care about the batch summary.

        `schema_skew_sink`, when given, collects `(entity_type, reason)` for
        every event THIS call refused via the version gate -- the identical
        one-sink-per-batch shape `conflict_sink`/`branch_fallback_sink`
        already use, consumed by `apply_pull_result`'s own
        `_log_schema_version_skew_summary` call. None is correct for a test
        that only cares about the `sync_apply_quarantine` row."""
        entity_type = ev.get("entity_type")
        # `self._handled_entity_types`, not a hardcoded tuple -- see the
        # module docstring's "Two-stream design" paragraph and `__init__`'s
        # own docstring for `handled_entity_types`. An event whose type is
        # outside THIS instance's own set is skipped exactly like a type
        # this build has never heard of at all: never an error, never
        # quarantined (a foreign-stream event is not poison -- it belongs to
        # the other stream), and the cursor still advances past it, because
        # this method returning True is what `apply_pull_result`'s caller
        # reads as "fully handled, nothing left to retry" -- UNLESS the
        # payload itself proves this "unknown" type is actually a FUTURE
        # entity type this build simply has not been taught yet (F6,
        # BOOKS-WAVE1-PLAN.md decision F6; v2 finding 1b, plan risk R2a).
        #
        # THE HAZARD THIS CLOSES: a v41 device introduces `gl_journal`
        # (this very wave); a v40 peer has never heard of that type, so
        # this same gate used to `return True` unconditionally -- the
        # cursor advanced PAST every `gl_journal` event, forever, with
        # nothing quarantined and nothing to retry. Once Owner's or the
        # LAN hub's own pruning (both scheduled, both outside this wave's
        # files) drops anything at or below the lowest acknowledged
        # cursor, that history is GONE -- verifier 2's own
        # `repro1_v40_peer.py` reproduces exactly this: a v40 till loses a
        # peer's entire backfilled ledger history and everything posted
        # in the upgrade gap. The KNOWN five-entity-type Phase-5 guard
        # below already refuses to apply a KNOWN type from a NEWER schema
        # version than this device's own; this extends the identical
        # refusal to a type this build cannot even dispatch on at all,
        # rather than leaving unknown-type skew as the one gap that guard
        # never covered.
        #
        # THE RULE: quarantine (never skip) an unhandled `entity_type`
        # under the SAME `schema_version_too_new` reason the known-type
        # gate below already uses -- `_retry_quarantined_events` already
        # replays that reason on every later pull, so this device
        # self-heals the moment it upgrades to a build that both
        # recognises the type AND advances its own schema_version past
        # the sender's -- IF AND ONLY IF the payload NAMES a sender
        # schema_version that is genuinely newer than this device's own.
        # An unknown type from an OLDER-OR-EQUAL sender, or one that never
        # stamps `_sender_schema_version` at all, keeps TODAY'S plain
        # skip -- this is deliberately NOT the same fail-closed posture
        # the known-type gate takes on an unparseable/absent version (that
        # gate's own `schema_version_unverifiable` reason): an unknown
        # type is already the ordinary, harmless "this belongs to a
        # stream/version this build never claimed to speak" case, and
        # only the ONE proven-unsafe direction (sender confirmed ahead)
        # should change that. The REGISTRY stream's own events
        # (`user`/`user_permission`, arriving here as "unknown" whenever
        # THIS instance is a retail-stream `SyncService`) are UNSTAMPED by
        # design -- no registry `SyncService` construction site passes a
        # `local_schema_version_provider` (see `read_outbox`'s own
        # docstring: no provider configured means nothing is stamped at
        # all) -- so they carry no `_sender_schema_version` key and fall
        # straight through to the plain skip below, exactly as before
        # this fix: `test_registry_stream_events_stay_unaffected_by_the_
        # unknown_type_schema_guard` (commercial_runtime/sync/tests/
        # test_sync_service.py) proves this directly.
        # A non-string entity_type (a list or dict off a hostile/broken peer)
        # is UNHASHABLE, and `in <set>` on it raised TypeError out of the
        # whole batch (vYM2). It is by definition not a type this build
        # handles, so it takes the same plain-skip path as any unknown one.
        # The hub transport already rejects the shape; defence in depth.
        if not isinstance(entity_type, str) or entity_type not in self._handled_entity_types:
            if self._local_schema_version_provider is not None:
                # isinstance: before F6 this path never read the payload at
                # all, so a non-object payload on an unknown type must stay
                # the plain skip, never an AttributeError out of the page.
                p_unknown = ev.get("payload")
                raw_sender_version = (p_unknown.get("_sender_schema_version")
                                      if isinstance(p_unknown, dict) else None)
                if raw_sender_version is not None:
                    receiver_version = self._parse_schema_version(
                        self._local_schema_version_provider())
                    sender_version = self._parse_schema_version(raw_sender_version)
                    if (receiver_version is not None and sender_version is not None
                            and sender_version > receiver_version):
                        if schema_skew_sink is not None:
                            schema_skew_sink.append((entity_type, "schema_version_too_new"))
                        return self._quarantine_apply_event(
                            conn, ev, reason="schema_version_too_new",
                            detail=(
                                f"entity_type={entity_type!r} is not one this "
                                f"build's SyncService instance handles at all, "
                                f"AND its sender's schema_version={sender_version} "
                                f"is ahead of this device's local schema_version="
                                f"{receiver_version} -- refusing to skip past it "
                                "silently (that would lose it permanently once a "
                                "relay/hub prunes it) until this device is "
                                f"upgraded to schema_version>={sender_version} and "
                                "can recognise this entity_type."
                            ),
                        )
            return True
        p = ev.get("payload") or {}
        if not isinstance(p, dict):
            # P2S H3: every branch below reads the payload with `.get`, so a
            # list / string / number payload on a HANDLED type used to raise
            # `AttributeError` out of the loop, roll the whole batch back and
            # be re-pulled forever with every later event stuck behind it.
            # Parked as one row instead (never skipped: a peer that later
            # fixes its payload re-sends under the same id and the retry
            # loop picks it up).
            return self._quarantine_apply_event(
                conn, ev, reason="malformed_payload",
                detail=f"payload is a {type(p).__name__}, not an object")
        event_type = ev.get("event_type")
        # AUDIT-033 / S-AUTH: AUTHORITY events must be signed by a member of this
        # licence who is allowed to send that kind of event. Runs BEFORE the
        # per-type applier and in front of (never instead of) its own epoch /
        # row_version / quarantine checks. A refusal PARKS the event with its
        # full payload (`_auth` included), so `_retry_quarantined_events`
        # re-verifies it each pull -- a `main_unknown` event applies the moment
        # the assignment arrives, a forgery stays parked and harmless. The
        # applier then sees the payload WITHOUT `_auth` (nothing stores it), while
        # `_orig_payload` keeps the signed original for any later park.
        # Never trust a value that arrived with the event: the gate below sets all of
        # these, from the verified signature, and nothing else may.
        for _private in ("_authority_verified", "_authority_signer_is_main",
                         "_authority_signer_dev", "_authority_signer_inst",
                         "_authority_harmless_owner_edit"):
            ev.pop(_private, None)
        if _authority.is_authority_event(ev):
            refusal = self._authority_gate(conn, ev, p, local_company_id, entity_type,
                                           from_quarantine=from_quarantine)
            # Review V3r G3: a verifier for payloads of THIS event's identity, handed
            # to the park only on a private COPY of the event (never the caller's
            # dict -- a callable must not leak into anything that serialises events).
            verify_for_park = self._payload_verifier(ev)
            if refusal is not None:
                return self._quarantine_apply_event(
                    conn, dict(ev, _authority_verify=verify_for_park),
                    reason=refusal.reason, detail=refusal.detail)
            # Tell `_apply_event_guarded` this event passed verification AND the
            # rule table, so that once it APPLIES it may supersede the unsigned
            # copies of the same entity parked earlier (`_supersede_parked_
            # unsigned`). A private marker on the caller's own dict: it is never
            # stored (quarantine keeps only the payload) and a pulled event
            # cannot carry it (the relays drop unknown top-level keys, and the
            # flag is set here, after the gate, never read from the wire).
            ev["_authority_verified"] = True
            p = _authority.strip_auth(p)
            ev = dict(ev, payload=p, _orig_payload=ev.get("payload"),
                      _authority_verify=verify_for_park)
        # Schema-version skew guard (module docstring's dedicated section) --
        # runs BEFORE any entity-specific branch, uniformly, for every
        # handled entity type: a structural compatibility precondition, not
        # a per-entity business rule, so it belongs above the dispatch, not
        # duplicated into each branch below. Entirely inert (`self._local_
        # schema_version_provider is None`) for every call site that has not
        # opted in -- see `__init__`'s own docstring for why this is safe by
        # construction rather than by luck.
        if self._local_schema_version_provider is not None:
            receiver_version = self._parse_schema_version(self._local_schema_version_provider())
            raw_sender_version = p.get("_sender_schema_version")
            sender_version = self._parse_schema_version(raw_sender_version)
            if receiver_version is None or sender_version is None:
                if schema_skew_sink is not None:
                    schema_skew_sink.append((entity_type, "schema_version_unverifiable"))
                return self._quarantine_apply_event(
                    conn, ev, reason="schema_version_unverifiable",
                    detail=(
                        f"local schema_version={receiver_version!r}, incoming "
                        f"schema_version={raw_sender_version!r} (entity_type={entity_type!r}, "
                        f"event_type={event_type!r}); cannot prove this event is safe to apply "
                        "on this device's current schema -- refusing rather than assuming it is."
                    ),
                )
            if sender_version > receiver_version:
                if schema_skew_sink is not None:
                    schema_skew_sink.append((entity_type, "schema_version_too_new"))
                return self._quarantine_apply_event(
                    conn, ev, reason="schema_version_too_new",
                    detail=(
                        f"incoming schema_version={sender_version} is ahead of this device's "
                        f"local schema_version={receiver_version} (entity_type={entity_type!r}, "
                        f"event_type={event_type!r}); applying it here could silently drop "
                        "columns this build has never migrated -- refusing until this device "
                        f"is upgraded to schema_version>={sender_version}."
                    ),
                )
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
                #
                # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
                # the DO UPDATE half no longer blindly writes every column
                # from `excluded.*` -- a sender that only edited `name` was
                # ALSO re-sending its own stale `description` at whatever
                # value it last saw, and applying that stale value here
                # clobbered a `description` edit some OTHER device made in
                # the meantime, even though the two edits never touched the
                # same field. `_changed_fields`, when the payload carries it,
                # names exactly the columns this write actually changed; the
                # INSERT half is untouched (a brand-new id has no local value
                # to preserve, so it always takes the full snapshot). See
                # `_delta_set_clause`'s own docstring for the missing-key
                # ("legacy") fallback and why the column list itself is
                # code-owned, never taken from the wire.
                #
                # launch-readiness Phase 6 stage 6b-ii (tombstones,
                # phase6b-decisions.md "Decision B"): a CSV re-import
                # resurrecting a tombstoned category (import_api.py) queues
                # an `update` event naming `deleted_at_utc` in
                # `_changed_fields`.
                #
                # CORRECTION (stage 6b-iii-a, 2026-08-27): the paragraph that
                # used to sit here claimed this branch deliberately did NOT
                # add `deleted_at_utc` to its own column list, because doing
                # so raised `sqlite3.OperationalError: no such column:
                # deleted_at_utc` against this file's and test_internal_
                # routes.py's hand-built minimal fixtures. That was true of
                # the FIRST attempt within this same 6b-ii commit -- it is
                # not true of the code a few lines below, which DOES include
                # `deleted_at_utc` in the list, because the fixtures were
                # updated (both files' `CREATE TABLE categories` gained the
                # column) rather than the column left out. Left uncorrected,
                # a reader would trust the comment over the code sitting
                # right under it.
                changed_fields = p.get("_changed_fields")
                # `deleted_at_utc` is in the DELTA-GATED column list, never
                # written unconditionally, and that distinction is the whole
                # reason an import resurrection can travel safely.
                #
                # Unconditional would be a real bug: device A, which never
                # saw a tombstone device B applied, renames the category and
                # sends `_changed_fields=['name']` carrying its own
                # `deleted_at_utc` of NULL. Writing that NULL would
                # RESURRECT a category B had deleted, off the back of an
                # edit that never touched deletion at all -- the exact
                # untouched-field clobber stage 6b-i exists to close,
                # pointed at the one column where it un-deletes something.
                #
                # Delta-gated, both directions come out right: a name edit
                # leaves the tombstone alone, and an import resurrection
                # (import_api.py, phase6b-decisions.md "Decision B") names
                # `deleted_at_utc` in `_changed_fields` and therefore clears
                # it on every device, not just the one that ran the import.
                delta_frag, delta_binds = self._delta_set_clause(
                    "categories", ["name", "description", "deleted_at_utc"], changed_fields)
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO categories (id, company_id, name, description, row_version, "
                    "updated_at_utc, deleted_at_utc) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(categories.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > categories.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("description", ""),
                     insert_row_version, p.get("updated_at_utc"), p.get("deleted_at_utc"),
                     *delta_binds,
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
                # launch-readiness Phase 6 stage 6b-ii (tombstones,
                # phase6b-decisions.md "Decision A"): a gated soft-delete
                # replaces the old hard DELETE. Identical shape to the
                # product/customer/supplier delete branches above --
                # NULL-tolerant WHERE (a legacy delete payload with no
                # `row_version` key still applies; see the category
                # create/update branch's comment above for the full
                # "missing is not stale" reasoning), `MAX`-protected
                # row_version (never regresses the counter), and
                # `_local_row_version`-disambiguated conflict recording
                # (None means no local row, not a conflict; a real integer
                # means a genuine stale-delete conflict). This closes the one
                # ungated destructive catalogue path stage 6a-ii flagged: a
                # stale category delete relayed after a rename on another
                # device can no longer remove the renamed row.
                #
                # `deleted_at_utc` gets a REAL fallback here (this device's
                # own current time), unlike `updated_at_utc` a few lines down
                # (which stays honestly NULL for a legacy payload -- see the
                # create/update branch's own comment on that). The two are
                # not equivalent: `updated_at_utc` is purely informational,
                # but `deleted_at_utc` is the ENTIRE visibility gate for
                # categories (`list_categories`/`list_products`'s
                # `deleted_at_utc IS NULL` filters) -- categories have no
                # `status` column to fall back on the way product/customer/
                # supplier deletes do (their `status='inactive'` is written
                # unconditionally regardless of what the payload carries, so
                # a NULL `deleted_at_utc` there is a harmless redundancy, not
                # a visible bug). Binding a bare `p.get("deleted_at_utc")`
                # (None for the oldest legacy shape -- `delete_category` used
                # to queue only `{'id': category_id}`, no timestamp at all)
                # would apply "successfully" (rowcount 1, cascade runs) while
                # leaving the category fully live and visible forever: a
                # silent correctness bug, not a mere metadata gap. Found by
                # running `retail_category_delete_fk_sync_test.py` for real,
                # not by inspection.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                deleted_at_utc = p.get("deleted_at_utc") or datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "UPDATE categories SET deleted_at_utc=?, "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (deleted_at_utc, insert_row_version, p.get("updated_at_utc"), p.get("id"),
                     raw_row_version, raw_row_version),
                )
                if cur.rowcount == 0:
                    local_row_version = self._local_row_version(conn, "categories", p.get("id"))
                    if local_row_version is not None:
                        self._record_sync_conflict(
                            conn, conflict_sink, local_company_id=local_company_id,
                            entity_type="category", entity_id=p.get("id"), event_type="delete",
                            local_row_version=local_row_version,
                            incoming_row_version=raw_row_version, payload=p,
                        )
                else:
                    # THE CASCADE -- see retail_api.py's delete_category
                    # comment for the full reasoning (schema v3's
                    # `ON DELETE SET NULL`, why this walk must run
                    # identically on both the local delete path and here, and
                    # why it must NOT bump the touched products' own
                    # row_version or queue product sync events). Run ONLY
                    # when the tombstone actually applied above (this
                    # `else`, not `cur.rowcount == 0`): a discarded stale
                    # delete must not null anything.
                    #
                    # Tenancy is derived from the category row this branch
                    # just tombstoned, NOT from `local_company_id`. That
                    # keeps the `company_id` scoping every business query in
                    # this codebase requires (see CLAUDE.md) while leaving a
                    # delete-only batch free of any `local_company_id`
                    # requirement at all.
                    #
                    # That distinction is the whole point, not a
                    # micro-optimisation: `_get_local_company_id()` RAISES
                    # when no provider is wired or when onboarding has not
                    # finished. Making a lone category delete -- the common
                    # real-world shape, a delete pushed on its own -- depend
                    # on it would put a throw back onto the exact code path
                    # whose entire history is about NOT wedging the cursor.
                    # `retail_category_delete_fk_sync_test.py`'s module
                    # docstring records what that cost last time: an
                    # exception raised in here aborted `apply_pull_result`
                    # BEFORE the cursor advanced, `run_once` swallowed it,
                    # and the device silently stopped receiving every event
                    # from every device, forever.
                    #
                    # The subquery cannot pick the wrong tenant: a tombstone
                    # leaves the row in place -- that is what distinguishes
                    # it from the `DELETE` this replaced -- so the category's
                    # own `company_id` is present and authoritative at
                    # exactly this moment.
                    conn.execute(
                        "UPDATE products SET category_id=NULL "
                        "WHERE category_id=? AND company_id=("
                        "SELECT company_id FROM categories WHERE id=?)",
                        (p.get("id"), p.get("id")),
                    )
        elif entity_type == "retail_setting":
            # 2026-09-05: shop-level settings travel between the shop's
            # devices (see retail_api.py::_queue_setting_sync_event for the
            # defect that made this necessary). Only reachable from the
            # RETAIL-configured instance -- `retail_settings` lives in
            # retail.db. Written under THIS device's own company_id, never
            # the payload's (the payload carries none), identical to every
            # other company-scoped branch here.
            #
            # Plain last-write-wins, NOT row_version-gated: like
            # `user_permissions`, this table has no version column, settings
            # edits are rare and deliberate, and the admin who changes a
            # currency sees the result on the next screen load. Documented
            # residual risk, same as the user_permission branch's.
            if event_type not in ("create", "update", "delete"):
                return True
            skey = p.get("skey")
            if not isinstance(skey, str) or not skey or len(skey) > 64 or skey.startswith("blob_"):
                # A malformed key can never become valid on retry, and the
                # blob prefix is refused on the emit side too -- belt and
                # braces, because a kilobyte logo must never be written
                # through this path. Skipped, not quarantined.
                #
                # "blob_" is retail_api.py's `_SETTINGS_BLOB_PREFIX` literal,
                # hardcoded here rather than imported -- sync_service must
                # not import retail_api. The two literals must match.
                return True
            if skey.startswith(_DEVICE_LOCAL_SETTING_PREFIXES):
                # DEVICE-LOCAL settings are never legitimately on the wire:
                # the only writer (retail_api.py::_write_local_setting)
                # deliberately skips _queue_setting_sync_event. So an event
                # naming one is a peer trying to write THIS till's private
                # state -- e.g. `numbering_till:<terminal>`, this till's own
                # document-number counter, which a peer could rewind to force
                # a duplicate number, or jump to burn the series. Skipped, not
                # quarantined (it can never become valid on retry), for
                # create/update AND delete, and logged once per key so a
                # hostile or buggy peer cannot flood the log.
                seen = self.__dict__.setdefault("_device_local_setting_logged", set())
                if skey not in seen:
                    seen.add(skey)
                    logger.warning(
                        "SyncService: refused a %s event for device-local "
                        "retail_setting %r from a peer (logged once per key)",
                        event_type, skey)
                return True
            # `local_company_id` is the batch-level eager fetch, and
            # apply_pull_result only performs it for batches that carry a
            # create/update -- a DELETE-ONLY batch (an admin cleared the
            # business day and nothing else changed) arrives with None, and
            # `DELETE ... WHERE company_id=NULL` matches nothing, silently.
            # Caught by test_retail_setting_sync_apply.py's delete-only case
            # before it shipped; resolve it here when the batch did not.
            company_id = local_company_id or self._get_local_company_id()
            if skey.startswith("branch_code:"):
                # Schema v42: a peer that has not (yet) heard of a branch
                # alias may still address the receipt code by the OLD branch
                # uid. Land it under the canonical uid, so the delete and the
                # upsert below both hit the row the numbering code reads.
                _bc_uid = skey[len("branch_code:"):]
                _bc_canon = _canonical_branch_uid(conn, _bc_uid, company_id)
                if _bc_canon and _bc_canon != _bc_uid:
                    skey = "branch_code:" + _bc_canon
            if event_type == "delete":
                conn.execute("DELETE FROM retail_settings WHERE company_id=? AND skey=?", (company_id, skey))
                return True
            svalue = p.get("svalue")
            conn.execute(
                "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
                "ON CONFLICT(company_id, skey) DO UPDATE SET svalue=excluded.svalue",
                (company_id, skey, "" if svalue is None else str(svalue)),
            )
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
                #
                # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
                # DO UPDATE now writes only the columns `_changed_fields`
                # names -- see the category branch's comment above for the
                # full reasoning (why a full-snapshot re-send used to clobber
                # an untouched field, why the INSERT half is unaffected, why
                # a missing `_changed_fields` key means "every column",
                # `_delta_set_clause`'s own docstring for the wire-vs-code-
                # owned column-name safety note).
                #
                # launch-readiness Phase 6 stage 6b-iii-a (Step 3, restore
                # paths must clear the tombstone): `deleted_at_utc` added to
                # the DELTA-GATED column list, exactly as the category
                # branch already has it -- see that branch's comment for the
                # full reasoning (unconditional would let a device that
                # never saw a tombstone resurrect it off the back of an
                # ordinary edit re-sending its own stale `deleted_at_utc` of
                # NULL). `update_product`'s restore path (retail_api.py) now
                # names `deleted_at_utc` in `_changed_fields` only when it
                # actually cleared it, so this only ever un-deletes a row on
                # the genuine restore path.
                # launch-readiness "product variants, wave 1" (schema v25):
                # `parent_product_id`/`variant_label` added to BOTH the
                # delta-gated column list below AND the INSERT column list a
                # few lines down. This is THE site ROADMAP.md's own warning
                # names -- miss either half here (or the create/update
                # payload emission in retail_api.py) and a variant arrives on
                # a peer device as an orphan standalone product: it scans and
                # sells fine (it is still an ordinary row with its own
                # barcode/stock), but its grouping under the parent silently
                # never crosses the wire. `p.get("parent_product_id")`/
                # `p.get("variant_label")` default to `None` -- the correct
                # reading for a payload from a pre-v25 emitter (an older
                # device's queued outbox entry): "this row is not a variant",
                # which is exactly what NULL already means for every existing
                # product, so an old payload changes nothing about how it
                # applies.
                # Schema v39 (cost basis): `avg_cost` was omitted from BOTH the
                # delta-gated column list below AND the INSERT column list/
                # VALUES a few lines down -- retail_api.py puts it on the wire
                # (create's payload carries a literal 0; receive_purchase_
                # order's own update event carries the real moving average,
                # `_changed_fields: ['avg_cost']`) but this apply branch had
                # no column named `avg_cost` anywhere, so `_delta_set_clause`
                # could never gate it (its own docstring: a column absent from
                # `columns` is code-invisible, not merely unchanged) and the
                # plain INSERT half left it at the schema DEFAULT 0 regardless
                # of what the payload carried. A peer device therefore never
                # converged on the real cost basis at all.
                changed_fields = p.get("_changed_fields")
                delta_frag, delta_binds = self._delta_set_clause(
                    "products",
                    ["sku", "barcode", "name", "category_id", "supplier_id", "cost_price",
                     "sell_price", "tax_rate", "unit", "reorder_level", "reorder_method", "status",
                     "deleted_at_utc", "parent_product_id", "variant_label", "avg_cost"],
                    changed_fields)
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO products (id, company_id, sku, barcode, name, category_id, supplier_id, "
                    "cost_price, sell_price, tax_rate, unit, reorder_level, reorder_method, status, "
                    "deleted_at_utc, parent_product_id, variant_label, avg_cost, row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(products.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > products.row_version",
                    (p.get("id"), local_company_id, p.get("sku"), p.get("barcode", ""), p.get("name"),
                     p.get("category_id"), p.get("supplier_id"), p.get("cost_price", 0), p.get("sell_price", 0),
                     p.get("tax_rate", 0), p.get("unit", "pcs"), p.get("reorder_level", 5),
                     p.get("reorder_method", "none"), p.get("status", "active"), p.get("deleted_at_utc"),
                     p.get("parent_product_id"), p.get("variant_label"), p.get("avg_cost", 0),
                     insert_row_version, p.get("updated_at_utc"),
                     *delta_binds,
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
                # launch-readiness Phase 6 stage 6b-ii (tombstones,
                # phase6b-decisions.md): `deleted_at_utc` carried through.
                # The reject-stale WHERE, the `MAX` never-regress protection
                # and the conflict recording below are all UNCHANGED from
                # stage 6a-ii.
                #
                # launch-readiness Phase 6 stage 6b-iii-a (deletion stops
                # overloading `status`, Step 2b/2c): `status='inactive'` is
                # REMOVED from this UPDATE -- `deleted_at_utc` is now the
                # ENTIRE visibility gate for a product, matching every read
                # path this stage gave a `deleted_at_utc IS NULL` filter (see
                # retail_api.py's delete_product comment for the full list).
                #
                # `deleted_at_utc` also GAINS a REAL fallback here (this
                # device's own current time), where before it bound a bare
                # `p.get("deleted_at_utc")` with no fallback at all -- safe
                # ONLY while `status='inactive'` was written unconditionally
                # alongside it. With that write gone, a LEGACY delete payload
                # -- one carrying no `deleted_at_utc` key at all, already
                # sitting in some device's outbox -- would otherwise apply
                # "successfully" (rowcount 1) while leaving the row FULLY
                # LIVE AND VISIBLE forever: exactly the category delete
                # branch's own `deleted_at_utc = p.get(...) or datetime.now(
                # ...)` fallback, and this is precisely the bug that fallback
                # exists to prevent, reached here by the same route the
                # category branch's own comment describes.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                deleted_at_utc = p.get("deleted_at_utc") or datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "UPDATE products SET deleted_at_utc=?, "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (deleted_at_utc, insert_row_version, p.get("updated_at_utc"), p.get("id"),
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
                #
                # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
                # DO UPDATE now writes only the columns `_changed_fields`
                # names -- see the category branch's comment above for the
                # full reasoning. This is the branch stage 6b-i's own
                # decisive test targets: a customer PATCH that only touches
                # `phone` must not revert an `address` edit some other
                # device made concurrently.
                #
                # launch-readiness Phase 6 stage 6b-iii-a: `deleted_at_utc`
                # added to the DELTA-GATED column list -- see the product
                # branch's identical comment above for the full reasoning.
                # NOTE: `update_customer` (retail_api.py) has no restore path
                # today (its `allowed` PATCH fields omit `status` entirely,
                # a pre-existing asymmetry with product/supplier), so nothing
                # yet EMITS a customer `_changed_fields` naming
                # `deleted_at_utc` -- this column is added here for parity
                # with the other two branches and so a future restore route
                # (6b-iii-b) needs no apply-side change, but it is inert
                # until that route exists.
                changed_fields = p.get("_changed_fields")
                delta_frag, delta_binds = self._delta_set_clause(
                    "customers", ["name", "phone", "email", "address", "status", "deleted_at_utc"], changed_fields)
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO customers (id, company_id, name, phone, email, address, status, "
                    "deleted_at_utc, row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(customers.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > customers.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active"), p.get("deleted_at_utc"),
                     insert_row_version, p.get("updated_at_utc"),
                     *delta_binds,
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
                # launch-readiness Phase 6 stage 6b-ii (tombstones):
                # `deleted_at_utc` carried through.
                #
                # launch-readiness Phase 6 stage 6b-iii-a: `status='inactive'`
                # REMOVED (see the product delete branch's comment above for
                # the full reasoning), and `deleted_at_utc` GAINS the same
                # `now()` fallback for the same "legacy payload with no
                # deleted_at_utc key must not leave the row fully live and
                # visible forever" reason.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                deleted_at_utc = p.get("deleted_at_utc") or datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "UPDATE customers SET deleted_at_utc=?, "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (deleted_at_utc, insert_row_version, p.get("updated_at_utc"), p.get("id"),
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
                # retail_api.py's `_refresh_party_balance` (v44: a derived cache).
                # stage 6a-ii (this stage): `WHERE excluded.row_version >
                # suppliers.row_version` gate, NULL-tolerant + `MAX`-protected
                # -- see the category branch's comment above for the full
                # reasoning.
                #
                # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
                # DO UPDATE now writes only the columns `_changed_fields`
                # names -- see the category branch's comment above for the
                # full reasoning.
                #
                # launch-readiness Phase 6 stage 6b-iii-a: `deleted_at_utc`
                # added to the DELTA-GATED column list -- see the product
                # branch's identical comment above for the full reasoning.
                # `update_supplier`'s restore path (retail_api.py) names
                # `deleted_at_utc` in `_changed_fields` when it clears the
                # tombstone, so this is what makes that restore actually
                # reach a receiving device.
                changed_fields = p.get("_changed_fields")
                delta_frag, delta_binds = self._delta_set_clause(
                    "suppliers", ["name", "phone", "email", "address", "status", "deleted_at_utc"], changed_fields)
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO suppliers (id, company_id, name, phone, email, address, status, "
                    "deleted_at_utc, row_version, updated_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(suppliers.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > suppliers.row_version",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active"), p.get("deleted_at_utc"),
                     insert_row_version, p.get("updated_at_utc"),
                     *delta_binds,
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
                # launch-readiness Phase 6 stage 6b-ii (tombstones):
                # `deleted_at_utc` carried through.
                #
                # launch-readiness Phase 6 stage 6b-iii-a: `status='inactive'`
                # REMOVED, `deleted_at_utc` GAINS the `now()` fallback -- see
                # the product delete branch's comment above for the full
                # reasoning, identical here.
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                deleted_at_utc = p.get("deleted_at_utc") or datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "UPDATE suppliers SET deleted_at_utc=?, "
                    "row_version=MAX(row_version, ?), updated_at_utc=? "
                    "WHERE id=? AND (? IS NULL OR ? > row_version)",
                    (deleted_at_utc, insert_row_version, p.get("updated_at_utc"), p.get("id"),
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
                #
                # launch-readiness Phase 6 stage 6b-i (changed-field deltas):
                # DO UPDATE now writes only the columns `_changed_fields`
                # names -- see the category branch's comment above for the
                # full reasoning. accept_reorder_request/decline_reorder_
                # request only ever change status/resolved_at, so their
                # `_changed_fields` names exactly those two.
                changed_fields = p.get("_changed_fields")
                delta_frag, delta_binds = self._delta_set_clause(
                    "reorder_requests", ["status", "draft_message", "resolved_at"], changed_fields)
                raw_row_version = p.get("row_version")
                insert_row_version = raw_row_version if raw_row_version is not None else 1
                cur = conn.execute(
                    "INSERT INTO reorder_requests (id, company_id, branch_id, product_id, status, "
                    "draft_message, resolved_at, row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(reorder_requests.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR ? > reorder_requests.row_version",
                    (p.get("id"), local_company_id, p.get("branch_id"), p.get("product_id"),
                     p.get("status", "pending"), p.get("draft_message"), p.get("resolved_at"),
                     insert_row_version, p.get("updated_at_utc"),
                     *delta_binds,
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
            try:
                # Schema v27/sync-payload-coverage follow-up: `points_redeemed_
                # amount` was missing from this INSERT entirely -- create_sale
                # puts it on the wire (see that route's own comment, right
                # beside amount_paid/change_amount in the same settled-tender
                # breakdown), but this apply branch never named the column, so
                # a redeemed sale arrived on every OTHER device reading 0
                # redeemed forever. `sale` has no update event (see this
                # branch's own comment above), so there is no delta-gate half
                # to fix -- this INSERT is the only write path.
                conn.execute(
                    "INSERT INTO sales (company_id, sale_number, branch_id, customer_id, cashier, "
                    "subtotal, discount_amount, tax_amount, total, amount_paid, change_amount, "
                    "points_redeemed_amount, "
                    "payment_method, status, idempotency_key, notes, created_at, due_date, session_id, "
                    "uid, actor_user_uid, terminal_id, created_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,NULL,?,?,?,?) "
                    "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                    (local_company_id, p.get("sale_number"), resolved_branch_id, p.get("customer_id"),
                     p.get("cashier", "POS"), p.get("subtotal", 0), p.get("discount_amount", 0),
                     p.get("tax_amount", 0), p.get("total", 0), p.get("amount_paid", 0),
                     p.get("change_amount", 0), p.get("points_redeemed_amount", 0),
                     p.get("payment_method", "cash"), p.get("status", "completed"),
                     p.get("notes", ""), p.get("created_at"), p.get("due_date"),
                     p.get("uid"), p.get("actor_user_uid"), p.get("terminal_id"), p.get("created_at_utc")),
                )
            except sqlite3.IntegrityError as exc:
                # Aseel-parity wave A-PAR (schema v34), per-document-type
                # numbering series: `ON CONFLICT(uid)` only ever suppresses
                # a collision on `idx_sales_uid`. `sales.sale_number`
                # carries its OWN bare UNIQUE, and a claim race on
                # `doc_series` (see that entity type's own `_apply_event`
                # branch, "THE CLAIM-RACE TIE-BREAK") can leave the LOSING
                # device having already minted one or more numbers from a
                # book it turns out not to own -- those numbers are already
                # on paper by the time convergence resolves ownership.
                # Uncaught, this raises straight out of `_apply_event`
                # exactly the AUDIT-032B shape this feature exists to
                # prevent everywhere else: cursor never advances, every
                # row from every device stops arriving forever. Caught BY
                # NAME, matching the identical discipline the `user`/
                # `email` collision and the `doc_series`/`code` collision
                # both already use -- never widened to bare
                # IntegrityError.
                if "sales.sale_number" in str(exc):
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_document_number",
                        detail=f"sale_number={p.get('sale_number')!r} already exists locally "
                               f"under a different sale (incoming uid={p.get('uid')!r})")
                raise
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
            # Schema v39 (cost basis): `unit_cost` -- the per-line cost
            # SNAPSHOT create_sale takes at sale time (see that route's own
            # comment) -- was missing from this INSERT entirely, same
            # omission as the product branch's `avg_cost` above. `sale_item`
            # has no update event either, so this INSERT is the only write
            # path; without this a device applying a pulled sale showed a
            # cost of 0 for every line it did not ring itself.
            conn.execute(
                "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, discount_pct, "
                "tax_rate, line_total, unit_cost, uid) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (sale_local_id, p.get("product_id"), p.get("quantity"), p.get("unit_price"),
                 p.get("discount_pct", 0), p.get("tax_rate", 0), p.get("line_total"),
                 p.get("unit_cost", 0), p.get("uid")),
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
            # Cross-device double-payout fix -- the three-way split
            # (`tender_refund_amount`/`ar_forgiven_amount`/
            # `store_credit_amount`) exists precisely so a refund is never
            # counted as BOTH drawer cash AND debt forgiveness -- see
            # core/retail/returns_settlement.py's own module docstring.
            # retail_api.py's create_return() already puts all three on the
            # wire (`_queue_sync_event(cur, 'return', ...)`); this INSERT
            # used to omit them from its column list entirely, so every
            # SYNCED return landed at the column DEFAULT of 0 regardless of
            # what the sending device actually recorded. create_return's own
            # partial-return guard (`_prior` in retail_api.py) sums exactly
            # this column across a sale's completed returns to know how much
            # tender is still payable -- with it silently zeroed, a second
            # device's own return against the same sale sees NO prior claim
            # and reopens the WHOLE tender pool, doubling the cash payout
            # (measured: sale 2x50=100, 40 cash/60 AR, return 1 claims 40
            # tender; after syncing, a second till's return ALSO claims 40 --
            # 80 paid out against 40 ever collected).
            #
            # A CURRENT device's payload carries all three keys, used as-is.
            # An OLDER device's payload carries none of them, and the split
            # is genuinely unknowable from the wire -- the fallback below
            # treats the WHOLE refund as tender rather than guessing 0,
            # because `_prior[0]` (the tender half) only ever answers "how
            # much tender may still be paid out against this sale": guessing
            # 0 PERMITS a double payout (exactly this defect), while guessing
            # high merely REFUSES a later over-refund. Between paying twice
            # and refusing once, refusing is the correct failure direction
            # for money. `p.get(k)` alone, not `p.get(k, default)`, is
            # deliberate: `dict.get(key, default)` returns `None` -- not
            # `default` -- when `key` IS present with value `None`, so an
            # explicit JSON null must be folded into the same fallback as an
            # absent key via an explicit `is None` check, never silently
            # written as a bare NULL into a money column.
            #
            # This fallback cannot distort any drawer report: this INSERT
            # writes `session_id` as a literal NULL a few lines below (see
            # the VALUES list), and `_cash_session_report` (retail_api.py)
            # is session-scoped (`WHERE ... session_id=?`, and SQL NULL never
            # equals a real session id) -- a synced return never enters
            # another device's own X/Z report at all.
            _tender_refund_amount = p.get("tender_refund_amount")
            if _tender_refund_amount is None:
                _tender_refund_amount = p.get("refund_amount", 0)
            _ar_forgiven_amount = p.get("ar_forgiven_amount")
            if _ar_forgiven_amount is None:
                _ar_forgiven_amount = 0
            _store_credit_amount = p.get("store_credit_amount")
            if _store_credit_amount is None:
                _store_credit_amount = 0
            # DAY-1d review F1: the tax this return gave back is a business
            # fact (the sending device's books reversed exactly this much), so
            # it travels. An OLDER device's payload has no key: NULL, which the
            # tax report treats as "pre-v43 row" and falls back for. An explicit
            # null is the same NULL, never coerced to 0 (0 would claim "this
            # return reversed no tax").
            _tax_refunded = p.get("tax_refunded")
            try:
                conn.execute(
                    "INSERT INTO returns (company_id, return_number, sale_id, branch_id, cashier, reason, "
                    "refund_method, refund_amount, status, idempotency_key, created_at, session_id, "
                    "uid, actor_user_uid, terminal_id, created_at_utc, "
                    "tender_refund_amount, ar_forgiven_amount, store_credit_amount, tax_refunded) "
                    "VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                    (local_company_id, p.get("return_number"), sale_local_id, resolved_branch_id,
                     p.get("cashier", "POS"), p.get("reason", ""), p.get("refund_method", "cash"),
                     p.get("refund_amount", 0), p.get("status", "completed"), p.get("created_at"),
                     p.get("uid"), p.get("actor_user_uid"), p.get("terminal_id"), p.get("created_at_utc"),
                     _tender_refund_amount, _ar_forgiven_amount, _store_credit_amount,
                     _tax_refunded),
                )
            except sqlite3.IntegrityError as exc:
                # Aseel-parity wave A-PAR (schema v34) -- identical
                # reasoning to the "sale" branch's own catch above:
                # `returns.return_number` carries its own bare UNIQUE, and
                # a `doc_series` claim-race loser can have already minted
                # one under a book it turns out not to own.
                if "returns.return_number" in str(exc):
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_document_number",
                        detail=f"return_number={p.get('return_number')!r} already exists locally "
                               f"under a different return (incoming uid={p.get('uid')!r})")
                raise
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
            if event_type == "po_link":
                # W4a: attach a pre-v45 supplier payment to its purchase order
                # -- see `_apply_payment_po_link`.
                return self._apply_payment_po_link(conn, ev, p, local_company_id, conflict_sink)
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
                # v44: the void used to ALSO move `credit_balance` here (a
                # signed delta on the party), and that was the one apply path
                # that did -- sale/payment/return creates pulled from a peer
                # never touched the balance, so a receive-only computer read 30
                # where the shop was owed 80. The balance is now DERIVED from the
                # shop-wide rows (retail core/party_balance.py: voided payments
                # drop out of the sum) and refreshed after every pull by
                # retail_api.install_party_balance_refresh, so the status flip
                # below is all the engine has to do; moving the cache by a delta
                # here as well would double-count against the derivation.
                # The flip still happens on the TRANSITION only, so a replayed
                # or doubly-delivered event is a no-op.
                before = conn.execute(
                    "SELECT id, status, party_type, party_id, direction, amount, related_type, "
                    "related_id FROM payments WHERE uid=? AND company_id=?",
                    (p.get("uid"), local_company_id)).fetchone()
                if before is not None and (before["status"] or "active") != "voided":
                    conn.execute(
                        "UPDATE payments SET status='voided' WHERE uid=? AND company_id=?",
                        (p.get("uid"), local_company_id))
                    # W4a: an order's `amount_paid` is derived from its active
                    # payments, so the void must re-derive it here too -- the
                    # old stored increment never came back down on any computer.
                    if before["related_type"] == "po" and before["related_id"]:
                        self._refresh_po(conn, local_company_id, before["related_id"])
                    return True
                if before is not None:
                    return True  # already voided here: nothing to do, nothing to move
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
            # W4a: a PO instalment names its order by `po_uid`, never by the
            # writer's `related_id`. That integer is the WRITER's own
            # purchase_orders.id, which on this computer names an unrelated
            # order (or none) -- so every instalment used to land on the wrong
            # PO. Resolve the uid to THIS computer's id; park if the order
            # has not arrived (replayed once it does).
            related_id = p.get("related_id")
            po_local_id = None
            if p.get("related_type") == "po":
                if p.get("po_uid"):
                    po_local_id = self._local_po_id(conn, local_company_id, p.get("po_uid"))
                    if po_local_id is None:
                        return self._quarantine_apply_event(
                            conn, ev, reason="missing_parent:purchase_order",
                            detail=f"po_uid={p.get('po_uid')!r} not found locally")
                # A pre-v45 writer sends no `po_uid`: the integer it sent means
                # nothing here, so the payment is stored with NO order (money
                # still counts toward the supplier's balance, which is keyed on
                # party_id). A later `po_link` event, emitted by that writer's
                # upgrade backfill, attaches it. NULL is the safe answer;
                # keeping the writer's integer was the original bug.
                related_id = po_local_id
            conn.execute(
                "INSERT INTO payments (company_id, reference, party_type, party_id, direction, amount, "
                "currency, fx_rate, method, related_type, related_id, sale_id, notes, status, "
                "created_by, device, created_at, uid) "
                "VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("reference"), p.get("party_type"), p.get("party_id"),
                 p.get("direction"), p.get("amount", 0), p.get("currency", "USD"), p.get("method", "cash"),
                 p.get("related_type"), related_id, sale_local_id, p.get("notes", ""),
                 p.get("status", "active"), p.get("created_by"), p.get("device"), p.get("created_at"),
                 p.get("uid")),
            )
            if po_local_id is not None:
                self._refresh_po(conn, local_company_id, po_local_id)
        elif entity_type == "purchase_order_receipt":
            if event_type != "create":
                return True
            return self._apply_po_receipt(
                conn, ev, p, local_company_id, branch_fallback_sink, conflict_sink)
        elif entity_type == "purchase_order_receipt_line":
            if event_type != "create":
                return True
            return self._apply_po_receipt_line(conn, ev, p, local_company_id, conflict_sink)
        elif entity_type == "purchase_order":
            # W4a (schema v45). The header of a purchase order another
            # computer typed. `create` is the immutable fact (number, supplier,
            # totals, order journal uid); `update` carries ONLY the status
            # transition. `amount_paid`/`payment_status` never ride the wire --
            # they are re-derived from the synced payments below.
            if event_type == "create":
                return self._apply_purchase_order_create(
                    conn, ev, p, local_company_id, branch_fallback_sink)
            if event_type == "update":
                return self._apply_purchase_order_update(
                    conn, ev, p, local_company_id, conflict_sink)
            return True
        elif entity_type == "purchase_order_item":
            if event_type != "create":
                return True
            # W4a verify F4: the parent is looked up INSIDE the receiver's company.
            po_row = conn.execute(
                "SELECT id, uid, status, branch_id, total, received_at, created_at, item_count "
                "FROM purchase_orders WHERE uid=? AND company_id=?",
                (p.get("po_uid"), local_company_id)
            ).fetchone() if p.get("po_uid") else None
            if po_row is None:
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:purchase_order",
                    detail=f"po_uid={p.get('po_uid')!r} not found locally")
            # `purchase_order_items.product_id -> products(id)` is a real FK
            # on an INTEGER NOT NULL column -- same wedge, same parking, as
            # the sale_item branch above (see its comment for the reasoning).
            if not self._row_exists(conn, "products", p.get("product_id")):
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:product",
                    detail=f"product_id={p.get('product_id')!r} not found locally")
            conn.execute(
                "INSERT INTO purchase_order_items (po_id, product_id, quantity, unit_cost, total, "
                "received_qty, uid) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (po_row["id"], p.get("product_id"), p.get("quantity"), p.get("unit_cost"),
                 p.get("total"), p.get("received_qty") or 0, p.get("uid")),
            )
            # W4a-2: received_qty / status are DERIVED from receipts now, so the
            # old "parent already received -> received_qty=quantity" shortcut is
            # gone. Its one legitimate case -- a line parked on a missing
            # product and replayed AFTER a v45 writer's {status:'received'}
            # event synthesized the legacy receipt -- joins that receipt here
            # (same uids every computer derives); otherwise the order would
            # read 'partial' forever.
            if conn.execute(
                    "SELECT 1 FROM purchase_order_receipts WHERE po_id=? AND company_id=? "
                    "AND legacy=1", (po_row["id"], local_company_id)).fetchone():
                self._ensure_legacy_receipt(
                    conn, local_company_id, po_row, only_item_uid=p.get("uid"))
            self._refresh_po(conn, local_company_id, po_row["id"])
        elif entity_type == "stock_transfer":
            # W3b (schema v47): the header of a transfer another computer
            # typed. METADATA ONLY -- never moves stock, never posts books.
            if event_type == "create":
                return self._apply_stock_transfer_create(
                    conn, ev, p, local_company_id, branch_fallback_sink)
            if event_type == "update":
                return self._apply_stock_transfer_update(
                    conn, ev, p, local_company_id, conflict_sink)
            return True
        elif entity_type == "stock_transfer_item":
            if event_type == "create":
                return self._apply_stock_transfer_item_create(conn, ev, p, local_company_id)
            if event_type == "update":
                return self._apply_stock_transfer_item_update(
                    conn, ev, p, local_company_id, conflict_sink)
            return True
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
            # PARKED-CHILDREN RULE (see `_movement_source_parked_reason`): a
            # movement whose sale/return is parked here (or, for a payload
            # that names its source, absent) is parked WITH it. Applying it
            # alone moves stock for a sale this device does not hold.
            parked_reason = self._movement_source_parked_reason(conn, p)
            if parked_reason is not None:
                return self._quarantine_apply_event(
                    conn, ev, reason=parked_reason,
                    detail=f"inventory_movement uid={p.get('uid')!r} belongs to a "
                           f"{parked_reason.split(':', 1)[1]} that is parked or absent here "
                           f"(source_uid={p.get('source_uid')!r}, reference={p.get('reference')!r}, "
                           f"terminal_id={p.get('terminal_id')!r})")
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
                # Schema v40 (per-branch cost basis): maintain `inventory_
                # balances.avg_cost` the same way the two LOCAL write paths
                # already do (api/retail_api.py's receive_purchase_order and
                # receive_stock_transfer) -- see database/schema.py's v40
                # migration docstring, "NOT MAINTAINED BY THIS MIGRATION",
                # for the gap this closes: a device that only ever learns a
                # branch's stock THROUGH SYNC -- never through a local
                # receipt or a local transfer-receive at that branch --
                # never converged its own avg_cost otherwise.
                #
                # Only BLENDING_MOVEMENT_TYPES (opening_stock/purchase_in/transfer_in/return_in) weight into the average --
                # the only two movement_types that EVER write inventory_
                # balances.avg_cost locally. A movement that REMOVES stock
                # ('sale_out', 'transfer_out') does not change what the
                # remaining stock cost, even though it too carries a real
                # unit_cost on the wire now, so it is deliberately excluded.
                #
                # `unit_cost` 0 means "no cost information" (a manual
                # adjustment, an opening count, or a pre-v40 transfer_in
                # replayed from before this column existed) and must not
                # drag a real average toward zero -- skip the update
                # entirely rather than weight in a false zero.
                #
                # Nested inside this SAME `if cur.rowcount:` as the quantity
                # update below -- HAZARD 1's own guard -- so a replayed event
                # (a `DO NOTHING` conflict on the movement INSERT above)
                # moves this average exactly as many times as it moves the
                # quantity: zero.
                incoming_unit_cost = p.get("unit_cost") or 0
                if p.get("movement_type") in BLENDING_MOVEMENT_TYPES and incoming_unit_cost:
                    # Read BEFORE the quantity upsert two statements below
                    # moves `quantity_on_hand` -- weighting the stock already
                    # on hand against the stock THIS movement carries would
                    # double-count this movement's own quantity if read
                    # after, the identical ordering both local write paths
                    # already follow. Seed a zero row first (INSERT OR
                    # IGNORE) so the SELECT always finds one, matching
                    # receive_purchase_order/receive_stock_transfer's own
                    # "seed-then-read-then-update" shape for a branch that
                    # has never stocked this product before.
                    conn.execute(
                        "INSERT OR IGNORE INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand) "
                        "VALUES (?,?,?,0)",
                        (local_company_id, p.get("product_id"), resolved_branch_id),
                    )
                    bal_row = conn.execute(
                        "SELECT quantity_on_hand, avg_cost FROM inventory_balances "
                        "WHERE company_id=? AND product_id=? AND branch_id=?",
                        (local_company_id, p.get("product_id"), resolved_branch_id),
                    ).fetchone()
                    # STOCK-BOOKS verify F1: the arithmetic is the ONE shared rule
                    # (commercial_runtime/stock_cost.py), the same call the local
                    # writers make -- opening_stock and return_in blend too, and
                    # free/negative prior stock takes the incoming cost. The
                    # result is quantized at 0.0001 (this module has no currency).
                    new_avg = blended_unit_cost(
                        bal_row[0], bal_row[1], p.get("quantity"), incoming_unit_cost)
                    new_avg_cost = float(new_avg.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP))
                    conn.execute(
                        "UPDATE inventory_balances SET avg_cost=? "
                        "WHERE company_id=? AND product_id=? AND branch_id=?",
                        (new_avg_cost, local_company_id, p.get("product_id"), resolved_branch_id),
                    )
                conn.execute(
                    "INSERT INTO inventory_balances (company_id, product_id, branch_id, quantity_on_hand) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT(company_id, product_id, branch_id) "
                    "DO UPDATE SET quantity_on_hand = quantity_on_hand + excluded.quantity_on_hand",
                    (local_company_id, p.get("product_id"), resolved_branch_id, p.get("quantity")),
                )
                # STOCK EXCEPTIONS (launch-readiness Phase 7 stage 7d-i,
                # docs/launch-readiness/phase7-offline-ux.md "Decision 4";
                # ROADMAP.md's 2026-08-28 "retail schema v20 CLAIMED"
                # entry). Detection and recording only -- this stage
                # relaxes no guard and refuses nothing new; `create_sale`'s
                # `qty > on_hand` check upstream is untouched.
                #
                # Deliberately INSIDE the `if cur.rowcount:` block, not
                # after it, for the identical reason HAZARD 1 above gates
                # the balance update itself on the same condition: a
                # `DO NOTHING` conflict (a replayed batch, a re-pulled
                # cursor range, a retried quarantine row that resolved on
                # an earlier pass) means this movement's effect on the
                # balance was ALREADY accounted for by whichever earlier
                # apply actually inserted it. Re-checking the balance and
                # re-recording/refreshing an exception on a replay would
                # re-stamp `detected_at_utc` with the CURRENT wall-clock
                # time even though nothing about the shop's stock changed
                # this call -- an exception that appears to keep
                # rediscovering itself on every retried pull tick, which
                # is worse than not detecting it promptly, because it
                # teaches an owner to stop trusting the timestamp.
                #
                # Read back rather than derived from `p.get("quantity")`
                # here: the upsert above ADDS a signed delta to whatever
                # was already in the row, so the resulting balance is a
                # fact about the row, not about this one movement.
                new_balance = conn.execute(
                    "SELECT quantity_on_hand FROM inventory_balances "
                    "WHERE company_id=? AND product_id=? AND branch_id=?",
                    (local_company_id, p.get("product_id"), resolved_branch_id),
                ).fetchone()[0]
                if new_balance < 0:
                    self._record_or_refresh_stock_exception(
                        conn, local_company_id=local_company_id,
                        product_id=p.get("product_id"), branch_id=resolved_branch_id,
                        observed_quantity_on_hand=new_balance,
                    )
                # No `else` branch that clears an exception when the
                # balance is >= 0 -- Decision 4 is explicit that nothing in
                # this stage may auto-resolve or delete one. A balance that
                # recovers (a later merge, a manual correction, a
                # replenishment) leaves any already-open row exactly as it
                # was: still open, still visible, until a human resolves it
                # (stage 7d-ii, gated CAP_STOCK_ADJUST).
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
            # Schema v42: address the branch by its CANONICAL uid. If this
            # device still holds the branch under an OLD (aliased) uid and has
            # no row for the canonical one, update that row in place instead
            # of inserting a second copy of the same shop branch.
            _b_uid = p.get("uid")
            _b_canon = _canonical_branch_uid(conn, _b_uid, local_company_id)
            if _b_canon and _b_canon != _b_uid:
                # P2S M3b. This event reached the canonical row THROUGH an
                # alias. That is how a placeholder branch converges on the
                # default one, and a converging placeholder is called 'Main
                # Branch' / 'Main Store'. Anything else would let an alias
                # rename (or, with no row yet, name) the shop's Main branch
                # after a DIFFERENT branch -- the vYM 'North Branch' probe.
                # Events that name the canonical uid itself are unaffected.
                if p.get("name") not in _PLACEHOLDER_BRANCH_NAMES:
                    return self._quarantine_apply_event(
                        conn, ev, reason="branch_alias_conflict",
                        detail=f"branch event for aliased uid {_b_uid!r} would rename the "
                               f"canonical branch {_b_canon!r} to {p.get('name')!r}")
                _b_uid = _b_canon
            _b_have = conn.execute(
                "SELECT 1 FROM branches WHERE uid = ? AND CAST(company_id AS TEXT) = ?",
                (_b_uid, str(local_company_id))).fetchone() if _b_uid else True
            if not _b_have:
                _b_old = None
                try:
                    _b_old = conn.execute(
                        "SELECT b.id FROM branches b JOIN branch_uid_aliases a "
                        "ON a.alias_uid = b.uid WHERE a.canonical_uid = ? "
                        "AND CAST(a.company_id AS TEXT) = ? "
                        "AND CAST(b.company_id AS TEXT) = ? ORDER BY b.id LIMIT 1",
                        (_b_uid, str(local_company_id), str(local_company_id))).fetchone()
                except sqlite3.Error:
                    _b_old = None
                if _b_old:
                    conn.execute(
                        "UPDATE branches SET name=?, address=?, phone=?, status=? WHERE id=?",
                        (p.get("name"), p.get("address", ""), p.get("phone", ""),
                         p.get("status", "active"), _b_old[0]))
                    return True
            conn.execute(
                "INSERT INTO branches (company_id, name, address, phone, status, uid) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO UPDATE SET "
                "name=excluded.name, address=excluded.address, phone=excluded.phone, status=excluded.status",
                (local_company_id, p.get("name"), p.get("address", ""), p.get("phone", ""),
                 p.get("status", "active"), _b_uid),
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
            #
            # `branch_scope_uid` (registry v7, account-hierarchy design §9
            # item 7) IS in the plain allowlist and DOES get a plain
            # `excluded.branch_scope_uid` -- unlike `session_version`, there
            # is no MAX/ordering subtlety here: it is an ordinary field that
            # moves with `row_version` like `role`/`status`/every other
            # column in this list, so a scope set (or cleared) on the
            # owner's device reaches every other till within the sync
            # cadence, the same way a role change already does. A payload
            # from a pre-v7 emitter simply omits the key -- `p.get(...)`
            # then supplies `None`, which is exactly what NULL already
            # means ("every branch"), so an old sender can never force a
            # scope onto a receiver that only understands NULL.
            # L3 (2026-09-29 security review): the text rule runs on RECEIPT, so
            # a hostile or corrupt peer cannot store a name the local routes
            # would refuse (bidi overrides, line breaks, an unknown preset).
            # Returns a copy and never raises.
            p = sanitize_synced_user_fields(p)
            uid = p.get("uid")
            # Review V3r G4: `ON CONFLICT(uid)` below is not company-scoped, and a
            # registry can hold users of more than one company id (the owner-change
            # rule, `_local_admin_uids`, is company-scoped, so another company's
            # owner is invisible to it). A uid that already belongs to a DIFFERENT
            # company is never written -- not by the harmless path, not by the upsert.
            foreign = conn.execute(
                "SELECT 1 FROM users WHERE uid=? AND company_id<>?", (uid, local_company_id)).fetchone()
            if foreign is not None:
                return self._quarantine_apply_event(
                    conn, ev, reason=_authority.REASON_FOREIGN_COMPANY_USER,
                    detail=f"user {ascii(str(uid))} belongs to a different company on this computer")
            if ev.get("_authority_harmless_owner_edit") is True:
                # AUDIT-033 fix round (verifier V finding #3). A non-main computer
                # may edit an owner's COSMETIC fields (the authority rule checked
                # that no critical field changed), but a full upsert would also
                # carry its `row_version`, and a huge one would make this receiver
                # ignore every later change the main sends for that owner (the main
                # sends local+1). A cap per event cannot work (a burst of +1 events
                # each passes against the receiver's then-current value), so the
                # owner row's version moves ONLY on main-signed events: this path
                # writes display_name and language and nothing else -- never
                # row_version, updated_at_utc, employee_id or a critical column.
                # Last arrival wins on these two fields, which is acceptable.
                conn.execute(
                    "UPDATE users SET display_name=CASE WHEN ? THEN ? ELSE display_name END, "
                    "language=COALESCE(?, language) WHERE uid=? AND company_id=?",
                    (1 if "display_name" in p else 0, p.get("display_name"), p.get("language"),
                     uid, local_company_id))
                return True
            # 2026-09-05, the owner's decision "one owner login on every
            # device": the employee CODE is a per-device display label that
            # every install mints on its own (`ADMIN-0001` at first run,
            # `EMP-{count+1}` per hire), never the wire identity -- `uid`
            # is. So a code that collides with a DIFFERENT person locally is
            # re-numbered here, not refused: before this, the owner's own
            # account arrived on every second device as
            # `duplicate_employee_id` and was parked, and its eight
            # permission events piled up behind it as `missing_parent:user`.
            employee_id = self._resolve_employee_code(conn, local_company_id, uid, p.get("employee_id"))
            # Registry v9 (Track S): `display_name` and `role_preset` ride the
            # user event, but a v8 sender never writes either key. An ABSENT
            # key must PRESERVE the local value -- a bare
            # `display_name=excluded.display_name` would let every PIN change
            # made on an old build blank the person's name and their
            # Accountant label on every new build (mutation-proved in
            # retail_s_mixed_build_twodevice_test.py). The has-flags come from
            # identity's own pure plan so the rule lives in one place.
            local_role_row = conn.execute(
                "SELECT role, role_preset FROM users WHERE uid=? AND company_id=?", (uid, local_company_id)
            ).fetchone()
            user_plan = user_sync_apply_plan(
                p, local_role_row[0] if local_role_row else None,
                local_role_row[1] if local_role_row else None)
            # AUDIT-033 fix round (verifier V finding #4). A MAIN-signed event that
            # makes someone an owner beats a member-written row of that uid, even
            # one with a higher row_version -- otherwise any member who writes the
            # shop owner's uid first (before the main's real account arrives on a
            # joining computer) squats it for good. `users.main_row_version`
            # (device-local, never synced) is the highest version this receiver ever
            # applied from a main-signed event; the override needs the incoming
            # version to be ABOVE it (so replaying an old "X is admin" after the main
            # demoted X cannot win) and the local row to have moved past it (it was
            # written by someone else since). Ordinary users are never overridden, so
            # concurrent member and main edits keep converging by version. A
            # fixture registry without the column gets no override and no stamp.
            signer_is_main = ev.get("_authority_signer_is_main") is True
            has_main_rv = any(r[1] == "main_row_version"
                              for r in conn.execute("PRAGMA table_info(users)").fetchall())
            override = False
            if (has_main_rv and signer_is_main and local_role_row
                    and normalize_role(p.get("role")) == ROLE_ADMIN
                    and normalize_role(local_role_row[0]) != ROLE_ADMIN):
                cur_row = conn.execute(
                    "SELECT row_version, main_row_version FROM users WHERE uid=? AND company_id=?",
                    (uid, local_company_id)).fetchone()
                if cur_row is not None:
                    incoming_rv = self._as_int(p.get("row_version", 1), 0)
                    main_rv = cur_row[1]
                    override = (incoming_rv > (main_rv or 0)
                                and (main_rv is None or self._as_int(cur_row[0], 0) > main_rv))
            try:
                user_cursor = conn.execute(
                    "INSERT INTO users (id, company_id, uid, employee_id, email, role, status, "
                    "require_password_change, language, password_hash, pin_hash, row_version, "
                    "updated_at_utc, session_version, branch_scope_uid, display_name, role_preset) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(uid) WHERE uid IS NOT NULL DO UPDATE SET "
                    "display_name=CASE WHEN ? THEN excluded.display_name ELSE users.display_name END, "
                    "role_preset=CASE WHEN ? THEN excluded.role_preset ELSE users.role_preset END, "
                    "email=excluded.email, employee_id=excluded.employee_id, role=excluded.role, "
                    "status=excluded.status, require_password_change=excluded.require_password_change, "
                    "language=excluded.language, password_hash=excluded.password_hash, "
                    "pin_hash=excluded.pin_hash, row_version=excluded.row_version, "
                    "updated_at_utc=excluded.updated_at_utc, "
                    "session_version=MAX(COALESCE(users.session_version,0), COALESCE(excluded.session_version,0)), "
                    "branch_scope_uid=excluded.branch_scope_uid "
                    "WHERE excluded.row_version > users.row_version OR ?",
                    (str(uuid.uuid4()), local_company_id, uid, employee_id, p.get("email"),
                     p.get("role", "cashier"), p.get("status", "active"),
                     p.get("require_password_change", 1), p.get("language", "en"),
                     p.get("password_hash"), p.get("pin_hash"), p.get("row_version", 1),
                     p.get("updated_at_utc"), p.get("session_version", 1), p.get("branch_scope_uid"),
                     p.get("display_name"), p.get("role_preset"),
                     1 if user_plan["has_display_name"] else 0,
                     1 if user_plan["has_role_preset"] else 0,
                     1 if override else 0),
                )
                if has_main_rv and signer_is_main and user_cursor.rowcount:
                    conn.execute(
                        "UPDATE users SET main_row_version=? WHERE uid=? AND company_id=?",
                        (self._as_int(p.get("row_version", 1), 1), uid, local_company_id))
                if user_plan["reset_books"] and user_cursor.rowcount:
                    # An OLD sender changed this person's role. It re-seeded
                    # only the eight codes it knows, so the four books rows
                    # here would keep what the old role had (an ex-Accountant
                    # would stay one). Fail closed: rewrite them to the new
                    # role's defaults. NO emit -- every device derives the
                    # same answer from the same event. Gated on the upsert
                    # having actually applied (row_version won): a stale
                    # event must not reset anything.
                    reset_user_id = self._local_id_by_uid(conn, "users", uid)
                    for books_code, books_level in user_plan["books_plan"].items():
                        conn.execute(
                            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) "
                            "VALUES (?,?,?,?) ON CONFLICT(user_id, subsystem) "
                            "DO UPDATE SET access_level=excluded.access_level",
                            (str(uuid.uuid4()), reset_user_id, books_code, books_level),
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
                # A user event missing a REQUIRED field (null password_hash /
                # employee_id / email) is a malformed payload, not a
                # collision and not a code bug worth wedging the stream for:
                # the registry stream is ordered, so raising here stopped
                # every user/permission event queued behind it, for every
                # device, until an operator intervened (vYM2 finding). Park
                # it, keep going. This check MUST come before the
                # `users.email` match below: SQLite words a null email as
                # "NOT NULL constraint failed: users.email", which contains
                # "users.email" and used to be mislabelled duplicate_email.
                # Only the column name goes in `detail`, never a value --
                # the event may carry credential hashes.
                if msg.startswith("NOT NULL constraint failed"):
                    return self._quarantine_apply_event(
                        conn, ev, reason="malformed_user",
                        detail=f"{msg} (incoming uid={uid!r}); the event is missing a required field")
                if msg.startswith("UNIQUE constraint failed") and "users.email" in msg:
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_email",
                        detail=f"email={p.get('email')!r} already registered to a different "
                               f"account locally (incoming uid={uid!r})")
                if "users.company_id, users.employee_id" in msg:
                    # Last resort only since 2026-09-05: `_resolve_employee_
                    # code` above re-numbers a colliding code before the
                    # INSERT, so this is reachable only if a local write
                    # takes the chosen code between that SELECT and this
                    # statement. Parked, retried on the next pull, resolves
                    # to the next free number then.
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_employee_id",
                        detail=f"employee_id={p.get('employee_id')!r} already registered under "
                               f"company_id={local_company_id!r} (incoming uid={uid!r})")
                # Deliberately LOUD (see the comment above): tell
                # `_apply_event_guarded` this one is not a malformed-payload
                # case to park, so its blanket IntegrityError net lets it out.
                exc._aura_loud_integrity = True
                raise
            # H3 (2026-09-29 security review): AUDIT-032 on the receiving side.
            # A role change (or a first arrival of this person) can leave a
            # non-admin holding both cash.close and cash.approve; the local
            # routes refuse that, sync must too. Same transaction, no emit;
            # it only ever narrows access, so it runs even when the row_version
            # gate made the upsert above a no-op.
            enforce_cash_pair_after_apply(conn, local_company_id, uid)
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
            # Review V4r N3 (the twin of V3r G4 on `user`): the uid resolves across
            # EVERY company in this registry, but the authority rule protects only
            # THIS company's owners, so a member could grant or revoke permissions
            # of another company's user. A uid owned by a different company is
            # never written.
            if local_company_id is not None and conn.execute(
                    "SELECT 1 FROM users WHERE id=? AND company_id<>?",
                    (local_user_id, local_company_id)).fetchone() is not None:
                return self._quarantine_apply_event(
                    conn, ev, reason=_authority.REASON_FOREIGN_COMPANY_USER,
                    detail=f"user {ascii(str(user_uid))} belongs to a different company on this computer")
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
            #
            # AUDIT-033 B2b: a signed `_reannounce` is the main computer RE-STATING
            # a grant after an upgrade (so a device that joined later, and parked
            # every pre-upgrade unsigned grant, can get them). With no version to
            # order it by, the only safe reading is "insert if I lack it, never
            # overwrite": a re-statement built from the main's view could
            # otherwise revert an edit the main has not pulled yet.
            on_conflict = ("DO NOTHING" if p.get("_reannounce") is True
                           else "DO UPDATE SET access_level=excluded.access_level")
            conn.execute(
                "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id, subsystem) " + on_conflict,
                (str(uuid.uuid4()), local_user_id, subsystem, p.get("access_level", "none")),
            )
            # H3: two permission events (cash.close, then cash.approve) or two
            # devices' racing edits can add up to the pair the local routes
            # refuse. Drop cash.approve here, same transaction, no emit.
            enforce_cash_pair_after_apply(conn, local_company_id, user_uid)
        elif entity_type == "device":
            # Registry v9 (Track S). Only ever reachable from the REGISTRY
            # stream (`devices` lives in registry.db). Every decision --
            # insert / update / touch / skip / quarantine, "revoked is
            # sticky", the main-computer flag recompute -- is identity's own
            # `apply_device_event`; this branch only routes its verdict.
            # `local_company_id` is the receiver's (the payload has no
            # company_id at all). A device is NEVER deleted by sync.
            if event_type not in ("create", "update"):
                return True
            try:
                device_plan = apply_device_event(conn, local_company_id, p)
            except ValueError as exc:
                # No device_id: no retry can fix a payload shape, but
                # dropping it silently would hide a broken peer. Parked so an
                # operator can see it.
                return self._quarantine_apply_event(
                    conn, ev, reason="malformed_device", detail=str(exc))
            if device_plan["action"] == "quarantine":
                detail = None
                if device_plan["reason"] == "duplicate_device_fingerprint":
                    # `device_has_open_clash` matches on entity_id OR a
                    # detail naming the device. The event's own entity_id is
                    # the INCOMING device; the clash is also about the LOCAL
                    # device that already owns the fingerprint, and THAT one
                    # must not be handed the main role either -- so name it.
                    local_owner = conn.execute(
                        "SELECT id FROM devices WHERE device_fingerprint=?",
                        (p.get("device_fingerprint"),),
                    ).fetchone()
                    detail = (f"incoming device {p.get('device_id')!r} shares a fingerprint "
                              f"with local device {local_owner[0] if local_owner else None!r}")
                else:
                    detail = (f"device {p.get('device_id')!r} already exists here "
                              f"under a different owner installation or company")
                return self._quarantine_apply_event(
                    conn, ev, reason=device_plan["reason"], detail=detail)
        elif entity_type == "admin_device":
            # Registry v9 (Track S): the "main computer" assignment. Ordered
            # by epoch (then earlier assigned_at, then smaller device id) --
            # `apply_admin_assignment` owns that rule, stores an assignment
            # whose device row has not arrived yet, and recomputes the flag.
            # It must NEVER emit from here: every device reaches the same
            # answer from the same events, and an echo would loop.
            if event_type not in ("create", "update"):
                return True
            try:
                # Epoch poisoning: identity accepts any positive integer, so
                # an assignment at epoch 2**63-1 would be stored and win
                # forever -- and the next legitimate takeover (epoch+1) could
                # then not even be written (OverflowError), freezing the main
                # computer for good. One assignment is made per deliberate
                # takeover; 2**31 of them is not reachable, so anything past
                # it is a hostile or corrupt payload and is parked, not stored.
                claimed_epoch = int(p.get("epoch"))
                if claimed_epoch > _MAX_SYNCED_ADMIN_EPOCH:
                    raise ValueError(
                        f"admin_device epoch {claimed_epoch} is beyond any real takeover count")
                apply_admin_assignment(conn, local_company_id, p)
            except ValueError as exc:
                return self._quarantine_apply_event(
                    conn, ev, reason="malformed_admin_device", detail=str(exc))
        elif entity_type == "cheque":
            # Aseel-parity wave A-PAR (schema v32). `cheques` is an
            # IMMUTABLE header -- see core/retail/cheques.py's own module
            # docstring for the full "why status is not a column" story.
            # `create` ONLY, the sale/inventory_movement posture: nothing
            # else is ever emitted for this entity type today, and refusing
            # anything else is the safe default every other immutable-fact
            # branch in this method already takes.
            if event_type != "create":
                return True
            resolved_branch_id = self._resolve_branch_id(
                conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
            conn.execute(
                "INSERT INTO cheques (id, company_id, branch_id, direction, party_type, party_id, "
                "cheque_number, bank_name, bank_branch, drawer_name, account_number, amount, currency, "
                "issue_date, due_date, sale_id, related_type, related_id, notes, created_by, "
                "created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (p.get("id"), local_company_id, resolved_branch_id, p.get("direction"),
                 p.get("party_type"), p.get("party_id"), p.get("cheque_number"), p.get("bank_name"),
                 p.get("bank_branch"), p.get("drawer_name"), p.get("account_number"), p.get("amount", 0),
                 p.get("currency", "USD"), p.get("issue_date"), p.get("due_date"), p.get("sale_id"),
                 p.get("related_type"), p.get("related_id"), p.get("notes", ""),
                 p.get("created_by", "System"), p.get("created_at_utc")),
            )
        elif entity_type == "cheque_event":
            # `cheque_events` is APPEND-ONLY -- `create` ONLY, same posture
            # as `cheque` immediately above (and for the identical reason:
            # nothing else is ever emitted). NEITHER branch contains any
            # business logic -- no status, no legality check, no
            # row_version, no `_record_sync_conflict` -- because the apply
            # side stores no derived state to protect; `fold()`
            # (core/retail/cheques.py) recomputes status from whatever set
            # of events this device holds, on every read.
            if event_type != "create":
                return True
            # The parent check is real, not defensive theatre: the header
            # and its own first event are TWO separate outbox rows, and
            # Owner's push-side quarantine (681b0fa) can split a parent from
            # a child event just as easily as it can split a sale from its
            # sale_items -- same "missing_parent" vocabulary, same
            # `_row_exists`/`_quarantine_apply_event` pair the sale_item/
            # return_item branches above already use, and the same
            # `_retry_quarantined_events` tick replays a split pair once the
            # header arrives.
            if not self._row_exists(conn, "cheques", p.get("cheque_id")):
                return self._quarantine_apply_event(
                    conn, ev, reason="missing_parent:cheque",
                    detail=f"cheque_id={p.get('cheque_id')!r} not found locally")
            conn.execute(
                "INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, "
                "occurred_on, bank_reference, reason, to_party_type, to_party_id, created_by, "
                "created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (p.get("id"), p.get("cheque_id"), p.get("event_type"), p.get("from_status"),
                 p.get("to_status"), p.get("occurred_on"), p.get("bank_reference"), p.get("reason"),
                 p.get("to_party_type"), p.get("to_party_id"), p.get("created_by", "System"),
                 p.get("created_at_utc")),
            )
        elif entity_type == "quotation":
            # Aseel-parity wave A-PAR (schema v33). Shaped on `reorder_
            # request` immediately above for its reject-stale gate (a
            # MUTABLE header, unlike cheques) and on `sale`/`sale_item` for
            # branch resolution and missing-parent quarantine. `create` AND
            # `update` both arrive for this entity -- `create` at SEND only
            # (carrying the frozen line set), every later transition
            # (accept/decline/cancel/convert) as `update` -- see database/
            # schema.py's `_migrate_add_sales_quotations` docstring's
            # "DRAFTS DO NOT SYNC" section. There is no `delete` event type;
            # a quotation is withdrawn via `status='cancelled'`, never
            # removed.
            if event_type not in ("create", "update"):
                return True

            # AUDIT-032C (DEFECT 3), the identical fix `sale`/`cheque` above
            # already apply: `p.get("branch_id")` is the SENDING device's own
            # local integer and means nothing here. `branch_uid` is resolved
            # to THIS device's own local `branches.id` instead -- NEVER the
            # raw payload integer (the `reorder_request` branch's own defect,
            # closed here rather than repeated).
            resolved_branch_id = self._resolve_branch_id(
                conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)

            # B2: missing-parent quarantine for the FROZEN line set a
            # `create` event carries -- the identical helper and the
            # identical `missing_parent:<thing>` vocabulary the `sale_item`
            # branch above uses, checked BEFORE any write so an unresolvable
            # product is PARKED (visible, replayable) rather than allowed to
            # raise `sqlite3.IntegrityError` and wedge the whole batch behind
            # it forever. An `update` event never carries a `lines` key at
            # all (quotations are immutable past SEND, so nothing after
            # `create` ever needs to touch a line) -- `p.get("lines") or []`
            # makes this loop a genuine no-op for every update, not merely an
            # empty one.
            for ln in (p.get("lines") or []):
                if not self._row_exists(conn, "products", ln.get("product_id")):
                    return self._quarantine_apply_event(
                        conn, ev, reason="missing_parent:product",
                        detail=f"product_id={ln.get('product_id')!r} not found locally")

            # C5's REAL exposure (the submitted design's own test 7 was
            # withdrawn as proof of a different thing -- see
            # retail_quotation_test.py's test_second_conversion_is_refused_
            # by_the_status_check for what THAT test actually proves).
            # `BEGIN IMMEDIATE` + the status pre-check in create_sale's
            # conversion block cover ONE database; they cannot cover two
            # ONLINE tills that each convert the SAME quotation before
            # either's event has arrived at the other. Both resulting sales
            # are real, both took real money, and neither may be silently
            # un-linked -- so a genuine double-conversion is made VISIBLE
            # through the existing `sync_conflicts` table (a SECOND use of
            # one mechanism, not a new one) rather than prevented, which is
            # not something this code path can honestly do.
            #
            # Detected BEFORE the header upsert below: read the LOCAL row's
            # own `converted_sale_uid` first, and if it is already non-NULL
            # and the INCOMING payload names a DIFFERENT non-NULL sale, this
            # is exactly that collision. The fix is not to drop the whole
            # event -- the rest of this header (status/dates/etc.) may still
            # be a legitimate, later write that must still apply -- it is to
            # drop ONLY the three conversion-linked columns from what this
            # event is allowed to overwrite, so the LOCAL sale keeps its own
            # link forever.
            header_columns = [
                "status", "sent_at", "accepted_at", "declined_at", "cancelled_at", "converted_at",
                "converted_sale_uid", "conversion_variance_json", "valid_until", "notes",
                "subtotal", "discount_amount", "tax_amount", "total",
            ]
            changed_fields = p.get("_changed_fields")
            local_conversion = conn.execute(
                "SELECT converted_sale_uid, row_version FROM sales_quotations WHERE id=?",
                (p.get("id"),)
            ).fetchone()
            incoming_sale_uid = p.get("converted_sale_uid")
            conversion_columns = ("converted_sale_uid", "converted_at", "conversion_variance_json")
            if (local_conversion is not None and local_conversion["converted_sale_uid"]
                    and incoming_sale_uid and incoming_sale_uid != local_conversion["converted_sale_uid"]):
                self._record_sync_conflict(
                    conn, conflict_sink, local_company_id=local_company_id,
                    entity_type="quotation", entity_id=p.get("id"), event_type="double_conversion",
                    local_row_version=local_conversion["row_version"],
                    incoming_row_version=p.get("row_version"), payload=p,
                )
                # `changed_fields is not None` -- a modern, delta-aware event
                # -- simply drops the three conversion columns from the list
                # it already names. `changed_fields is None` -- a legacy/
                # full-snapshot event, which `_delta_set_clause` would
                # otherwise read as "every column changed" -- names every
                # OTHER column explicitly instead, which is the only way to
                # keep the "carry everything except these three" contract
                # when there is no delta list to subtract from.
                if changed_fields is not None:
                    changed_fields = [c for c in changed_fields if c not in conversion_columns]
                else:
                    changed_fields = [c for c in header_columns if c not in conversion_columns]

            delta_frag, delta_binds = self._delta_set_clause(
                "sales_quotations", header_columns, changed_fields)
            raw_row_version = p.get("row_version")
            insert_row_version = raw_row_version if raw_row_version is not None else 1
            # `company_id` is ALWAYS `local_company_id`, never `p.get(
            # "company_id")` -- the module docstring's "Cross-device
            # company_id bug fix", restated here because it is exactly as
            # load-bearing for this table as for every other one above.
            cur = conn.execute(
                "INSERT INTO sales_quotations (id, company_id, branch_id, customer_id, doc_number, "
                "doc_kind, status, valid_until, currency, tax_mode, subtotal, discount_amount, "
                "tax_amount, total, notes, created_by, created_at, sent_at, accepted_at, declined_at, "
                "cancelled_at, converted_at, converted_sale_uid, conversion_variance_json, "
                "row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                "row_version=MAX(sales_quotations.row_version, excluded.row_version), "
                "updated_at_utc=excluded.updated_at_utc "
                "WHERE ? IS NULL OR ? > sales_quotations.row_version",
                (p.get("id"), local_company_id, resolved_branch_id, p.get("customer_id"),
                 p.get("doc_number"), p.get("doc_kind", "quotation"), p.get("status", "draft"),
                 p.get("valid_until"), p.get("currency"), p.get("tax_mode"),
                 p.get("subtotal", 0), p.get("discount_amount", 0), p.get("tax_amount", 0),
                 p.get("total", 0), p.get("notes", ""), p.get("created_by", "System"),
                 p.get("created_at"), p.get("sent_at"), p.get("accepted_at"), p.get("declined_at"),
                 p.get("cancelled_at"), p.get("converted_at"), p.get("converted_sale_uid"),
                 p.get("conversion_variance_json"), insert_row_version, p.get("updated_at_utc"),
                 *delta_binds,
                 raw_row_version, raw_row_version),
            )
            if cur.rowcount == 0:
                local_row_version = self._local_row_version(conn, "sales_quotations", p.get("id"))
                if local_row_version is not None:
                    self._record_sync_conflict(
                        conn, conflict_sink, local_company_id=local_company_id,
                        entity_type="quotation", entity_id=p.get("id"), event_type=event_type,
                        local_row_version=local_row_version,
                        incoming_row_version=raw_row_version, payload=p,
                    )
                # STOP -- do not touch lines. A stale/rejected header write
                # must not insert lines that belong to whatever payload just
                # lost the reject-stale gate.
                return True

            # Lines are inserted ONCE, on the `create` path, and NEVER
            # replaced -- there is no line-delete/line-replace event on the
            # wire at all (see the branch's own opening comment). An
            # `update` event carrying a `lines` key would be either a future
            # bug on the emit side or a corrupt payload; `p.get("lines")`
            # is always absent/empty for a real `update` today, so this
            # is simply never reached for one.
            if event_type == "create":
                for ln in (p.get("lines") or []):
                    conn.execute(
                        "INSERT INTO sales_quotation_lines (id, quotation_id, product_id, "
                        "product_name_snapshot, quantity, unit_price, discount_pct, tax_rate, "
                        "line_total, promotion_name_snapshot, line_no) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(id) DO NOTHING",
                        (ln.get("id"), p.get("id"), ln.get("product_id"),
                         ln.get("product_name_snapshot"), ln.get("quantity"), ln.get("unit_price"),
                         ln.get("discount_pct", 0), ln.get("tax_rate", 0), ln.get("line_total"),
                         ln.get("promotion_name_snapshot"), ln.get("line_no")),
                    )
        elif entity_type == "doc_series":
            # Aseel-parity wave A-PAR (schema v34). `create` and `update`
            # both arrive; there is no `delete` -- a book is withdrawn via
            # `status='retired'` (retire_doc_series), never removed, the
            # same posture `quotation`/`branch` already take on their own
            # rows.
            if event_type not in ("create", "update"):
                return True

            # `company_id` is ALWAYS `local_company_id`, never
            # `p.get("company_id")` -- the module docstring's "Cross-device
            # company_id bug fix", identical reasoning to every other
            # entity type in this file. `doc_series` carries no
            # `branch_id` column at all (only `branch_uid`, which travels
            # UNCHANGED -- see below), so unlike `sale`/`return`/
            # `quotation` there is no `_resolve_branch_id` call here: this
            # table simply has nothing for that helper to resolve.
            header_columns = [
                "label", "status", "branch_uid",
                "allocator_terminal_uid", "claimed_at_utc",
            ]
            changed_fields = p.get("_changed_fields")
            delta_frag, delta_binds = self._delta_set_clause(
                "doc_series", header_columns, changed_fields)
            raw_row_version = p.get("row_version")
            insert_row_version = raw_row_version if raw_row_version is not None else 1

            # THE CLAIM-RACE TIE-BREAK -- the defect neither the original
            # design nor its own adversarial review caught. Two devices
            # each claiming the SAME unclaimed book while both offline
            # start from an identical synced row at row_version=1 and BOTH
            # write row_version=2: the plain reject-stale gate every other
            # mutable-header branch in this file uses
            # (`WHERE excluded.row_version > table.row_version`) is FALSE
            # in BOTH directions at equal row_version, so NEITHER claim
            # applies and the fleet is left PERMANENTLY divergent -- two
            # tills each believing they own the book, each minting
            # `A-000001`. Those duplicate document numbers then hit the
            # bare UNIQUE on `sales.sale_number`/`returns.return_number`
            # on the receiving device, which is exactly the AUDIT-032B
            # wedge this whole feature exists to prevent.
            #
            # The fix is a DETERMINISTIC TOTAL ORDER every device computes
            # IDENTICALLY from payload data alone, so a tie at
            # row_version is broken the SAME way on every device rather
            # than left unresolved: higher row_version wins outright;
            # at equal row_version, the LATER claimed_at_utc wins; at an
            # equal (or absent, absent) claimed_at_utc, the
            # lexicographically GREATER allocator_terminal_uid wins. Any
            # one of these three legs deciding is enough -- the other two
            # exist only to break a tie the leg before it could not.
            # Written out as plain COALESCE/comparison SQL rather than a
            # SQLite row-value comparison so it does not depend on the
            # bundled sqlite3 version. The LOSER stops automatically with
            # NO special-case code of its own: `resolve_series` (core/
            # retail/doc_series.py) filters on
            # `allocator_terminal_uid = this device's own terminal_uid`,
            # so the instant this UPDATE's WHERE clause evaluates false on
            # the losing device, its own row no longer names it as the
            # owner and its very next sale silently falls back to the
            # legacy numbering path.
            try:
                cur = conn.execute(
                    "INSERT INTO doc_series (id, company_id, doc_type, code, label, branch_uid, "
                    "allocator_terminal_uid, claimed_at_utc, pad_width, start_no, status, "
                    "created_at, row_version, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET " + delta_frag + ", "
                    "row_version=MAX(doc_series.row_version, excluded.row_version), "
                    "updated_at_utc=excluded.updated_at_utc "
                    "WHERE ? IS NULL OR excluded.row_version > doc_series.row_version "
                    "OR (excluded.row_version = doc_series.row_version "
                    "    AND (COALESCE(excluded.claimed_at_utc,'') > COALESCE(doc_series.claimed_at_utc,'') "
                    "         OR (COALESCE(excluded.claimed_at_utc,'') = COALESCE(doc_series.claimed_at_utc,'') "
                    "             AND COALESCE(excluded.allocator_terminal_uid,'') "
                    "               > COALESCE(doc_series.allocator_terminal_uid,''))))",
                    (p.get("id"), local_company_id, p.get("doc_type"), p.get("code"),
                     p.get("label"),
                     # v42: a book written against an OLD (aliased) branch uid
                     # lands against the canonical one; the delta clause reads
                     # `excluded.branch_uid`, so this covers the update too.
                     _canonical_branch_uid(conn, p.get("branch_uid"), local_company_id),
                     p.get("allocator_terminal_uid"),
                     p.get("claimed_at_utc"), p.get("pad_width", 6), p.get("start_no", 1),
                     p.get("status", "active"), p.get("created_at"),
                     insert_row_version, p.get("updated_at_utc"),
                     *delta_binds,
                     raw_row_version),
                )
            except sqlite3.IntegrityError as exc:
                # `idx_doc_series_code` (UNIQUE on doc_type, code) is NOT
                # the conflict target above (`id` is), so a collision on IT
                # raises straight out of this statement exactly the way
                # `sync_service.py`'s own `user`/`email` collision already
                # documents in full: "ON CONFLICT(id) only ever suppresses
                # a collision on the id primary key. A DIFFERENT unique
                # index still raises a plain IntegrityError that escapes
                # this statement entirely." Left uncaught, this would raise
                # out of `_apply_event`, the cursor would never advance,
                # and sync from every device would stop forever --
                # trivially reachable here: two offline devices each
                # creating a book coded 'A' for the same doc_type. Caught
                # BY NAME, never widened to bare IntegrityError (that would
                # park a NOT NULL/FK violation too, converting a real bug
                # into quiet data loss instead of a loud, fixable one).
                msg = str(exc)
                if "doc_series.doc_type, doc_series.code" in msg:
                    return self._quarantine_apply_event(
                        conn, ev, reason="duplicate_series_code",
                        detail=f"doc_type={p.get('doc_type')!r} code={p.get('code')!r} already "
                               f"exists locally under a different series id "
                               f"(incoming id={p.get('id')!r})")
                raise
            if cur.rowcount == 0:
                local_row_version = self._local_row_version(conn, "doc_series", p.get("id"))
                # AUDIT-033 B2b: an IDENTICAL copy of what this device already holds
                # (same version, claim time, allocator, status and label) is a
                # re-announce / echo, not a conflict. The upgrade re-announce re-sends
                # every book on purpose; recording each as a "conflict" would bury
                # the genuine ones in noise.
                if local_row_version is not None and not self._doc_series_is_echo(conn, p):
                    self._record_sync_conflict(
                        conn, conflict_sink, local_company_id=local_company_id,
                        entity_type="doc_series", entity_id=p.get("id"), event_type=event_type,
                        local_row_version=local_row_version,
                        incoming_row_version=raw_row_version, payload=p,
                    )
                return True
        elif entity_type == "branch_alias":
            return self._apply_branch_alias(conn, ev, p, event_type, local_company_id)
        elif entity_type == "till_allocation":
            return self._apply_till_allocation(
                conn, ev, p, event_type, local_company_id, conflict_sink)
        elif entity_type == "party_balance_adjustment":
            return self._apply_party_balance_adjustment(conn, ev, p, event_type, local_company_id)
        elif entity_type == "gl_journal":
            # BOOKS-WAVE1 W1 (schema v41), plan §2.5. INLINE, no dedicated
            # hook method -- see the RETAIL_SYNC_ENTITY_TYPES comment above
            # for why: every existing `SyncService(...)` construction (all
            # 39 test-harness call sites plus both of app.py's own) keeps
            # applying this new entity type with zero wiring changes, the
            # same posture wave B's `inventory_movement`/`branch` branches
            # already established.
            if event_type != "create":
                # No `gl_journal` "update" or "delete" event is EVER
                # emitted -- see the RETAIL_SYNC_ENTITY_TYPES comment
                # above. Reaching here with one means either a future
                # write-side bug or a malicious/corrupt relay payload;
                # refusing it is the same safe default `sale`/
                # `inventory_movement` already take for their own
                # never-emitted event types.
                return True
            uid = p.get("uid")
            if not uid:
                # Plan §2.5 step 1: a uid-less journal can never be
                # deduped (`ON CONFLICT(uid)` below has nothing to key
                # on) and can never be NAMED later by a `source_uid` or
                # `reversed_journal_uid` -- parking it, rather than
                # inserting it uid-less, is the only safe choice.
                return self._quarantine_apply_event(
                    conn, ev, reason="gl_invalid",
                    detail="gl_journal payload carries no uid -- cannot dedupe "
                           "it or let a later reversal name it")
            # F8 (BOOKS-WAVE1-PLAN.md; v1 gap 1): `business_date` and
            # `source_type` are NOT NULL columns on `gl_journals`
            # (database/schema.py's `_migrate_add_gl_journal_sync`) --
            # inserting either as NULL raises `sqlite3.IntegrityError`
            # straight out of the INSERT below, escaping `_apply_event`
            # entirely and wedging the whole pull batch, instead of
            # parking one malformed/hostile payload the same way a
            # missing `uid` already does above.
            if not p.get("business_date") or not p.get("source_type"):
                return self._quarantine_apply_event(
                    conn, ev, reason="gl_invalid",
                    detail=f"gl_journal uid={uid!r} payload missing "
                           f"business_date and/or source_type -- both are "
                           f"NOT NULL columns on gl_journals; inserting "
                           f"either as NULL would raise sqlite3."
                           f"IntegrityError out of _apply_event rather "
                           f"than parking cleanly")
            # F8, the rest of it (third wave verifier, 2026-09-26): every
            # value this branch binds into SQL must be the TYPE the emitter
            # sends -- `journal_sync_payload` reads TEXT columns, so each of
            # these is a str or None, never a dict/list/bool/number. A dict
            # or list used to raise `sqlite3.ProgrammingError` ("Error
            # binding parameter") out of the INSERT below, and a non-list
            # `accounts` a `TypeError` -- the same page-wedge the checks
            # above close; a bool/number was written into the books as text
            # ('1', '3.5'). Checked here, before ANY write (plan section 2.5
            # step 2). `test_wrongly_typed_payload_values_are_parked_not_
            # raised` (retail_gl_journal_apply_test.py) is RED without it.
            bad_type = self._gl_journal_type_error(p)
            if bad_type is not None:
                return self._quarantine_apply_event(
                    conn, ev, reason="gl_invalid",
                    detail=f"gl_journal payload has a wrongly-typed value: {bad_type}")
            currency = p.get("currency") or DEFAULT_BASE_CURRENCY
            clean_lines, invalid_reason = self._validate_gl_journal_lines(
                p.get("lines"), currency)
            if invalid_reason is not None:
                # Plan §2.5 step 2: validate BEFORE any write. See
                # `_validate_gl_journal_lines`'s own docstring for exactly
                # which shape and balance failures map to which of the
                # two reason strings.
                return self._quarantine_apply_event(
                    conn, ev, reason=invalid_reason,
                    detail=f"gl_journal uid={uid!r} failed line validation "
                           f"(reason={invalid_reason!r})")
            # `branch_uid` NULL must stay NULL (a company-wide journal) --
            # deliberately NOT run through `_resolve_branch_id` in that
            # case, whose tier-2 fallback would wrongly pin it to this
            # device's own default branch (plan §2.2, "Branch" row). A
            # non-null uid DOES go through the ordinary resolver, exactly
            # like sale/return/inventory_movement above.
            branch_uid = p.get("branch_uid")
            resolved_branch_id = (
                self._resolve_branch_id(conn, local_company_id, branch_uid,
                                         fallback_sink=branch_fallback_sink)
                if branch_uid else None
            )
            # Reversals: the payload names the reversed journal by UID,
            # never by the sending device's own local integer id (which
            # means nothing here) -- resolved via the same `_local_id_
            # by_uid` helper `sale_item`/`user_permission` already use
            # against their own parent tables, just pointed at
            # `gl_journals` instead. Not found yet -> parked, retried the
            # same way any other missing-parent case is (plan §2.2,
            # "Reversals" row).
            reversed_journal_uid = p.get("reversed_journal_uid")
            reversed_journal_id = None
            if reversed_journal_uid:
                reversed_journal_id = self._local_id_by_uid(
                    conn, "gl_journals", reversed_journal_uid)
                if reversed_journal_id is None:
                    return self._quarantine_apply_event(
                        conn, ev, reason="missing_parent:gl_journal",
                        detail=f"reversed_journal_uid={reversed_journal_uid!r} "
                               f"not found locally yet (gl_journal uid={uid!r})")
            # ONE REVERSAL PER JOURNAL. Two computers that each void the same
            # payment while offline each mint a reversing journal, and (before
            # the reversal id became deterministic) with two different uids, so
            # the by-uid DO NOTHING below could not tell them apart and the
            # payment's money was reversed TWICE on whichever device met both.
            # A journal that already has a reversal under a DIFFERENT uid is
            # parked, not applied. The same uid again is a replay/echo and falls
            # through to the idempotent insert. The first reversal to land wins,
            # which is what a local void_payment does too.
            if reversed_journal_id is not None:
                already = conn.execute(
                    "SELECT uid FROM gl_journals WHERE company_id=? AND reversed_journal_id=? "
                    "AND uid IS NOT ? LIMIT 1",
                    (local_company_id, reversed_journal_id, uid)).fetchone()
                if already is not None:
                    return self._quarantine_apply_event(
                        conn, ev, reason="gl_duplicate_reversal",
                        detail=f"gl_journal uid={uid!r} would reverse journal "
                               f"{reversed_journal_uid!r} a second time; it is already "
                               f"reversed by uid={already['uid']!r}")
            # `source_id` -- plan §2.2 "source_id" row: the RECEIVER's own
            # local id, never the sender's integer, and NULL rather than
            # parked when it cannot be resolved (a journal is allowed to
            # arrive before the sale/return it came from; only the
            # cost-basis flag cares, and that is W1b, out of this wave's
            # scope).
            source_type = p.get("source_type")
            source_uid = p.get("source_uid")
            # PARKED-CHILDREN RULE, journal half: park ONLY when the source
            # is PARKED on this device. An ABSENT source keeps the v41
            # behaviour (apply, `source_id` NULL) -- the backfill ships
            # journals for documents this device never held, and parking
            # those would strand them forever.
            if source_type in ("sale", "return") and self._parked_document_by_uid(
                    conn, source_type, source_uid):
                return self._quarantine_apply_event(
                    conn, ev, reason=f"missing_parent:{source_type}",
                    detail=f"gl_journal uid={uid!r} belongs to {source_type} "
                           f"source_uid={source_uid!r}, which is parked on this device")
            if source_type == "sale":
                source_id = self._local_id_by_uid(conn, "sales", source_uid)
            elif source_type == "return":
                source_id = self._local_id_by_uid(conn, "returns", source_uid)
            elif source_type == "reversal":
                source_id = reversed_journal_id
            else:
                source_id = None
            # Account lines travel by CODE, never by the sending device's
            # own `account_id` (plan §2.2, "Account lines" row). Seed any
            # code this device has never seen from the payload's own
            # `accounts` metadata BEFORE resolving any line below --
            # walked in the ORDER the payload lists them (Builder A's
            # `journal_sync_payload` lists parents before children, the
            # same convention `general_ledger.ensure_default_chart` itself
            # uses for `DEFAULT_CHART`), so a child whose parent this
            # device has never seeded resolves its `parent_id` correctly
            # in this same pass rather than racing the per-line lookup
            # below.
            for meta in (p.get("accounts") or []):
                if not isinstance(meta, dict):
                    continue
                code = meta.get("code")
                if not code:
                    continue
                if conn.execute(
                    "SELECT 1 FROM gl_accounts WHERE company_id=? AND code=?",
                    (local_company_id, code),
                ).fetchone():
                    continue
                parent_id = None
                parent_code = meta.get("parent_code")
                if parent_code:
                    parent_row = conn.execute(
                        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?",
                        (local_company_id, parent_code),
                    ).fetchone()
                    parent_id = parent_row["id"] if parent_row else None
                conn.execute(
                    "INSERT OR IGNORE INTO gl_accounts "
                    "(company_id, code, name, account_type, normal_balance, parent_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (local_company_id, code, meta.get("name") or code,
                     meta.get("account_type") or "asset",
                     meta.get("normal_balance") or "debit", parent_id),
                )
            line_account_ids = {}
            for code, _debit, _credit, _memo in clean_lines:
                if code in line_account_ids:
                    continue
                row = conn.execute(
                    "SELECT id FROM gl_accounts WHERE company_id=? AND code=?",
                    (local_company_id, code),
                ).fetchone()
                if row is None:
                    # Neither already on this device NOR described by
                    # this payload's own `accounts` metadata -- there is
                    # no safe account to file this line under. Park it
                    # rather than guess or invent one.
                    return self._quarantine_apply_event(
                        conn, ev, reason="gl_unknown_account",
                        detail=f"account_code={code!r} not found locally and "
                               f"not described by this payload's own accounts "
                               f"metadata (gl_journal uid={uid!r})")
                line_account_ids[code] = row["id"]
            # `ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING` -- the
            # same partial-index shape every other RETAIL_UID_TABLES
            # immutable-fact branch above uses. `business_date`,
            # `reference`, `memo`, `posted_by` and `posted_at` all travel
            # VERBATIM from the payload and never fall back to SQLite's
            # own column defaults (`posted_at`'s is `CURRENT_TIMESTAMP`):
            # this is history arriving from ANOTHER device, not a fact
            # happening on this one right now (plan §2.2, "Reports" row --
            # `business_date` must match the source document's own date,
            # never today's, or trial_balance/income_statement periods
            # would disagree between the two devices).
            cur = conn.execute(
                "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, "
                "memo, source_type, source_id, currency, reversed_journal_id, posted_by, "
                "posted_at, uid, source_uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, resolved_branch_id, p.get("business_date"),
                 p.get("reference"), p.get("memo"), source_type, source_id,
                 currency, reversed_journal_id, p.get("posted_by") or "System",
                 p.get("posted_at"), uid, source_uid),
            )
            # HAZARD-1, THE DECISIVE LINE -- identical shape to
            # `inventory_movement` above: the lines are inserted IF AND
            # ONLY IF the header row was ACTUALLY just inserted
            # (`cur.rowcount == 1`). `rowcount == 0` means `DO NOTHING`
            # fired -- a replayed batch, a re-pulled cursor range, or this
            # device receiving its own echo of a journal it posted itself
            # (FileRelay/hub-forward, plan §2.2 "No double post" row) --
            # and inserting the lines anyway would double every debit and
            # credit on this journal while `trial_balance`'s own SUM
            # silently absorbed it as a second, identical-looking posting.
            if cur.rowcount:
                journal_id = cur.lastrowid
                for code, debit, credit, line_memo in clean_lines:
                    conn.execute(
                        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
                        "VALUES (?,?,?,?,?)",
                        (journal_id, line_account_ids[code], float(debit), float(credit), line_memo),
                    )
        return True

    _GL_JOURNAL_TEXT_FIELDS = (
        "uid", "business_date", "reference", "memo", "source_type", "source_uid",
        "branch_uid", "currency", "reversed_journal_uid", "posted_by", "posted_at",
    )
    _GL_ACCOUNT_TEXT_FIELDS = ("code", "name", "account_type", "normal_balance", "parent_code")

    @classmethod
    def _gl_journal_type_error(cls, p: dict) -> Optional[str]:
        """Names the first payload value whose TYPE the `gl_journal` emitter
        can never produce, or returns None when every value is well-typed.
        Header scalars and every `accounts[]` field: str or None. Each line's
        `account_code`: str; its `memo`: str or None (amounts are checked by
        `_validate_gl_journal_lines`). `accounts`: a list or None; each entry
        a dict (a non-dict entry keeps the older skip below). `bool` is
        refused even though it is an `int` -- it is never a str either."""
        def _text(v):
            return v is None or isinstance(v, str)

        for key in cls._GL_JOURNAL_TEXT_FIELDS:
            if not _text(p.get(key)):
                return f"{key}={type(p.get(key)).__name__}"
        lines = p.get("lines")
        if isinstance(lines, (list, tuple)):
            for i, line in enumerate(lines):
                if not isinstance(line, dict):
                    continue  # _validate_gl_journal_lines parks this shape
                if not isinstance(line.get("account_code"), str):
                    return f"lines[{i}].account_code={type(line.get('account_code')).__name__}"
                if not _text(line.get("memo")):
                    return f"lines[{i}].memo={type(line.get('memo')).__name__}"
        accounts = p.get("accounts")
        if accounts is not None and not isinstance(accounts, (list, tuple)):
            return f"accounts={type(accounts).__name__}"
        for i, meta in enumerate(accounts or []):
            if not isinstance(meta, dict):
                continue
            for key in cls._GL_ACCOUNT_TEXT_FIELDS:
                if not _text(meta.get(key)):
                    return f"accounts[{i}].{key}={type(meta.get(key)).__name__}"
        return None

    @staticmethod
    def _validate_gl_journal_lines(raw_lines, currency: str):
        """Validates one pulled `gl_journal` payload's `lines` array BEFORE
        any write happens (plan §2.5 step 2) and returns `(clean_lines,
        None)` on success -- `clean_lines` a list of `(account_code, debit
        Decimal, credit Decimal, memo)` tuples, quantized to `currency`'s
        own minor unit -- or `(None, reason)` on any failure, where
        `reason` is exactly the `sync_apply_quarantine.reason` string the
        `gl_journal` branch above should park this event under.

        Mirrors `general_ledger.validated_lines` -- the pure function
        Builder A's own half of this wave pulled OUT of `post_journal`
        for EXACTLY this reason (see that function's own docstring, which
        names this method and this file by name as its parity proof) --
        RULE FOR RULE, WITHOUT importing it: `commercial_runtime` must
        never depend on either product (see `commercial_runtime/
        currency.py`'s own module docstring), and `validated_lines` lives
        in `products/retail/backend/core/retail/general_ledger.py`. The
        rules are therefore necessarily duplicated rather than shared --
        the identical layering trade-off `general_ledger.py`'s own
        `_gl_epsilon` already documents for its `api.retail_api.
        _money_epsilon` twin. `test_balance_check_agrees_with_post_
        journal_on_the_same_lines` (this file's own test) is what keeps
        the two duplicates honest: both read `currency_quantum(currency)`
        from the one shared table, and both bucket every input the
        identical way -- see the two reasons below, each mapped onto the
        exact exception `validated_lines` itself raises for the same
        input.
          * `"gl_invalid"` -- everywhere `validated_lines` raises
            `ValueError`: a negative debit or credit (there is no such
            thing in double-entry bookkeeping), or a line carrying BOTH a
            nonzero debit and a nonzero credit (one leg, one side). Also
            covers shapes `validated_lines` never has to consider because
            its caller (`post_journal`) always hands it a Python list of
            dicts it built itself -- a PULLED payload has no such
            guarantee: `lines` missing/not a list, an entry that is not a
            dict, missing its `account_code`, a `debit`/`credit` that
            cannot be read as a number at all, a NaN/+-Infinity amount (F8,
            BOOKS-WAVE1-PLAN.md; v1 gap 1 -- parses fine as a `Decimal` but
            raises the moment it is compared or quantized), or an
            "absurdly large" finite amount that overflows `Decimal`'s own
            quantize precision. None of these are "this journal doesn't
            balance"; they are "this payload cannot even be interpreted as
            one", so a malformed/hostile relay payload is parked rather
            than raising `KeyError`/`InvalidOperation` and wedging the
            whole pull batch the way an unguarded `validated_lines` call
            would.
          * `"gl_unbalanced"` -- everywhere `validated_lines` raises
            `GeneralLedgerImbalanceError`: nothing left after an all-zero
            line is DROPPED (identical `if not d and not c: continue`,
            never rejected -- see the loop below), whether because every
            line supplied was zero or `lines` was empty to begin with; or
            a debit/credit sum that disagrees by more than half
            `currency`'s own minor unit.
        """
        if not isinstance(raw_lines, (list, tuple)):
            return None, "gl_invalid"
        quantum = currency_quantum(currency)
        clean = []
        total_debit = Decimal('0')
        total_credit = Decimal('0')
        for raw in raw_lines:
            if not isinstance(raw, dict) or not raw.get("account_code"):
                return None, "gl_invalid"
            try:
                d = Decimal(str(raw.get("debit") or 0))
                c = Decimal(str(raw.get("credit") or 0))
            except (ArithmeticError, ValueError, TypeError):
                return None, "gl_invalid"
            # F8 (BOOKS-WAVE1-PLAN.md; v1 gap 1) -- a NaN or +/-Infinity
            # `Decimal` parses successfully above (`Decimal("nan")` and
            # `Decimal("inf")` never raise) but then raises `decimal.
            # InvalidOperation` the moment it is COMPARED (`d < 0` below) --
            # OUTSIDE the `try` that only wrapped the parse, which used to
            # escape this method and `_apply_event` entirely, wedging the
            # whole pull batch rather than parking one bad payload. Checked
            # explicitly, before any comparison, rather than folded into a
            # wider `except InvalidOperation` around the comparisons --
            # `d.is_finite()` never raises for any Decimal, including NaN
            # and Infinity, so this can never itself become a second
            # unguarded comparison.
            if not d.is_finite() or not c.is_finite():
                return None, "gl_invalid"
            if d < 0 or c < 0 or (d and c):
                # `validated_lines`' own `ValueError` cases (a negative
                # side, or both sides nonzero) -- a MALFORMED line, not a
                # balance failure, so `gl_invalid`, never `gl_unbalanced`.
                return None, "gl_invalid"
            try:
                # F8 -- an "absurdly large" amount (e.g. 1E+9999) is
                # FINITE (the `is_finite()` check above lets it through)
                # but can still raise `decimal.InvalidOperation`/`Overflow`
                # here, because `quantize` refuses a result that would
                # need more significant digits than the default Decimal
                # context's own precision allows. Caught here, not left to
                # escape as gap #1 (v1) described.
                d = d.quantize(quantum, rounding=ROUND_HALF_UP)
                c = c.quantize(quantum, rounding=ROUND_HALF_UP)
            except (ArithmeticError, ValueError, TypeError):
                return None, "gl_invalid"
            # F4 (BOOKS-WAVE1-PLAN.md decision F4; v2 finding 5): quantize
            # BEFORE checking for zero -- IDENTICAL ordering to
            # `general_ledger.validated_lines`'s own fix (see that
            # function's docstring for the full "sub-fil-only journal"
            # reasoning) -- a sub-quantum line is nonzero here but rounds
            # to 0.000/0.000 and must be dropped, never kept as a
            # zero-value line. Silently dropped, never rejected, so the
            # two functions can never quietly disagree about which lines
            # count towards the balance.
            if not d and not c:
                continue
            total_debit += d
            total_credit += c
            clean.append((raw["account_code"], d, c, raw.get("memo")))
        if not clean:
            # `validated_lines`' own `GeneralLedgerImbalanceError`
            # ("refusing to validate an empty journal -- every line
            # supplied was zero") -- an empty `lines` list and an
            # all-zero one collapse to the identical outcome here too.
            return None, "gl_unbalanced"
        eps = quantum / 2
        if abs(total_debit - total_credit) > eps:
            return None, "gl_unbalanced"
        return clean, None

    # ---------------------------------------------------------------
    # PARKED-CHILDREN RULE (Track S, 2026-09-29). Money first.
    #
    # A sale parked as `duplicate_document_number` never resolves -- nothing
    # changes the number it collides with. Its sale_item and payment rows
    # already park behind it (`missing_parent:sale`). Its STOCK MOVEMENT and
    # its ACCOUNTING JOURNAL used to apply anyway (each only checked its own
    # product / left `source_id` NULL), so the receiving computer held the
    # stock and the books of a sale it did not hold -- stock down, revenue
    # up, no sale, no payment. `retail_p1b_parked_sale_children_test.py`
    # part B pinned that old behaviour; it now pins the opposite.
    #
    # The rule: a movement or a journal whose SOURCE document is parked here
    # is parked with it (same `missing_parent:<type>` vocabulary), and is
    # replayed by the ordinary retry loop, oldest first, if the source ever
    # applies. ONE-WAY on purpose for journals: a source that is merely
    # ABSENT does not park a `gl_journal` (the v41 backfill legitimately
    # ships journals for sales this device never held); only a source
    # that is PARKED does. Movements carrying `source_uid` (new payloads)
    # also park when the source is absent -- the emit order puts the
    # sale first, so absent means "not applied", never "not yet".
    # ---------------------------------------------------------------
    @staticmethod
    def _parked_document_by_uid(conn, entity_type: str, uid: Optional[str]) -> bool:
        """Does `sync_apply_quarantine` hold this document (`entity_type`,
        wire `uid`) on THIS device? Matches the row's `entity_id` (which is
        the uid for every sale/return event) OR the uid inside the parked
        payload -- hand-written and legacy rows are not guaranteed to agree.
        Missing-table tolerant, like `_has_quarantined_events`."""
        if not uid:
            return False
        try:
            rows = conn.execute(
                "SELECT entity_id, payload FROM sync_apply_quarantine WHERE entity_type=?",
                (entity_type,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if SyncService._is_missing_table_error(exc):
                return False
            raise
        for row in rows:
            if row["entity_id"] == uid:
                return True
            try:
                parked = json.loads(row["payload"])
                if isinstance(parked, dict) and parked.get("uid") == uid:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _parked_document_by_number(conn, entity_type: str, number_key: str,
                                    number: Optional[str], terminal_id: Optional[str]) -> bool:
        """LEGACY payloads (sent before `source_uid` existed) name their source
        only by `reference` = the document number. A parked `entity_type`
        row whose (`number_key`, `terminal_id`) equals (`number`,
        `terminal_id`) is that source. The terminal is part of the key
        because two computers can legitimately hold the same number (that IS
        the clash that parked it), and the movement of the computer whose sale
        WON must not be parked because the loser's sale was.

        P2S L1: two more guards, both about the WINNER. (1) A blank terminal
        on either side identifies nothing (None == None is not "the same
        computer"), so a blank never matches. (2) When a PRESENT local
        document already carries this number on this terminal, that document
        is the movement's parent and the parked one is only its twin (the
        loser of a duplicate-number clash) -- the movement belongs to the
        present one and is not parked."""
        if not number or not terminal_id:
            return False
        try:
            rows = conn.execute(
                "SELECT payload FROM sync_apply_quarantine WHERE entity_type=?",
                (entity_type,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if SyncService._is_missing_table_error(exc):
                return False
            raise
        for row in rows:
            try:
                parked = json.loads(row["payload"]) or {}
            except (TypeError, ValueError):
                continue
            if not isinstance(parked, dict):
                continue
            if parked.get(number_key) == number and parked.get("terminal_id") == terminal_id:
                twin_parked = True
                break
        else:
            return False
        # A parked twin exists. If the number is ALSO held by a present
        # local document on this terminal, the movement is that document's.
        table, column = (("sales", "sale_number") if entity_type == "sale"
                         else ("returns", "return_number"))
        try:
            present = conn.execute(
                f'SELECT 1 FROM "{table}" WHERE "{column}"=? AND terminal_id=? LIMIT 1',
                (number, terminal_id),
            ).fetchone()
        except sqlite3.OperationalError:
            # Cannot tell (a minimal fixture schema): keep the conservative
            # old behaviour -- park.
            return twin_parked
        return present is None

    @classmethod
    def _movement_source_parked_reason(cls, conn, p: dict) -> Optional[str]:
        """The `missing_parent:<type>` reason to park an `inventory_movement`
        under, or None when nothing about its source document blocks it.
        New payloads carry `source_type`/`source_uid`; legacy `sale_out` /
        `return_in` payloads are matched by (reference, terminal_id)."""
        source_type = p.get("source_type")
        source_uid = p.get("source_uid")
        if source_type in ("sale", "return") and source_uid:
            if cls._parked_document_by_uid(conn, source_type, source_uid):
                return f"missing_parent:{source_type}"
            table = "sales" if source_type == "sale" else "returns"
            if cls._local_id_by_uid(conn, table, source_uid) is None:
                return f"missing_parent:{source_type}"
            return None
        movement_type = p.get("movement_type")
        if movement_type == "sale_out":
            if cls._parked_document_by_number(
                    conn, "sale", "sale_number", p.get("reference"), p.get("terminal_id")):
                return "missing_parent:sale"
        elif movement_type == "return_in":
            if cls._parked_document_by_number(
                    conn, "return", "return_number", p.get("reference"), p.get("terminal_id")):
                return "missing_parent:return"
        return None

    @staticmethod
    def _local_id_by_uid(conn, table: str, uid: Optional[str]):
        """Resolves a Phase 5 money-moving parent's LOCAL autoincrement `id`
        from its wire `uid` -- the receiving device's own row, never the
        sending device's local id (which means nothing here). Returns None
        both when `uid` itself is falsy (a malformed/absent payload field)
        and when no local row carries it yet (the genuine orphan case this
        phase's third hazard is about) -- callers treat both identically.
        `table` is always one of a small set of literal strings this module
        passes ("sales", "returns", and -- BOOKS-WAVE1 W1 -- "gl_journals",
        for `gl_journal`'s own `reversed_journal_uid` lookup), never
        external input, so the f-string below carries no injection risk
        despite not being parameterized -- SQLite has no way to
        parameterize a table name at all."""
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
        # DELIBERATELY NOT status-filtered. A peer's event NAMES its branch by
        # uid, and that branch may since have been retired on this device --
        # but the event still belongs to it. Filtering here would drop through
        # to the default below and file another till's sale under the WRONG
        # branch, which is worse than recording it against a closed one:
        # history has to converge where it actually happened. Retirement stops
        # NEW local work being filed to a branch (see retail_api.py's
        # `_default_branch` and `_resolve_working_branch`); it does not
        # rewrite where a peer's existing work belongs.
        if branch_uid:
            row = conn.execute(
                "SELECT id FROM branches WHERE uid=? AND company_id=?",
                (branch_uid, local_company_id),
            ).fetchone()
            if row:
                return row["id"]
            # Tier 1b (schema v42): the event may name the branch by an OLD
            # uid that has since been aliased to a canonical one -- or by the
            # canonical one while THIS device still holds the branch under an
            # alias. Same branch either way, so resolve it in both directions
            # before falling to the default branch (which would file another
            # till's sale under the wrong branch). Company-scoped, and a
            # missing alias table just means "no alias".
            try:
                _canon = _canonical_branch_uid(conn, branch_uid, local_company_id)
                _row1b = None
                if _canon and _canon != branch_uid:
                    _row1b = conn.execute(
                        "SELECT id FROM branches WHERE uid=? AND company_id=?",
                        (_canon, local_company_id)).fetchone()
                if _row1b is None and _canon:
                    _row1b = conn.execute(
                        "SELECT b.id AS id FROM branches b JOIN branch_uid_aliases a "
                        "ON a.alias_uid = b.uid "
                        "WHERE a.canonical_uid = ? AND CAST(a.company_id AS TEXT) = ? "
                        "AND b.company_id = ? ORDER BY b.id LIMIT 1",
                        (_canon, str(local_company_id), local_company_id)).fetchone()
                if _row1b is not None:
                    return _row1b["id"]
            except sqlite3.Error:
                pass
        # The FALLBACK is status-filtered, for the same reason
        # `_default_branch` is: an event that names no branch at all is new
        # work being placed, and new work must not land on a retired branch.
        # Falls back to the unfiltered pick when a shop has somehow retired
        # everything, because filing it somewhere beats dropping it.
        #
        # GUARDED ON THE COLUMN EXISTING, which it is not always. `branches.
        # status` is part of the CURRENT schema, but this function also runs
        # against hand-built minimal fixtures and against peers on older
        # shapes -- an unguarded reference dies with "no such column: status"
        # and takes 11 sync tests with it, which is exactly what it did when
        # this guard was first written without the check. Same lesson, same
        # day, as `payments.related_type` in schema.py's v37 migration: never
        # assume a column this file does not itself create.
        _branch_cols = {r[1] for r in conn.execute("PRAGMA table_info(branches)").fetchall()}
        if 'status' in _branch_cols:
            row = conn.execute(
                "SELECT id FROM branches WHERE company_id=? AND COALESCE(status,'active')='active' "
                "ORDER BY id LIMIT 1",
                (local_company_id,),
            ).fetchone()
        else:
            row = None
        if row is None:
            # Either the column does not exist, or every branch is retired.
            # Filing the row somewhere beats dropping it.
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
            #
            # v42: when this install's Owner licence id IS the company it is
            # healing (the branches are stored under the licence id), a
            # self-healed placeholder gets the shop-wide DETERMINISTIC default
            # uid, not a random one -- a random one is exactly how two tills
            # ended up with two "Main Branch" rows that were never the same
            # branch. Any other case keeps the random uid.
            _heal_uid = str(uuid.uuid4())
            _lid = _owner_licence_id()
            if _lid and str(_lid) == str(local_company_id):
                _heal_uid = _default_branch_uid(_lid) or _heal_uid
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO branches (company_id,name,address,phone,uid) VALUES (?,?,?,?,?)",
                (local_company_id, "Main Branch", "", "", _heal_uid),
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

    # ------------------------------------------------------------------
    # Schema v42 (Track S): `branch_alias` and `till_allocation`.
    #
    # These two entity types are RETAIL-stream types. Since P2S H3
    # `_apply_event_guarded` runs EVERY handled event in its own savepoint on
    # both streams, so anything these raise is parked there as one
    # `malformed_<type>` row -- but a bad payload is still best handled HERE
    # where the reason is specific: validated, skipped (`return True`, it can
    # never become valid on retry) or parked, never raised -- one hostile or
    # buggy event must not stop a whole pull batch.
    # Both write with plain SQL and queue NO sync event of their own.
    # ------------------------------------------------------------------
    @staticmethod
    def _bounded_text(value, max_len: int = _ALIAS_UID_MAX_LEN) -> Optional[str]:
        if isinstance(value, str) and 0 < len(value) <= max_len:
            return value
        return None

    @staticmethod
    def _bounded_int(value, low: int, high: int) -> Optional[int]:
        # bool is an int subclass; True must not become till number 1.
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if low <= value <= high else None

    def _apply_branch_alias(self, conn, ev: dict, p: dict, event_type: str,
                            local_company_id: Optional[str]) -> bool:
        """`branch_alias` create: 'alias_uid is the same branch as
        canonical_uid'. Idempotent; never merges two distinct local branches;
        refuses a chain that would loop."""
        if event_type != "create":
            return True
        alias = self._bounded_text(p.get("alias_uid"))
        canon = self._bounded_text(p.get("canonical_uid"))
        if alias is None or canon is None or alias == canon:
            logger.warning("SyncService: skipped a malformed branch_alias event (entity_id=%r)",
                           ev.get("entity_id"))
            return True
        cid = str(local_company_id or self._get_local_company_id() or "")
        if not cid:
            return True
        try:
            existing = conn.execute(
                "SELECT canonical_uid, CAST(company_id AS TEXT) FROM branch_uid_aliases "
                "WHERE alias_uid = ?", (alias,)).fetchone()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return True
            raise
        if existing is not None:
            if existing[0] != canon or existing[1] != cid:
                return self._quarantine_apply_event(
                    conn, ev, reason="branch_alias_conflict",
                    detail=f"alias {alias!r} already points at {existing[0]!r} "
                           f"(company {existing[1]!r}); incoming says {canon!r}")
            _normalise_branch_uid_refs(conn, cid)
            return True
        # A loop (canonical eventually leads back to the alias) would make the
        # read-time hop-follower spin to its limit on every lookup.
        if _canonical_branch_uid(conn, canon, cid) == alias:
            return self._quarantine_apply_event(
                conn, ev, reason="branch_alias_conflict",
                detail=f"alias {alias!r} -> {canon!r} would close a loop")
        # Never merge two DIFFERENT local branch rows: their stock, cash
        # sessions and sales are keyed on the local id, and there is no safe
        # automatic answer for which one survives. A lone row under either
        # uid is fine -- tier 1b of `_resolve_branch_id` finds it both ways.
        try:
            both = conn.execute(
                "SELECT COUNT(DISTINCT uid) FROM branches "
                "WHERE uid IN (?, ?) AND CAST(company_id AS TEXT) = ?",
                (alias, canon, cid)).fetchone()
        except sqlite3.OperationalError:
            both = None
        if both is not None and both[0] >= 2:
            return self._quarantine_apply_event(
                conn, ev, reason="branch_alias_conflict",
                detail=f"this device holds two separate branches for {alias!r} and "
                       f"{canon!r}; they are not merged automatically")
        # P2S M3a. The only convergence the product ever announces is "a random
        # placeholder branch became this licence's deterministic default one".
        # An alias to ANY other uid lets a peer fold a real branch -- its
        # sales, stock and cash sessions -- into another, so it is refused. It
        # is parked, not dropped, so a device that has no licence YET (the
        # default cannot be derived) applies it on a later round; a peer's
        # genuine forgery just stays parked and harmless. Checked AFTER the
        # replay / loop / two-branch guards so those keep their own reasons.
        _expected_canon = _default_branch_uid(_owner_licence_id())
        if _expected_canon is None or canon != _expected_canon:
            return self._quarantine_apply_event(
                conn, ev, reason="branch_alias_conflict",
                detail=f"alias {alias!r} -> {canon!r} refused: a branch alias may only "
                       f"point at this licence's default branch uid"
                       + ("" if _expected_canon else " (no licence id here to derive it from yet)"))
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        created = self._bounded_text(p.get("created_at_utc"), 40) or now
        reason = self._bounded_text(p.get("reason"), 64) or "peer"
        origin = self._bounded_text(p.get("origin_terminal_uid"))
        conn.execute(
            "INSERT INTO branch_uid_aliases (alias_uid, canonical_uid, company_id, reason, "
            "created_at_utc, origin_terminal_uid, announced_at_utc) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(alias_uid) DO NOTHING",
            (alias, canon, cid, reason, created, origin, now))
        _normalise_branch_uid_refs(conn, cid)
        return True

    # ---------------------------------------------------------------
    # W4a (schema v45): purchase orders shared between computers.
    # ---------------------------------------------------------------
    def set_po_state_refresh(self, hook: Optional[Callable]) -> None:
        """Install (or clear) the D13 hook: `hook(conn, company_id, po_id)`
        re-derives one order's received state AND paid figure from the rows
        that sync. Called after every purchase_order / receipt / receipt-line /
        PO-payment apply."""
        self.po_state_refresh = hook

    def _refresh_po(self, conn, company_id, po_id) -> None:
        """Re-derive one order after an apply changed its inputs. The installed
        hook (the retail core's own functions) wins -- ONE implementation of
        the derivation is what the shop runs and what a test can pin; the
        in-module twin below exists only so an instance without the hook
        (unit tests, a service wired outside retail_api) still converges."""
        if po_id is None:
            return
        hook = self.po_state_refresh
        if hook is not None:
            hook(conn, company_id, po_id)
            return
        self._derive_po_receive_state(conn, company_id, po_id)
        self._recompute_po_paid(conn, company_id, po_id)

    @staticmethod
    def _derive_po_receive_state(conn, company_id, po_id) -> None:
        """Twin of `core/retail/purchase_orders.py::recompute_po_receive_state`
        (W4a-2 D2/D3): items.received_qty, status and received_at are caches of
        the receipts + the set-once cancelled_at/closed_at.

          receipts exist -> 'received' if every line's received >= ordered,
                            else 'closed' if closed_at/cancelled_at, else
                            'partial'
          none           -> 'cancelled' if cancelled_at or closed_at, else
                            'pending'
        """
        po = conn.execute(
            "SELECT id, cancelled_at, closed_at FROM purchase_orders WHERE id=? AND company_id=?",
            (po_id, company_id)).fetchone()
        if po is None:
            return
        received = _po_received_by_item(conn, company_id, po_id)
        items = conn.execute(
            "SELECT id, quantity FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()
        for it in items:
            conn.execute("UPDATE purchase_order_items SET received_qty=? WHERE id=?",
                         (float(received.get(it[0], Decimal("0"))), it[0]))
        n_receipts, last_at = conn.execute(
            "SELECT COUNT(*), MAX(received_at) FROM purchase_order_receipts "
            "WHERE po_id=? AND company_id=?", (po_id, company_id)).fetchone()
        stamped = bool(po["cancelled_at"] or po["closed_at"])
        if n_receipts:
            complete = bool(items) and all(
                received.get(it[0], Decimal("0")) >= Decimal(str(it[1] or 0)) for it in items)
            status = "received" if complete else ("closed" if stamped else "partial")
        else:
            status = "cancelled" if stamped else "pending"
        conn.execute(
            "UPDATE purchase_orders SET status=?, received_at=? WHERE id=? AND company_id=?",
            (status, last_at if status == "received" else None, po_id, company_id))

    @classmethod
    def _flag_po_conflicts(cls, conn, conflict_sink, company_id, po_id,
                           receipt_just_applied: bool = False) -> None:
        """Record, ONCE per order, the ways independent computers can disagree
        about one purchase order's facts:

          * 'po_over_received' -- two DIFFERENT offline deliveries together
            exceed what was ordered. Both are real (stock arrived), so both
            stay applied and the order reads 'received'; a human decides
            whether the supplier shorted the next order or a count was wrong.
          * 'po_received_and_cancelled' -- goods arrived on an order another
            computer withdrew. Receipts win (stock and books already moved).
          * 'po_received_after_close' (W4a-2 verify R5) -- an ORDER-basis order
            (booked up front, before v46) that is closed short or cancelled while
            a receipt exists that the closer may not have seen. The close /
            cancel journal was computed from the receipts its computer held;
            the late receipt moves 1260 -> Inventory on top, and no self-correcting
            journal exists (its amount depends on facts that arrive in any
            order). RECORDED, NOT REPAIRED: the money skew is a written residual
            and only orders already open when v46 was installed can hit it.
            Flagged when a receipt event has just been applied to such an order
            (`receipt_just_applied`), or -- from the order's own update event --
            when a receipt was created AFTER the close/cancel stamp.
        """
        po = conn.execute(
            "SELECT uid, cancelled_at, closed_at, ap_basis FROM purchase_orders WHERE id=? AND company_id=?",
            (po_id, company_id)).fetchone()
        if po is None:
            return
        ordered = {row[0]: Decimal(str(row[1] or 0)) for row in conn.execute(
            "SELECT id, quantity FROM purchase_order_items WHERE po_id=?", (po_id,)).fetchall()}
        if any(got > ordered.get(i, Decimal("0"))
               for i, got in _po_received_by_item(conn, company_id, po_id).items()):
            cls._record_sync_conflict_once(
                conn, conflict_sink, local_company_id=company_id, entity_type="purchase_order",
                entity_id=po["uid"], event_type="create", local_row_version=0,
                incoming_row_version=0, reason="po_over_received", payload={"uid": po["uid"]})
        if po["cancelled_at"] and conn.execute(
                "SELECT 1 FROM purchase_order_receipts WHERE po_id=? AND company_id=?",
                (po_id, company_id)).fetchone():
            cls._record_sync_conflict_once(
                conn, conflict_sink, local_company_id=company_id, entity_type="purchase_order",
                entity_id=po["uid"], event_type="update", local_row_version=0,
                incoming_row_version=0, reason="po_received_and_cancelled",
                payload={"uid": po["uid"]})
        stamps = [x for x in (po["closed_at"], po["cancelled_at"]) if x]
        if (po["ap_basis"] or "order") == "order" and stamps:
            real = conn.execute(
                "SELECT created_at_utc FROM purchase_order_receipts "
                "WHERE po_id=? AND company_id=? AND COALESCE(legacy,0)=0", (po_id, company_id)).fetchall()
            stamp = min(str(x) for x in stamps)
            late = bool(real) if receipt_just_applied else any(
                r[0] and str(r[0]) > stamp for r in real)
            if late:
                cls._record_sync_conflict_once(
                    conn, conflict_sink, local_company_id=company_id, entity_type="purchase_order",
                    entity_id=po["uid"], event_type="update", local_row_version=0,
                    incoming_row_version=0, reason="po_received_after_close",
                    payload={"uid": po["uid"]})

    @staticmethod
    def _ensure_legacy_receipt(conn, company_id, po, only_item_uid=None) -> None:
        """Synthesize (idempotently) the LEGACY receipt a v45 writer's
        {status:'received'} stands for (D15): one receipt, uid
        uuid5(NS, "<po_uid>:legacy-receipt"), value = the order's total, one line
        per order line for the full ordered quantity, movement_uid = W4a's own
        `receipt_movement_uid`. Every computer derives the same uids, so it is
        NEVER queued -- and never moves stock or books: the v45 writer's own
        movements/journals already sync under those movement uids.

        `only_item_uid` adds just that line (an item that arrived after the
        status event); the receipt must already exist then."""
        leg_uid = str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{po['uid']}:legacy-receipt"))
        conn.execute(
            "INSERT INTO purchase_order_receipts (company_id, uid, po_id, branch_id, received_at, "
            "value, line_count, legacy) VALUES (?,?,?,?,?,?,?,1) ON CONFLICT(uid) DO NOTHING",
            (company_id, leg_uid, po["id"], po["branch_id"],
             po["received_at"] or po["created_at"], float(po["total"] or 0),
             po["item_count"]))
        rid = conn.execute(
            "SELECT id FROM purchase_order_receipts WHERE uid=? AND company_id=?",
            (leg_uid, company_id)).fetchone()["id"]
        sql = "SELECT id, uid, quantity FROM purchase_order_items WHERE po_id=?"
        params = [po["id"]]
        if only_item_uid is not None:
            sql += " AND uid=?"
            params.append(only_item_uid)
        for it in conn.execute(sql, params).fetchall():
            if not it["uid"]:
                continue
            item_movement = str(uuid.uuid5(
                _PO_RECEIPT_NAMESPACE, f"{po['uid']}:{it['uid']}:receipt"))
            conn.execute(
                "INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity, "
                "movement_uid) VALUES (?,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
                (rid, str(uuid.uuid5(_PO_RECEIPT_NAMESPACE, f"{leg_uid}:{it['uid']}")),
                 it["id"], float(it["quantity"] or 0), item_movement))

    @staticmethod
    def _recompute_po_paid(conn, company_id, po_id) -> None:
        """Re-derives one order's `amount_paid` / `payment_status` from the
        ACTIVE payments that name it -- the receiver-side twin of
        `core/retail/purchase_orders.py::recompute_po_paid` (commercial_
        runtime cannot import products/, the same layering trade-off
        `_validate_gl_journal_lines` documents; a test pins the two to the
        same answers).

        WHY derived: the old `amount_paid` was a stored increment the writing
        computer bumped on pay and never lowered on void, and a sibling's
        instalment used to land on the wrong order. Now the figure is a pure
        function of rows that sync, so every computer reaches the same number
        whatever order the events arrive in -- which is also why every apply
        that can change the inputs (payment create, void, po_link, the order's
        own create) calls this.

        Money is summed in INTEGER minor units (the same CAST(ROUND(x*factor))
        party_balance.py uses) so 0.1 + 0.2 never drifts a fils; the 'active'
        predicate is party_balance's (`COALESCE(status,'active')='active'`)."""
        order = conn.execute(
            "SELECT total, status FROM purchase_orders WHERE id=? AND company_id=?",
            (po_id, company_id)).fetchone()
        if order is None:
            return
        try:
            cur_row = conn.execute(
                "SELECT svalue FROM retail_settings WHERE company_id=? AND skey='base_currency'",
                (company_id,)).fetchone()
            currency = (cur_row[0] if cur_row and cur_row[0] else None) or DEFAULT_BASE_CURRENCY
        except sqlite3.OperationalError:
            currency = DEFAULT_BASE_CURRENCY
        quantum = currency_quantum(currency)
        digits = -quantum.as_tuple().exponent
        factor = 10 ** digits
        minor = conn.execute(
            "SELECT COALESCE(SUM(CAST(ROUND(amount * ?) AS INTEGER)), 0) FROM payments "
            "WHERE company_id=? AND related_type='po' AND related_id=? "
            "AND COALESCE(status,'active')='active'",
            (factor, company_id, po_id)).fetchone()[0]
        paid = float(Decimal(minor) / Decimal(factor))
        total = float(order["total"] or 0)
        if order["status"] == "closed":
            # W4a-2 verify R4 (twin of the core rule): a closed-short order owes
            # only for what arrived, so 'paid' compares against the receipts'
            # value. 'cancelled' is unchanged.
            charge_minor = conn.execute(
                "SELECT COALESCE(SUM(CAST(ROUND(value * ?) AS INTEGER)), 0) FROM purchase_order_receipts "
                "WHERE company_id=? AND po_id=?", (factor, company_id, po_id)).fetchone()[0]
            total = float(Decimal(charge_minor) / Decimal(factor))
        eps = float(quantum) / 2.0
        if total > eps and total - paid <= eps:
            status = "paid"
        elif paid > eps:
            status = "partial"
        else:
            status = "unpaid"
        conn.execute(
            "UPDATE purchase_orders SET amount_paid=?, payment_status=? WHERE id=? AND company_id=?",
            (paid, status, po_id, company_id))

    @staticmethod
    def _local_po_id(conn, company_id, uid):
        """This computer's integer id for the purchase order with wire uid
        `uid`, INSIDE `company_id` (W4a verify F4). Separate from
        `_local_id_by_uid` on purpose: that helper is shared by tables whose
        callers rely on its unscoped behaviour; a parent lookup for a
        company-scoped table must never resolve another tenant's row."""
        if not uid:
            return None
        row = conn.execute(
            "SELECT id FROM purchase_orders WHERE uid=? AND company_id=?", (uid, company_id)
        ).fetchone()
        return row["id"] if row else None

    @classmethod
    def _record_sync_conflict_once(cls, conn, conflict_sink, *, local_company_id, entity_type,
                                    entity_id, event_type, local_row_version,
                                    incoming_row_version, reason, payload) -> bool:
        """`_record_sync_conflict`, but only when this (entity, reason) has no
        row yet (W4a verify F3). The same losing event is redelivered whenever
        a pull is replayed or a peer re-announces; the first row already tells
        a human everything, and N copies of it bury the real conflicts in the
        review list. Returns True when a row was written."""
        needle = "%" + json.dumps({"reason": reason})[1:-1] + "%"
        if conn.execute(
                "SELECT 1 FROM sync_conflicts WHERE company_id=? AND entity_type=? AND entity_id=? "
                "AND incoming_payload LIKE ?",
                (local_company_id, entity_type, entity_id, needle)).fetchone():
            return False
        cls._record_sync_conflict(
            conn, conflict_sink, local_company_id=local_company_id, entity_type=entity_type,
            entity_id=entity_id, event_type=event_type, local_row_version=local_row_version,
            incoming_row_version=incoming_row_version, payload={**payload, "reason": reason})
        return True

    def _apply_purchase_order_create(self, conn, ev: dict, p: dict, local_company_id,
                                      branch_fallback_sink) -> bool:
        """`purchase_order` create: insert the header another computer typed,
        under THIS computer's company and branch, exactly once.

        * `company_id` is the receiver's own, never the payload's (same rule
          as every other synced entity).
        * `branch_uid` -> this computer's branch via `_resolve_branch_id`
          (the writer's `branch_id` integer is meaningless here).
        * `supplier_id` is the supplier's wire id (suppliers sync under their
          own id); `purchase_orders.supplier_id` is a real FK, so an absent
          supplier is PARKED rather than allowed to raise and wedge the batch
          (replayed once the supplier event arrives).
        * `amount_paid`/`payment_status` start at 0/'unpaid' and are
          recomputed straight away: a payment may have arrived first and be
          parked behind this order, but one that was already applied (a
          po_link) must count at once.
        * `sync_backfill_pending` stays 0 -- that flag means "this computer
          still owes the outbox a create"; a receiver must never re-queue what
          it just pulled."""
        supplier_id = p.get("supplier_id")
        if supplier_id and not self._row_exists(conn, "suppliers", supplier_id):
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:supplier",
                detail=f"supplier_id={supplier_id!r} not found locally")
        resolved_branch_id = self._resolve_branch_id(
            conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
        try:
            conn.execute(
                "INSERT INTO purchase_orders (company_id, po_number, supplier_id, branch_id, status, "
                "subtotal, tax_total, total, notes, ordered_at, received_at, created_at, due_date, "
                "split_group_id, split_index, split_count, routing_status, routed_channel, routed_at, "
                "routed_to, idempotency_key, uid, order_journal_uid, amount_paid, payment_status, "
                "sync_backfill_pending, item_count, ap_basis, cancelled_at, closed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),?,?,?,?,?,?,?,?,?,?,?,0,'unpaid',0,?,?,?,?) "
                "ON CONFLICT(uid) WHERE uid IS NOT NULL DO NOTHING",
                (local_company_id, p.get("po_number"), supplier_id, resolved_branch_id,
                 p.get("status") or "pending", p.get("subtotal", 0), p.get("tax_total", 0),
                 p.get("total", 0), p.get("notes"), p.get("ordered_at"), p.get("received_at"),
                 p.get("created_at"), p.get("due_date"), p.get("split_group_id"),
                 p.get("split_index"), p.get("split_count"), p.get("routing_status"),
                 p.get("routed_channel"), p.get("routed_at"), p.get("routed_to"),
                 p.get("idempotency_key"), p.get("uid"), p.get("order_journal_uid"),
                 # W4a verify F1: how many lines the writer wrote. NULL when the
                 # writer predates the field -- receive then has nothing to
                 # compare against and keeps the old behaviour (see
                 # receive_purchase_order); inventing a count here would
                 # either strand the order or hide a missing line.
                 p.get("item_count"),
                 # W4a-2: how this order's supplier debt is booked. A v45
                 # writer sends no key -> 'order' (its history is never
                 # re-booked as receipts); a v46 writer sends 'receipt'.
                 p.get("ap_basis") or "order",
                 # A pre-v46 backfill create carries the CURRENT status and no
                 # stamp: a 'cancelled' one becomes the stamp (D14), a
                 # 'received' one gets its legacy receipt just below.
                 p.get("cancelled_at") or (
                     (p.get("received_at") or p.get("created_at"))
                     if p.get("status") == "cancelled" else None),
                 p.get("closed_at")),
            )
        except sqlite3.IntegrityError as exc:
            # `ON CONFLICT(uid)` only suppresses a collision on the uid index.
            # `po_number` carries its own bare UNIQUE (a number minted outside
            # this shop's numbering series can collide) -- parked BY NAME like
            # the sale_number / return_number cases, never widened to a bare
            # IntegrityError, so one clash cannot wedge every later row.
            if "purchase_orders.po_number" in str(exc):
                return self._quarantine_apply_event(
                    conn, ev, reason="duplicate_document_number",
                    detail=f"po_number={p.get('po_number')!r} already exists locally "
                           f"under a different purchase order (incoming uid={p.get('uid')!r})")
            # Same shape for the client-resubmission key: a second computer
            # that already holds a DIFFERENT order under this key must not
            # wedge the pull.
            if "purchase_orders.idempotency_key" in str(exc):
                return self._quarantine_apply_event(
                    conn, ev, reason="duplicate_idempotency_key",
                    detail=f"idempotency_key={p.get('idempotency_key')!r} already exists locally "
                           f"under a different purchase order (incoming uid={p.get('uid')!r})")
            raise
        local_id = self._local_po_id(conn, local_company_id, p.get("uid"))
        if local_id is not None:
            # W4a-2 fix (F2): this used to require NO ap_basis key, but the
            # boot backfill announces a legacy received order WITH
            # ap_basis='order' (the column is always sent), so the legacy
            # receipt was never synthesized and the order stayed 'pending' with
            # received_qty 0 on the receiving computer. Only a 'receipt'-basis
            # order is excluded: its deliveries arrive as real receipt events,
            # and synthesizing one here would count the goods twice.
            if p.get("status") == "received" and p.get("ap_basis") != "receipt":
                self._ensure_legacy_receipt_for(conn, local_company_id, local_id)
            self._refresh_po(conn, local_company_id, local_id)
        return True

    def _ensure_legacy_receipt_for(self, conn, company_id, po_id, received_at=None) -> None:
        """Synthesize the legacy receipt for order `po_id` unless it already
        has ANY receipt (real receipts are facts and win over a status claim)."""
        po = conn.execute(
            "SELECT id, uid, branch_id, total, received_at, created_at, item_count "
            "FROM purchase_orders WHERE id=? AND company_id=?", (po_id, company_id)).fetchone()
        if po is None or conn.execute(
                "SELECT 1 FROM purchase_order_receipts WHERE po_id=? AND company_id=?",
                (po_id, company_id)).fetchone():
            return
        po = dict(po)
        # The status event's own timestamp is when the goods arrived; the
        # order row's received_at is still empty on this computer.
        po["received_at"] = received_at or po["received_at"]
        self._ensure_legacy_receipt(conn, company_id, po)

    def _apply_po_receipt(self, conn, ev: dict, p: dict, local_company_id,
                          branch_fallback_sink, conflict_sink) -> bool:
        """`purchase_order_receipt` create (W4a-2): one delivery's header.

        Immutable fact keyed on `uid`. W4a-2 verify R1: the writer mints it as
        the history a COMPLETING receipt completes, or from the client's
        receipt key for a partial one -- so two computers that saw the same
        receipts and both received "the rest" collapse into this one row, while
        the same partial delivery typed twice offline stays two facts (flagged
        po_over_received if it exceeds the order; never lost). The order is resolved by
        `po_uid` INSIDE the receiver's company and parked until it arrives;
        `branch_uid` resolves to this computer's branch. `value` is the
        WRITER'S figure, stored as sent -- a receiver never recomputes it
        (rounding must not differ per computer). `legacy` is always 0: legacy
        receipts are derived locally, never shipped. Never moves stock and
        never posts books -- the writer's movements and journals sync as their
        own entities."""
        po_id = self._local_po_id(conn, local_company_id, p.get("po_uid"))
        if po_id is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:purchase_order",
                detail=f"po_uid={p.get('po_uid')!r} not found locally")
        branch_id = self._resolve_branch_id(
            conn, local_company_id, p.get("branch_uid"), fallback_sink=branch_fallback_sink)
        conn.execute(
            "INSERT INTO purchase_order_receipts (company_id, uid, po_id, branch_id, received_at, "
            "value, line_count, legacy, created_by, actor_user_uid, terminal_id, created_at_utc) "
            "VALUES (?,?,?,?,?,?,?,0,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
            (local_company_id, p.get("uid"), po_id, branch_id, p.get("received_at"),
             p.get("value") or 0, p.get("line_count"), p.get("created_by"),
             p.get("actor_user_uid"), p.get("terminal_id"), p.get("created_at_utc")))
        self._refresh_po(conn, local_company_id, po_id)
        self._flag_po_conflicts(conn, conflict_sink, local_company_id, po_id, receipt_just_applied=True)
        return True

    def _apply_po_receipt_line(self, conn, ev: dict, p: dict, local_company_id,
                               conflict_sink) -> bool:
        """`purchase_order_receipt_line` create (W4a-2): how much of one order
        line a delivery brought. The receipt is resolved by `receipt_uid`
        inside the receiver's company, the item by `po_item_uid` within THAT
        receipt's order (so a hostile uid cannot attach a line to another
        order); either missing parks the event. `movement_uid` is carried as
        text only -- the inventory movement it names syncs as its own event."""
        receipt = conn.execute(
            "SELECT id, po_id FROM purchase_order_receipts WHERE uid=? AND company_id=?",
            (p.get("receipt_uid"), local_company_id)).fetchone() if p.get("receipt_uid") else None
        if receipt is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:purchase_order_receipt",
                detail=f"receipt_uid={p.get('receipt_uid')!r} not found locally")
        item = conn.execute(
            "SELECT id FROM purchase_order_items WHERE uid=? AND po_id=?",
            (p.get("po_item_uid"), receipt["po_id"])).fetchone() if p.get("po_item_uid") else None
        if item is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:purchase_order_item",
                detail=f"po_item_uid={p.get('po_item_uid')!r} not found locally")
        conn.execute(
            "INSERT INTO purchase_order_receipt_lines (receipt_id, uid, po_item_id, quantity, "
            "movement_uid) VALUES (?,?,?,?,?) ON CONFLICT(uid) DO NOTHING",
            (receipt["id"], p.get("uid"), item["id"], p.get("quantity") or 0,
             p.get("movement_uid")))
        self._refresh_po(conn, local_company_id, receipt["po_id"])
        self._flag_po_conflicts(conn, conflict_sink, local_company_id, receipt["po_id"],
                                receipt_just_applied=True)
        return True

    def _apply_purchase_order_update(self, conn, ev: dict, p: dict, local_company_id,
                                      conflict_sink) -> bool:
        """`purchase_order` update (W4a-2): merge the order's two SET-ONCE
        stamps, `cancelled_at` and `closed_at`, then re-derive its state.

        WHY stamps and not a status: a status is not a counter -- the old
        forward-only rank had to pick a winner between 'received' and
        'cancelled' and guess at partial deliveries. Receipts are facts, the
        stamps are facts ("someone withdrew it at T", "someone closed it short
        at T"), and the status is a pure function of them (D3), so every
        computer reaches the same answer in any arrival order.

        Set-once, earliest wins: an incoming stamp fills an empty one, or
        replaces a LATER one; a NULL never erases (redelivery of an older
        event cannot un-cancel). v45 compat: a writer that still sends
        {status} -- 'cancelled' becomes a `cancelled_at` stamp (its received_at,
        else the order's created_at); 'received' becomes the LEGACY receipt
        (D15) unless real receipts already exist (facts win over a status
        claim). 'pending' / anything else is ignored. Never posts GL and never
        moves stock: journals and movements sync as their own entities."""
        local = conn.execute(
            "SELECT id, cancelled_at, closed_at, created_at FROM purchase_orders "
            "WHERE uid=? AND company_id=?", (p.get("uid"), local_company_id)).fetchone()
        if local is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:purchase_order",
                detail=f"uid={p.get('uid')!r} not found locally to update")
        legacy_status = p.get("status")
        in_cancelled = p.get("cancelled_at")
        if legacy_status == "cancelled" and not in_cancelled:
            in_cancelled = p.get("received_at") or local["created_at"]
        if legacy_status == "received":
            self._ensure_legacy_receipt_for(
                conn, local_company_id, local["id"], received_at=p.get("received_at"))

        def _earliest(current, incoming):
            if not incoming:
                return current
            if not current or str(incoming) < str(current):
                return incoming
            return current

        cancelled_at = _earliest(local["cancelled_at"], in_cancelled)
        closed_at = _earliest(local["closed_at"], p.get("closed_at"))
        if (cancelled_at, closed_at) != (local["cancelled_at"], local["closed_at"]):
            conn.execute(
                "UPDATE purchase_orders SET cancelled_at=?, closed_at=? WHERE id=? AND company_id=?",
                (cancelled_at, closed_at, local["id"], local_company_id))
        self._refresh_po(conn, local_company_id, local["id"])
        self._flag_po_conflicts(conn, conflict_sink, local_company_id, local["id"])
        return True

    # ── W3b: stock transfers (schema v47) ────────────────────────────────────

    @staticmethod
    def _transfer_earliest(local, incoming):
        """Set-once merge of a timestamp: the earliest non-empty value wins and
        an empty one never replaces a real one (so a redelivered older event
        cannot erase a stamp). 'YYYY-MM-DD HH:MM:SS' text orders as a string.
        Twin of `core/retail/stock_transfers.py::earliest`."""
        a = local or None
        b = incoming or None
        if a is None:
            return b
        if b is None:
            return a
        return min(str(a), str(b))

    @staticmethod
    def _transfer_rank_or_none(status, recalled_at):
        try:
            return transfer_state_rank(status, recalled_at)
        except ValueError:
            return None

    def _apply_stock_transfer_create(self, conn, ev: dict, p: dict, local_company_id,
                                      branch_fallback_sink) -> bool:
        """`stock_transfer` create: insert the header another computer typed,
        under THIS computer's company, with its two branches resolved from
        their uids (the writer's integer branch ids mean nothing here), exactly
        once. Status starts 'pending'; the writer's real state arrives as the
        `update` that follows (and as the boot backfill's current-facts update).
        `item_count` is how many lines the writer wrote -- NULL when it did not
        say, which leaves the receive guard nothing to compare against (a
        fabricated count would strand the transfer or hide a missing line).
        `sync_backfill_pending` stays 0: that flag means "this computer still
        owes the outbox a create"; a receiver must never re-queue what it pulled."""
        tid = p.get("id") or ev.get("entity_id")
        src = self._resolve_branch_id(
            conn, local_company_id, p.get("source_branch_uid"), fallback_sink=branch_fallback_sink)
        dst = self._resolve_branch_id(
            conn, local_company_id, p.get("destination_branch_uid"), fallback_sink=branch_fallback_sink)
        conn.execute(
            "INSERT INTO stock_transfers (id, company_id, source_branch_id, destination_branch_id, "
            "status, created_by, created_at, item_count, sync_backfill_pending, pre_v47) "
            "VALUES (?,?,?,?,'pending',?,COALESCE(?,CURRENT_TIMESTAMP),?,0,?) "
            "ON CONFLICT(id) DO NOTHING",
            (tid, local_company_id, src, dst, p.get("created_by") or "System",
             p.get("created_at"), p.get("item_count"),
             # W3b fix (F3): absent (a sender that predates the key) -> 0, the
             # strict side: the books guard applies.
             1 if p.get("pre_v47") else 0))
        return True

    def _apply_stock_transfer_update(self, conn, ev: dict, p: dict, local_company_id,
                                      conflict_sink) -> bool:
        """`stock_transfer` update: merge the state FORWARD ONLY by rank of the
        (status, recalled_at) pair, and the four stamps set-once (earliest
        wins, a NULL never erases).

        WHY a rank and not "last write wins": two computers can act on one
        transfer while offline, and every computer must reach the SAME answer
        in any arrival order. pending < cancelled-before-send < in_transit <
        recalled < received. Two meetings cannot be ordered honestly and are
        recorded -- once per transfer -- for a person:
          * 'transfer_sent_after_cancel' -- one computer cancelled a transfer
            that another had already sent. The goods left, so the send wins.
          * 'transfer_received_and_recalled' -- one computer received what
            another recalled. Received wins in status, but BOTH movements
            moved stock, so someone must correct the count.
        A lower rank than ours is simply ignored (an ordinary redelivery), with
        no conflict. Never moves stock and never posts books."""
        tid = p.get("id") or ev.get("entity_id")
        local = conn.execute(
            "SELECT status, sent_at, received_at, cancelled_at, recalled_at, pre_v47 "
            "FROM stock_transfers WHERE id=? AND company_id=?", (tid, local_company_id)).fetchone()
        if local is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:stock_transfer",
                detail=f"id={tid!r} not found locally to update")
        local_rank = self._transfer_rank_or_none(local["status"], local["recalled_at"])
        incoming_rank = self._transfer_rank_or_none(p.get("status"), p.get("recalled_at"))
        status = local["status"]
        if incoming_rank is not None and (local_rank is None or incoming_rank > local_rank):
            status = p.get("status")
        stamps = {k: self._transfer_earliest(local[k], p.get(k))
                  for k in ("sent_at", "received_at", "cancelled_at", "recalled_at")}
        if (status, *stamps.values()) != (local["status"], local["sent_at"], local["received_at"],
                                          local["cancelled_at"], local["recalled_at"]):
            conn.execute(
                "UPDATE stock_transfers SET status=?, sent_at=?, received_at=?, cancelled_at=?, "
                "recalled_at=? WHERE id=? AND company_id=?",
                (status, stamps["sent_at"], stamps["received_at"], stamps["cancelled_at"],
                 stamps["recalled_at"], tid, local_company_id))
        if local_rank is None or incoming_rank is None:
            return True
        ranks = {local_rank, incoming_rank}
        reason = None
        if ranks == {_TRANSFER_RANK_CANCELLED_BEFORE_SEND, _TRANSFER_RANK_IN_TRANSIT}:
            reason = "transfer_sent_after_cancel"
        elif ranks == {_TRANSFER_RANK_RECALLED, _TRANSFER_RANK_RECEIVED}:
            reason = "transfer_received_and_recalled"
        if reason:
            self._record_sync_conflict_once(
                conn, conflict_sink, local_company_id=local_company_id,
                entity_type="stock_transfer", entity_id=tid, event_type="update",
                local_row_version=local_rank, incoming_row_version=incoming_rank,
                reason=reason, payload={"id": tid, "status": p.get("status")})
        # A transfer that moves from "not sent here" to "sent elsewhere" may hold
        # PLACEHOLDER line costs on this computer: before v47 the column
        # DEFAULTed to 0, which would read as "free goods" and let a receive book
        # the goods in at cost 0. Clear them to NULL ("the send's cost has not
        # arrived") so the receive guard waits for the sender's real figures,
        # which follow as item updates. Only ever from a state this computer
        # never sent from.
        # W3b fix (F4): ONLY for a transfer that existed at the v47 upgrade
        # (`pre_v47`). A v47 transfer's lines are created NULL (the create route
        # and the item-create apply both write NULL), so a 0 on one is a REAL
        # zero-cost send; re-NULLing it stranded the transfer on
        # TRANSFER_SEND_NOT_ARRIVED forever when its item update had arrived
        # before the header moved. RESIDUAL: the same order flip on a PRE-v47
        # transfer whose real cost is genuinely 0 still re-NULLs (a placeholder
        # and a genuine zero cannot be told apart on a legacy row).
        if (local["pre_v47"] and incoming_rank > local_rank
                and local_rank < _TRANSFER_RANK_IN_TRANSIT <= incoming_rank):
            conn.execute(
                "UPDATE stock_transfer_items SET unit_cost=NULL WHERE transfer_id=? AND unit_cost=0",
                (tid,))
        return True

    def _apply_stock_transfer_item_create(self, conn, ev: dict, p: dict, local_company_id) -> bool:
        """`stock_transfer_item` create: one line of a transfer. The parent is
        looked up INSIDE the receiver's company (a line carries no company of
        its own), and parked until it arrives; `product_id` is a real FK, so an
        absent product is parked rather than raised. `unit_cost` and
        `quantity_received` are inserted as NULL ON PURPOSE: the column DEFAULT
        for unit_cost is 0, which would make a line that has not been sent
        look like free goods; NULL is what the receive guard reads as "the
        send has not arrived". They are filled by the item updates."""
        cid = local_company_id or self._get_local_company_id()
        tid = p.get("transfer_id")
        parent = conn.execute(
            "SELECT id FROM stock_transfers WHERE id=? AND company_id=?", (tid, cid)
        ).fetchone() if tid else None
        if parent is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:stock_transfer",
                detail=f"transfer_id={tid!r} not found locally")
        if not self._row_exists(conn, "products", p.get("product_id")):
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:product",
                detail=f"product_id={p.get('product_id')!r} not found locally")
        conn.execute(
            "INSERT INTO stock_transfer_items (id, transfer_id, product_id, quantity_sent, "
            "quantity_received, unit_cost) VALUES (?,?,?,?,NULL,NULL) ON CONFLICT(id) DO NOTHING",
            (p.get("id") or ev.get("entity_id"), tid, p.get("product_id"), p.get("quantity_sent") or 0))
        return True

    def _apply_stock_transfer_item_update(self, conn, ev: dict, p: dict, local_company_id,
                                           conflict_sink) -> bool:
        """`stock_transfer_item` update: merge the two facts a line learns.
        `unit_cost` (what the SENDER's books valued it at) and
        `quantity_received` are SET-ONCE: the first real value stays. A second,
        DIFFERENT value is a second send / second receipt of the same line on
        another computer -- both moved stock, so it is recorded (once per
        transfer, 'transfer_sent_twice' / 'transfer_received_twice') and the
        first value is kept; never silently replaced.

        A local unit_cost of 0 on a transfer this computer never sent is the
        column DEFAULT (a placeholder), not "free goods", so it does not block
        the sender's real cost. 0 is a real value for quantity_received (it
        arrived empty) and is never treated as unset."""
        cid = local_company_id or self._get_local_company_id()
        item_id = p.get("id") or ev.get("entity_id")
        row = conn.execute(
            "SELECT i.unit_cost, i.quantity_received, i.transfer_id, t.status, t.recalled_at, "
            "t.pre_v47 "
            "FROM stock_transfer_items i JOIN stock_transfers t ON t.id = i.transfer_id "
            "WHERE i.id=? AND t.company_id=?", (item_id, cid)).fetchone()
        if row is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:stock_transfer_item",
                detail=f"id={item_id!r} not found locally to update")
        tid = row["transfer_id"]
        header_rank = self._transfer_rank_or_none(row["status"], row["recalled_at"])
        new_cost, new_qty = row["unit_cost"], row["quantity_received"]
        incoming_cost, incoming_qty = p.get("unit_cost"), p.get("quantity_received")
        if incoming_cost is not None:
            # W3b fix (F4): a stored 0 is a placeholder only on a pre-v47 row.
            unset = row["unit_cost"] is None or (
                row["pre_v47"] and row["unit_cost"] == 0
                and (header_rank is None or header_rank < _TRANSFER_RANK_IN_TRANSIT))
            if unset:
                new_cost = incoming_cost
            elif Decimal(str(row["unit_cost"])) != Decimal(str(incoming_cost)):
                self._record_sync_conflict_once(
                    conn, conflict_sink, local_company_id=cid, entity_type="stock_transfer",
                    entity_id=tid, event_type="update", local_row_version=0,
                    incoming_row_version=0, reason="transfer_sent_twice",
                    payload={"id": item_id, "unit_cost": incoming_cost})
        if incoming_qty is not None:
            if row["quantity_received"] is None:
                new_qty = incoming_qty
            elif Decimal(str(row["quantity_received"])) != Decimal(str(incoming_qty)):
                self._record_sync_conflict_once(
                    conn, conflict_sink, local_company_id=cid, entity_type="stock_transfer",
                    entity_id=tid, event_type="update", local_row_version=0,
                    incoming_row_version=0, reason="transfer_received_twice",
                    payload={"id": item_id, "quantity_received": incoming_qty})
        if (new_cost, new_qty) != (row["unit_cost"], row["quantity_received"]):
            conn.execute("UPDATE stock_transfer_items SET unit_cost=?, quantity_received=? WHERE id=?",
                         (new_cost, new_qty, item_id))
        return True

    def _apply_payment_po_link(self, conn, ev: dict, p: dict, local_company_id,
                               conflict_sink=None) -> bool:
        """`payment` event_type `po_link` {uid, po_uid}: attach a payment to
        its purchase order by uid.

        Emitted by the upgrade backfill for pre-v45 payments, whose original
        `create` carried only the writer's own integer `related_id` (stored
        here as NULL, see the payment create branch). Sets related_type='po'
        and the LOCAL order id, then re-derives BOTH the order it leaves (if
        any) and the one it joins. Idempotent: a repeat finds nothing to
        change and recomputes to the same figures.

        Parks `missing_parent:payment` / `missing_parent:purchase_order`
        like every other child event whose parent has not arrived. A payment
        that is not a PO payment (related_type is something else) is left
        alone -- the event is only ever meant for PO payments."""
        pay = conn.execute(
            "SELECT id, related_type, related_id FROM payments WHERE uid=? AND company_id=?",
            (p.get("uid"), local_company_id)).fetchone()
        if pay is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:payment",
                detail=f"uid={p.get('uid')!r} not found locally to link")
        po_id = self._local_po_id(conn, local_company_id, p.get("po_uid"))
        if po_id is None:
            return self._quarantine_apply_event(
                conn, ev, reason="missing_parent:purchase_order",
                detail=f"po_uid={p.get('po_uid')!r} not found locally")
        if pay["related_type"] not in (None, "", "po"):
            return True
        # W4a verify F2: the computer that RECORDED a payment posted its journal
        # locally (`source_id` a real integer; a synced journal has it NULL).
        # That computer knows which order it paid, so a po_link for the payment
        # can only be a stranger's guess -- the pre-fix backfill emitted them for
        # every same-supplier payment carrying the order's integer. Applying it
        # would move OUR money onto another order. Ignored, and recorded ONCE
        # (a redelivery must not add a row) so a human can see it happened.
        # RESIDUAL: a down payment posts no po_payment journal of its own, so
        # its origin cannot be proved here (the sender's backfill proves it).
        if conn.execute(
                "SELECT 1 FROM gl_journals WHERE company_id=? AND source_type='po_payment' "
                "AND source_uid=? AND source_id IS NOT NULL",
                (local_company_id, p.get("uid"))).fetchone():
            self._record_sync_conflict_once(
                conn, conflict_sink, local_company_id=local_company_id, entity_type="payment",
                entity_id=p.get("uid"), event_type="po_link", local_row_version=0,
                incoming_row_version=0, reason="po_link_from_non_origin", payload=p)
            return True
        old_id = pay["related_id"] if pay["related_type"] == "po" else None
        conn.execute(
            "UPDATE payments SET related_type='po', related_id=? WHERE id=?", (po_id, pay["id"]))
        self._refresh_po(conn, local_company_id, po_id)
        if old_id and old_id != po_id:
            self._refresh_po(conn, local_company_id, old_id)
        return True

    def _apply_party_balance_adjustment(self, conn, ev: dict, p: dict, event_type: str,
                                        local_company_id: Optional[str]) -> bool:
        """v44 `party_balance_adjustment` create: one append-only fact that
        adds `amount` to a customer's or supplier's DERIVED balance.

        Create-only (anything else is ignored: the type never has an update or
        delete). Inserted under THIS device's company_id, never the payload's,
        `ON CONFLICT(uid) DO NOTHING` so a replay, an echo of our own event or
        the second computer's copy of the same fixed-uid carry is one fact.
        Emit-free: a receiver never re-announces what it applied.

        Validated BEFORE the write and parked, never raised: a non-finite or
        non-numeric amount would poison every balance derived for that party on
        every computer (SUM over a NaN), so a malformed payload is quarantined
        with its reason rather than stored."""
        if event_type != "create":
            return True
        uid = self._bounded_text(p.get("uid"), 64)
        party_id = self._bounded_text(p.get("party_id"), 64)
        party_type = p.get("party_type")
        amount = p.get("amount")
        problem = None
        try:
            # Exact canonical form, not merely parseable: an upper-case copy of
            # the same uuid is a DIFFERENT TEXT key, so ON CONFLICT(uid) would
            # keep both and the party's balance would count the carry twice.
            if uid is None or str(uuid.UUID(uid)) != uid:
                problem = "uid is not a canonical uuid"
        except (ValueError, AttributeError, TypeError):
            problem = "uid is not a canonical uuid"
        if problem is None and party_id is None:
            problem = "party_id missing or too long"
        if problem is None and party_type not in ("customer", "supplier"):
            problem = f"party_type {party_type!r} is not customer/supplier"
        if problem is None and (isinstance(amount, bool) or not isinstance(amount, (int, float))
                                or amount != amount or abs(amount) > 1e12):
            problem = f"amount {amount!r} is not a finite number"
        if problem is not None:
            return self._quarantine_apply_event(
                conn, ev, reason="malformed_party_balance_adjustment", detail=problem)
        cid = str(local_company_id or self._get_local_company_id() or "")
        if not cid:
            return True
        reason = self._bounded_text(p.get("reason"), 64) or "peer"
        try:
            conn.execute(
                "INSERT INTO party_balance_adjustments (uid, company_id, party_type, party_id, amount, "
                "reason, created_at, created_at_utc, created_by) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO NOTHING",
                (uid, cid, party_type, party_id, float(amount), reason,
                 self._bounded_text(p.get("created_at"), 40), self._bounded_text(p.get("created_at_utc"), 40),
                 self._bounded_text(p.get("created_by"), 64)))
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return True
            raise
        return True

    @staticmethod
    def _till_priority(admin_epoch, created_at_utc, alloc_id) -> tuple:
        """Lower sorts first and WINS a clash: the higher admin epoch (a later
        main-computer generation), then the earlier creation time, then the
        smaller id. Total and deterministic, so two devices holding the same
        two rows pick the same winner without talking."""
        return (-int(admin_epoch or 0), str(created_at_utc or ""), str(alloc_id))

    def _settle_till_allocations(self, conn, cid: str) -> None:
        """Recompute EVERY unreleased allocation's active/superseded verdict
        for this company from the whole set. A pure function of the rows, so
        arrival order cannot matter (P2S F2 / M1).

        Why a recompute and not the incremental "compare the newcomer with its
        clashes" it replaces: three allocations chained across a NUMBER clash
        and a TERMINAL clash settled differently by arrival order. Y (#1, T1),
        X (#1, T3, higher epoch), Z (#2, T1): arriving Y, Z, X, Z was demoted
        because Y still held T1 at that moment, then Y was demoted by X, and
        nothing went back to look at Z -- till 2 ended up with no owner on a
        device that had seen every event. A loser holds NOTHING, so a row
        beaten for its number frees its terminal for the next in line; only a
        walk over the whole set sees that.

        Walk in `_till_priority` order (higher admin epoch, earlier creation,
        smaller id); a row is active when neither its (branch, number) nor its
        terminal was taken by an earlier ACTIVE row, otherwise superseded.
        Company-wide, not per branch: the terminal unique index spans
        branches (a terminal has one number in the whole shop).

        Only 'active' and 'superseded' rows take part -- 'released' is a real
        lifecycle state, never a clash verdict. Demote before promote so the
        two partial unique indexes are never violated part-way through. No
        row_version bump and no outbox event: `superseded` is a local verdict
        every device derives identically."""
        rows = conn.execute(
            "SELECT id, branch_uid, till_number, terminal_uid, admin_epoch, "
            "created_at_utc, status FROM till_allocations "
            "WHERE CAST(company_id AS TEXT) = ? AND status IN ('active', 'superseded')",
            (cid,)).fetchall()
        rows = sorted(rows, key=lambda r: self._till_priority(r[4], r[5], r[0]))
        taken_numbers: set = set()
        taken_terminals: set = set()
        demote: list = []
        promote: list = []
        for r in rows:
            number_key = (r[1], r[2])
            if number_key in taken_numbers or r[3] in taken_terminals:
                if r[6] != "superseded":
                    demote.append(r[0])
                continue
            taken_numbers.add(number_key)
            taken_terminals.add(r[3])
            if r[6] != "active":
                promote.append(r[0])
        for alloc_id in demote:
            conn.execute("UPDATE till_allocations SET status = 'superseded' WHERE id = ? "
                         "AND CAST(company_id AS TEXT) = ?", (alloc_id, cid))
        for alloc_id in promote:
            conn.execute("UPDATE till_allocations SET status = 'active' WHERE id = ? "
                         "AND CAST(company_id AS TEXT) = ?", (alloc_id, cid))

    @staticmethod
    def _till_registry_assignment(cid: str):
        """`(main_device_id, epoch)` of THIS company's main-computer
        assignment in the local registry, or None when there is nothing to
        check against (no assignment yet, no registry table, or the registry
        cannot be opened). Its own tiny seam so a test can point it at a
        throwaway registry and so a registry problem can never wedge the
        retail apply: None means "accept", exactly as before P2S M2."""
        registry_conn = None
        try:
            from commercial_runtime.identity import registry_db
            from commercial_runtime.identity.device_registry import get_assignment

            registry_conn = registry_db.get_conn()
            assignment = get_assignment(registry_conn, cid)
            if not assignment:
                return None
            return str(assignment["device_id"]), int(assignment["epoch"])
        except Exception as exc:  # noqa: BLE001 -- see docstring: never wedge the apply
            logger.warning("SyncService: could not read the main-computer assignment (%s: %s); "
                           "accepting the till allocation unchecked", type(exc).__name__, exc)
            return None
        finally:
            if registry_conn is not None:
                try:
                    registry_conn.close()
                except Exception:  # noqa: BLE001
                    pass

    def _till_author_refusal(self, cid: str, by_uid: str, epoch: int):
        """P2S M2 -- `(quarantine_reason, detail)` when a NEW till allocation
        must be parked, else None.

        The money verifier's finding: any peer could send a `till_allocation`
        with `admin_epoch` 2**31-1 and a far-future `created_at_utc`; the clash
        settle orders by epoch first, so it outranked and superseded every real
        allocation, and nothing checked that the author was the main computer.
        Now the receiver compares against its OWN registry assignment
        `(main, C)`, for an incoming author `by_uid` at epoch `E`:

          * E > C  -> `allocation_epoch_unknown`. This device has not yet heard
            of an epoch that high. Parked, NOT dropped: `_retry_quarantined_
            events` replays it on every pull, so a real hand-over whose
            registry event simply arrives after the allocation applies as soon
            as the registry catches up, while a forged sky-high epoch stays
            parked for ever and outranks nothing.
          * E == C and author != main -> `till_allocation_not_from_main`.
          * E < C  -> accepted. An old main's allocation is history; the settle
            recompute orders by epoch DESC, so it can never displace a
            higher-epoch one.

        This is a BAR-RAISER, NOT AUTHENTICATION: a device holding the licence
        can still write the real main's id and current epoch into the payload.
        Proving who wrote an event is AUDIT-033 (next wave). Only INSERTs are
        checked -- an update carries no author, only the lifecycle moves -- and
        a forged LOW-epoch allocation for a FREE number is still accepted
        (recorded residual). No assignment to compare with -> accepted."""
        assignment = self._till_registry_assignment(cid)
        if assignment is None:
            return None
        main_device, current_epoch = assignment
        if epoch > current_epoch:
            return ("allocation_epoch_unknown",
                    f"allocation epoch {epoch} is above this device's known main-computer "
                    f"epoch {current_epoch}; parked until the registry catches up")
        if epoch == current_epoch and by_uid != main_device:
            return ("till_allocation_not_from_main",
                    f"allocated by {by_uid!r}, but the main computer at epoch "
                    f"{current_epoch} is {main_device!r}")
        return None

    def _apply_till_allocation(self, conn, ev: dict, p: dict, event_type: str,
                               local_company_id: Optional[str],
                               conflict_sink: Optional[list]) -> bool:
        """`till_allocation` create/update. Identity fields (number, terminal,
        branch, epoch) are immutable once a row exists; only the lifecycle
        (`status`, `row_version`, `updated_at_utc`) moves, and only forward."""
        if event_type not in ("create", "update"):
            return True
        alloc_id = self._bounded_text(p.get("id"))
        branch_uid = self._bounded_text(p.get("branch_uid"))
        terminal_uid = self._bounded_text(p.get("terminal_uid"))
        by_uid = self._bounded_text(p.get("allocated_by_terminal_uid"))
        till_number = self._bounded_int(p.get("till_number"), 1, 99)
        epoch = self._bounded_int(p.get("admin_epoch"), 0, _MAX_SYNCED_ADMIN_EPOCH)
        row_version = self._bounded_int(p.get("row_version", 1), 1, _MAX_SYNCED_ADMIN_EPOCH)
        status = p.get("status", "active")
        if isinstance(status, (list, dict)):
            # P2S H3: a list/dict status made `status not in _TILL_STATUSES`
            # raise `TypeError: unhashable type` (a set membership test) out
            # of the whole batch. A wrong-TYPE field is a broken peer, not a
            # value this build should quietly drop: park it where an operator
            # can see it (one row, `malformed_till_allocation`). A null or a
            # scalar status is hashable and keeps the long-standing "skip with
            # a warning" path below (pinned by
            # test_malformed_till_allocation_is_skipped_not_raised_not_stored).
            return self._quarantine_apply_event(
                conn, ev, reason="malformed_till_allocation",
                detail=f"status is a {type(status).__name__}, not a string")
        if (None in (alloc_id, branch_uid, terminal_uid, by_uid, till_number, epoch, row_version)
                or status not in _TILL_STATUSES):
            logger.warning("SyncService: skipped a malformed till_allocation event (entity_id=%r)",
                           ev.get("entity_id"))
            return True
        cid = str(local_company_id or self._get_local_company_id() or "")
        if not cid:
            return True
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        created = self._bounded_text(p.get("created_at_utc"), 40) or now
        updated = self._bounded_text(p.get("updated_at_utc"), 40) or created
        try:
            branch_uid = _canonical_branch_uid(conn, branch_uid, cid)
            local = conn.execute(
                "SELECT CAST(company_id AS TEXT), status, row_version FROM till_allocations "
                "WHERE id = ?", (alloc_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return True
            raise
        try:
            if local is not None:
                if local[0] != cid:
                    return self._quarantine_apply_event(
                        conn, ev, reason="till_allocation_conflict",
                        detail=f"allocation id {alloc_id!r} already belongs to another company")
                if row_version > int(local[2] or 0):
                    if status == "active" and local[1] in ("released", "superseded"):
                        # FORWARD-ONLY (vYM2 M2): the lifecycle is
                        # active -> released | superseded and never back. This
                        # update path has no author check, so a forged or
                        # replayed event with a big row_version could flip a
                        # released allocation (its number already handed on
                        # and acked) back to active and take the number away
                        # from the new holder. Parked, not applied; the
                        # holder of a number is decided by `_settle_
                        # till_allocations` from the set, never by an update
                        # claiming to revive a finished row. Only fires when
                        # the version would actually have applied, so a stale
                        # replay stays the silent no-op it always was.
                        return self._quarantine_apply_event(
                            conn, ev, reason="till_allocation_status_backwards",
                            detail=f"allocation {alloc_id!r} is {local[1]!r} here; "
                                   f"an update may not make it active again")
                    new_status = status
                    if status == "active" and local[1] != "active":
                        # Stored provisionally as a loser so the partial unique
                        # indexes cannot trip; the recompute below decides.
                        new_status = "superseded"
                    conn.execute(
                        "UPDATE till_allocations SET status = ?, row_version = ?, "
                        "updated_at_utc = ? WHERE id = ?",
                        (new_status, row_version, updated, alloc_id))
                elif (row_version == int(local[2] or 0) and status != local[1]
                        and "superseded" not in (status, local[1])):
                    # Same version, different lifecycle, and neither side is
                    # our own clash verdict: two writers disagree.
                    self._record_sync_conflict(
                        conn, conflict_sink, local_company_id=cid,
                        entity_type="till_allocation", entity_id=alloc_id,
                        event_type=event_type, local_row_version=int(local[2] or 0),
                        incoming_row_version=row_version, payload=p)
                # Also after a replay / stale update that changed nothing: the
                # verdict is derived from the set, so re-deriving is harmless
                # and heals a row left inconsistent by an older build.
                self._settle_till_allocations(conn, cid)
                return True
            refusal = self._till_author_refusal(cid, by_uid, epoch)
            if refusal is not None:
                return self._quarantine_apply_event(
                    conn, ev, reason=refusal[0], detail=refusal[1])
            store_status = "superseded" if status == "active" else status
            conn.execute(
                "INSERT INTO till_allocations (id, company_id, branch_uid, till_number, "
                "terminal_uid, status, allocated_by_terminal_uid, admin_epoch, row_version, "
                "created_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (alloc_id, cid, branch_uid, till_number, terminal_uid, store_status, by_uid,
                 epoch, row_version, created, updated))
            self._settle_till_allocations(conn, cid)
        except sqlite3.IntegrityError as exc:
            if "till_allocations" in str(exc):
                return self._quarantine_apply_event(
                    conn, ev, reason="till_allocation_conflict", detail=str(exc)[:200])
            raise
        return True

    @staticmethod
    def _resolve_employee_code(conn, local_company_id, uid, incoming):
        """The `employee_id` a pulled `user` row is written with.

        Every install mints its own codes -- `ADMIN-0001` for the first-run
        owner, `EMP-{count+1:04d}` per hire (onboarding_routes.py) -- with no
        coordination, so two devices of one shop produce the same code for
        different people as a matter of course. Until 2026-09-05 the second
        device parked such a row as `duplicate_employee_id`, which meant the
        owner's own account never arrived on their phone (measured: the
        desktop's `ADMIN-0001` parked on the Mi Note 10 with its eight
        permission events stuck behind it). The owner's decision that day:
        one owner login on every device. The code is a display label, `uid`
        is the identity, so a collision with a DIFFERENT person is resolved
        by numbering, never by refusing the person.

          1. no incoming code, or no local holder of it -> as sent
          2. the local holder IS this uid -> as sent (an ordinary update)
          3. held by someone else and this uid already has a row -> keep the
             code this device already shows for them (a later `update` from
             the sender must not re-collide on every delivery)
          4. held by someone else and this uid is new here -> the next free
             number in the same series (`ADMIN-0001` -> `ADMIN-0002`), or
             `<code>-<uid[:8]>` when the code carries no numeric tail

        Positional indexing throughout -- registry connections arrive with
        and without `row_factory`, same as user_accounts.py's own readers.
        """
        if not incoming or not uid:
            return incoming
        holder = conn.execute(
            "SELECT uid FROM users WHERE company_id=? AND employee_id=?",
            (local_company_id, incoming),
        ).fetchone()
        if holder is None or holder[0] == uid:
            return incoming
        mine = conn.execute("SELECT employee_id FROM users WHERE uid=?", (uid,)).fetchone()
        if mine is not None and mine[0]:
            return mine[0]
        tail = len(incoming) - len(incoming.rstrip("0123456789"))
        if tail:
            prefix, width = incoming[:-tail], tail
            number = int(incoming[-tail:]) + 1
            for _ in range(10000):
                candidate = f"{prefix}{number:0{width}d}"
                taken = conn.execute(
                    "SELECT 1 FROM users WHERE company_id=? AND employee_id=?",
                    (local_company_id, candidate),
                ).fetchone()
                if taken is None:
                    return candidate
                number += 1
        return f"{incoming}-{uid[:8]}"

    def _apply_event_guarded(self, conn, ev: dict, local_company_id: Optional[str] = None,
                             **sinks) -> bool:
        """`_apply_event`, but an event (on EITHER stream) that blows up on its
        own payload is parked instead of wedging the stream.

        P2S H3 widened this from the four registry entity types to every
        handled type on both streams: the retail stream had no per-event
        isolation at all, and one wrong-shaped `till_allocation` /
        `branch_alias` / `sale` / `branch` payload stopped ALL retail sync
        (measured by the money verifier: the batch rolled back, the cursor
        never moved, the same event was re-pulled forever). An unhandled
        `entity_type` still takes the direct path -- it is skipped or parked
        by `_apply_event` itself and touches nothing.

        Why this exists (Track S security review): `apply_pull_result` has no
        per-event savepoint, so ONE exception aborts the whole batch, the
        cursor never advances, and the same poison event is re-pulled forever.
        A peer (or a hostile device holding the licence) can craft a
        `device` / `admin_device` / `user` / `user_permission` payload that
        makes identity's helpers raise -- an `admin_epoch` of 10**20
        (OverflowError binding to SQLite's 64-bit integer), a non-numeric one
        (ValueError / TypeError), a value the tables reject (IntegrityError).

        Every such event runs inside its OWN savepoint: on failure only THAT
        event's writes are undone (`ROLLBACK TO`), it is parked in
        `sync_apply_quarantine` as `malformed_<entity_type>` (so an operator
        can see the broken peer, and `_retry_quarantined_events` re-tries it
        harmlessly), and the loop moves on to the next event.

        Caught: ANY exception an event raises on its own (the payload is the
        only thing that differs between a good and a bad event). NOT caught:
        a `sqlite3.DatabaseError` that is not a payload-shaped constraint or
        binding error -- `OperationalError` (locked), a corrupt file -- since
        those are not a property of the payload and must still fail the batch
        so the cursor stays put; and the `user` branch's deliberate loud
        IntegrityError for an unexpected constraint (wave A, Decision 4 --
        flagged by `_aura_loud_integrity`)."""
        if not isinstance(ev, dict):
            # Nothing to apply and nothing to park: there is no entity_id to
            # key a quarantine row on. Skipped, never an AttributeError.
            logger.warning("sync: skipped a pulled event that is not an object (%s)",
                           type(ev).__name__)
            return True
        entity_type = ev.get("entity_type")
        if not isinstance(entity_type, str) or entity_type not in self._handled_entity_types:
            return self._apply_event(conn, ev, local_company_id, **sinks)
        # Python's legacy sqlite3 driver opens its implicit transaction only
        # before DML; a SAVEPOINT issued outside one would start (and its
        # RELEASE would COMMIT) a transaction of its own. Open it explicitly
        # so the caller's single commit stays the only commit.
        if not conn.in_transaction:
            conn.execute("BEGIN")
        savepoint = "ev_apply_" + uuid.uuid4().hex[:12]
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            applied = self._apply_event(conn, ev, local_company_id, **sinks)
        except Exception as exc:
            if getattr(exc, "_aura_loud_integrity", False):
                raise
            if (isinstance(exc, sqlite3.DatabaseError)
                    and not isinstance(exc, _ISOLATED_APPLY_ERRORS)):
                raise
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            # ascii(): %s of an exception that quotes a lone surrogate would
            # otherwise hand the log handler a string its stream cannot
            # encode. %r/ascii() always escape it (vYM2, defence in depth).
            logger.warning(
                "sync: parked malformed %s event %s (%s: %s)",
                ascii(entity_type), ascii(ev.get("entity_id")),
                type(exc).__name__, ascii(str(exc)))
            return self._quarantine_apply_event(
                conn, ev, reason=f"malformed_{entity_type}",
                detail=f"{type(exc).__name__}: {exc}")
        conn.execute(f"RELEASE {savepoint}")
        if applied and ev.get("_authority_verified") is True:
            try:
                self._supersede_parked_unsigned(conn, ev)
            except sqlite3.OperationalError as exc:
                # Cleanup only: a failure here leaves a stale parked row (visible in
                # sync-review, harmless) and must never fail the batch.
                logger.warning("sync: could not clear superseded parked events (%s)", exc)
        return applied

    # Park reasons a SIGNED event of the same entity may clear. `unsigned_authority_
    # event` is "an older build sent this without proof"; `main_unknown` is "I did
    # not yet know who the main computer was" (a retry-by-itself state). Every OTHER
    # reason (bad_signature, foreign_licence, untrusted_lease, signer_binding_clash,
    # signer_revoked, *_needs_main, second_admin_refused, release_ack_wrong_sender,
    # alias_origin_mismatch, takeover_*) is a forgery or abuse signal: it stays for a
    # human, whatever signed event arrives later.
    _SUPERSEDABLE_PARK_REASONS = tuple(sorted(_authority.SUPERSEDABLE_PARK_REASONS))

    @staticmethod
    def _as_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _supersede_parked_unsigned(self, conn, ev: dict) -> int:
        """AUDIT-033 B2b. A VERIFIED, signed authority event just applied for an
        entity: drop the parked copies of the SAME entity that it supersedes.

        WHY. Relays keep history, so a device that joins (or re-pulls from cursor 0)
        after the upgrade receives the shop's PRE-upgrade authority events, which
        carry no signature and park as `unsigned_authority_event`. The main computer
        re-announces the current state signed (`sync/reannounce.py`); once that
        applies, the unsigned history of the same entity is answered and would
        otherwise sit in sync-review for ever, asking a person to look at things the
        shop has already moved past.

        WHAT MATCHES (all other parked rows are left strictly alone):
          * reason in `_SUPERSEDABLE_PARK_REASONS` -- never a forgery signal;
          * the same entity: users by uid, devices / till_allocations / doc_series by
            entity id; `admin_device` (a fresh entity id per event) by company, which
            is one assignment; `user_permission` (fresh ids too) by the pair
            (user_uid, subsystem);
          * a version <= the applied one: users/till_allocation/doc_series
            `row_version`, device `row_version` AND `admin_row_version`, admin_device
            `epoch`. A parked event that is NEWER than the applied state is a real,
            unanswered change and stays.
          * `user_permission` only for a signed `_reannounce`, and only when this
            device's permission row now EQUALS the re-announced level. Re-announces
            never overwrite (the table has no version), so a device that kept a
            different local value has a genuine disagreement the parked row may
            explain; clearing it would hide that.
        Returns how many rows were removed. No commit (the caller's transaction)."""
        entity_type = ev.get("entity_type")
        if entity_type not in ("user", "user_permission", "device", "admin_device",
                               "till_allocation", "doc_series"):
            return 0
        p = _authority.strip_auth(ev.get("payload"))
        if not isinstance(p, dict):
            return 0
        # AUDIT-033 fix round (verifier V finding #3/#4): only the computer whose
        # word settles the matter may clear what is parked about it. A harmless
        # owner edit by an ordinary member applying must never delete a parked
        # main-signed change. So: the MAIN may clear any of these; a device may
        # also clear parked copies of ITSELF, and a till of its own numbering book.
        signer_dev = ev.get("_authority_signer_dev")
        allowed = ev.get("_authority_signer_is_main") is True
        if not allowed and entity_type == "device" and signer_dev and signer_dev == p.get("device_id"):
            allowed = True
        if (not allowed and entity_type == "doc_series" and signer_dev
                and signer_dev == p.get("allocator_terminal_uid")):
            allowed = True
        if not allowed:
            return 0
        placeholders = ",".join("?" * len(self._SUPERSEDABLE_PARK_REASONS))
        keyed = self._quarantine_has_auth_key(conn)
        try:
            rows = conn.execute(
                "SELECT entity_id, event_type, payload, "
                + ("auth_key" if keyed else "'' AS auth_key")
                + " FROM sync_apply_quarantine "
                f"WHERE entity_type=? AND reason IN ({placeholders})",
                (entity_type, *self._SUPERSEDABLE_PARK_REASONS)).fetchall()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return 0
            raise
        if not rows:
            return 0
        if entity_type == "user_permission":
            if p.get("_reannounce") is not True or not self._permission_now_equals(conn, p):
                return 0
        removed = 0
        applied_id = str(ev.get("entity_id") or "").lower()
        for row in rows:
            try:
                parked = json.loads(row[2])
            except (TypeError, ValueError):
                continue
            if not isinstance(parked, dict):
                continue
            if entity_type == "user_permission":
                same = (parked.get("user_uid") == p.get("user_uid")
                        and parked.get("subsystem") == p.get("subsystem"))
            elif entity_type == "admin_device":
                same = self._as_int(parked.get("epoch"), 10 ** 12) <= self._as_int(p.get("epoch"), -1)
            else:
                same = str(row[0] or "").lower() == applied_id
                if same and entity_type == "device":
                    same = (self._as_int(parked.get("row_version"), 10 ** 12)
                            <= self._as_int(p.get("row_version"), -1)
                            and self._as_int(parked.get("admin_row_version"), 1)
                            <= self._as_int(p.get("admin_row_version"), 1))
                elif same:
                    same = (self._as_int(parked.get("row_version"), 10 ** 12)
                            <= self._as_int(p.get("row_version"), -1))
            if not same:
                continue
            if keyed:
                conn.execute(
                    "DELETE FROM sync_apply_quarantine WHERE entity_id=? AND event_type=? AND auth_key=?",
                    (row[0], row[1], row[3]))
            else:
                conn.execute(
                    "DELETE FROM sync_apply_quarantine WHERE entity_id=? AND event_type=?",
                    (row[0], row[1]))
            removed += 1
        if removed:
            logger.info("sync: cleared %d parked %s event(s) superseded by a signed one", removed, entity_type)
        return removed

    @staticmethod
    def _permission_now_equals(conn, p: dict) -> bool:
        """Does THIS device's `user_permissions` row for (p.user_uid, p.subsystem)
        hold exactly `p.access_level`? (see `_supersede_parked_unsigned`)."""
        try:
            row = conn.execute(
                "SELECT up.access_level FROM user_permissions up JOIN users u ON u.id = up.user_id "
                "WHERE u.uid=? AND up.subsystem=?", (p.get("user_uid"), p.get("subsystem"))).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None and row[0] == p.get("access_level")

    @staticmethod
    def _quarantine_apply_event(conn, ev: dict, reason: str, detail: str) -> bool:
        """Parks a Phase 5 money-moving child event in `sync_apply_quarantine`
        instead of dropping it or letting an FK violation raise and wedge the
        whole pull batch -- see schema.py's `CREATE TABLE sync_apply_quarantine`
        for the full reasoning (the client-side twin of Owner's own push-side
        quarantine, 681b0fa). An upsert keyed on `(entity_id,
        event_type)` -- NOT `entity_id` alone, see that CREATE TABLE's own
        comment for why -- makes re-parking the SAME still-blocked event on
        every retry tick refresh one row rather than grow an ever-larger pile
        of duplicates. P2S L2: on a re-park `reason`, `detail` and `payload`
        follow the LATEST attempt (an event first parked for one reason and
        now blocked for another must say so; `INSERT OR IGNORE` used to keep
        the first story), while `quarantined_at` is deliberately left alone
        and stays at the FIRST time the event was seen, which is exactly what
        "how long has this been stuck" should show and what the retry loop's
        oldest-first order relies on. Always returns False -- the event
        was NOT applied -- so `_apply_event`'s callers can tell this case
        apart from a genuine success."""
        def text(value) -> str:
            # P2S H3: the three key columns are TEXT NOT NULL. A malformed
            # event may carry a null / list / number there, and binding that
            # would raise out of the very handler that exists to park it.
            # vYM2: a LONE SURROGATE in a string ("\udc80" -- what json.loads
            # makes of the JSON text "\udc80") cannot be bound by sqlite3
            # (UnicodeEncodeError), which raised out of the very handler
            # that parks the event and wedged the batch again. Such a string
            # is stored as its escaped form ("\\udc80"): still readable by an
            # operator, still a stable key for the upsert, and a well-formed
            # string never changes. The hub transport already rejects these
            # shapes; this is defence in depth for a direct/LAN pull.
            if isinstance(value, str):
                return value.encode("utf-8", "backslashreplace").decode("utf-8")
            return "" if value is None else json.dumps(value, default=str)

        # reason carries f"malformed_{entity_type}" from the guard, and detail
        # carries exception text that can quote the offending value.
        reason = text(reason)
        detail = text(detail)
        entity_id, entity_type, event_type = text(ev.get("entity_id")), text(ev.get("entity_type")), text(ev.get("event_type"))
        # `_orig_payload` (set by the authority gate in `_apply_event`) is the
        # payload AS RECEIVED, `_auth` included -- a retry must be able to
        # re-verify the signature, so a park AFTER the gate stores it.
        stored_payload = ev.get("_orig_payload", ev.get("payload")) or {}
        payload_json = json.dumps(stored_payload, default=str)
        auth_key = _authority.quarantine_auth_key(stored_payload, entity_type)
        evidence = tuple(sorted(_authority.EVIDENCE_PARK_REASONS))
        supersedable = tuple(sorted(_authority.SUPERSEDABLE_PARK_REASONS))
        ev_marks, sup_marks = ",".join("?" * len(evidence)), ",".join("?" * len(supersedable))
        # AUDIT-033 fix rounds (verifier V findings #7/#8, review V3r G2): the row is
        # keyed by the CONTENT of the whole stored payload (`_auth` included), and
        # EVIDENCE is sticky.
        #   * Different content for one entity keeps its own row, so a signed or
        #     unsigned look-alike can never displace a legitimate parked change.
        #     The key used to hash only the signature TEXT; a signature is public,
        #     so copying it onto junk produced the same key and the junk's payload
        #     replaced the legitimate event's (G2).
        #   * IDENTICAL content is the same row, and on a re-park only reason and
        #     detail may move -- never `payload` of an authority event.
        #   * An UNSIGNED copy of an authority event is never stored beside a signed
        #     one for the same (entity_id, event_type): identified by the stored
        #     payload having no `_auth`, not by an empty key (every authority row
        #     now has a content key).
        #   * A reason that is a forgery or abuse signal (`EVIDENCE_PARK_REASONS`) is
        #     never rewritten into one a later signed event may clear
        #     (`SUPERSEDABLE_PARK_REASONS`): the CASE keeps the stored reason and
        #     detail (V6, V6b).
        #   * A bound on junk: see `_authority.MAX_PARKED_VERIFICATION_FAILURES_PER_
        #     ENTITY`.
        # `quarantined_at` is left alone on a re-park (first-seen time, as before).
        # Events that are not authority events keep key '' and keep their payload
        # following the latest attempt (P2S L2): nothing about them is signed.
        columns = [r[1] for r in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall()]
        keyed = "auth_key" in columns
        # Review V4r N1: nothing below may read every parked row of this entity WITH
        # its payload -- one entity's pile of evidence rows made each re-park O(n)
        # and a whole retry pass O(n^2) (500 rows -> 12 s, 2000 -> 246 s). Each
        # question asks the database only for what it needs, and LIMITs it where
        # existence is the whole question.
        new_auth = stored_payload.get(_authority.AUTH_KEY) if isinstance(stored_payload, dict) else None
        if new_auth is None and _authority.is_authority_event(ev):
            signed_parked = conn.execute(
                "SELECT 1 FROM sync_apply_quarantine WHERE entity_id=? AND event_type=? "
                "AND instr(payload, ?) > 0 LIMIT 1",
                (entity_id, event_type, '"' + _authority.AUTH_KEY + '"')).fetchone()
            if signed_parked is not None:
                logger.info("sync: not parking an unsigned copy of %s %s: a signed one is already parked",
                            ascii(entity_type), ascii(entity_id))
                return False
        if not keyed:
            # Old shape (a database that reached the previous v10/v42 before this
            # fix): the primary key is still (entity_id, event_type), so there is AT
            # MOST ONE parked row, and the guards the key would have given are done
            # by hand. Review V3r G3 (R6 restated): a parked row whose stored `_auth`
            # VERIFIES is never overwritten by anything but another VERIFIED event
            # from the SAME verified signer. Review V4r N4/N5: the parked row is
            # verified under ITS OWN stored entity_type (the key has no entity_type,
            # so a `user` junk event can land on a `device` row's key and used to
            # step the guard aside), the incoming one under its own, and a
            # replacement must not carry an OLDER row_version/epoch than the parked
            # one. Where no verifier was handed in (a direct call), nothing can be
            # proven and the old last-write behaviour stands.
            verify = ev.get("_authority_verify")
            if verify is not None:
                for row in conn.execute(
                        "SELECT payload, entity_type FROM sync_apply_quarantine "
                        "WHERE entity_id=? AND event_type=?", (entity_id, event_type)).fetchall():
                    prior = SyncService._loads_or_none(row[0])
                    if prior is None or prior == stored_payload:
                        continue
                    prior_v = verify(prior, row[1])
                    if not isinstance(prior_v, _authority.VerifiedSigner):
                        continue
                    new_v = verify(stored_payload, entity_type)
                    same_signer = (isinstance(new_v, _authority.VerifiedSigner)
                                   and row[1] == entity_type
                                   and (new_v.dev, new_v.inst, new_v.fingerprint)
                                   == (prior_v.dev, prior_v.inst, prior_v.fingerprint))
                    older = SyncService._payload_is_older(stored_payload, prior)
                    if not same_signer or older:
                        logger.warning(
                            "sync: keeping the parked verified %s event for %s (signer %s); a different, "
                            "unverifiable or older copy cannot replace it in an old-shape quarantine",
                            ascii(row[1]), ascii(entity_id), ascii(prior_v.dev))
                        return False
        # The flood bounds. Only a NEW row can grow the pile.
        if reason in _authority.VERIFICATION_FAILURE_REASONS or reason in _authority.EVIDENCE_PARK_REASONS:
            same_row = bool(conn.execute(
                "SELECT 1 FROM sync_apply_quarantine WHERE entity_id=? AND event_type=?"
                + (" AND auth_key=?" if keyed else "") + " LIMIT 1",
                (entity_id, event_type) + ((auth_key,) if keyed else ())).fetchone())
            if not same_row and not SyncService._park_within_bounds(
                    conn, ev, reason, entity_type, entity_id):
                return False
        keep_case = (f"CASE WHEN sync_apply_quarantine.reason IN ({ev_marks}) AND excluded.reason IN ({sup_marks}) "
                     "THEN sync_apply_quarantine.{col} ELSE excluded.{col} END")
        # Authority rows: the stored payload is never replaced (identical content on
        # a keyed table; on the old shape the guard above already decided).
        payload_set = ("excluded.payload" if (auth_key == "" or not keyed)
                       else "sync_apply_quarantine.payload")
        conn.execute(
            "INSERT INTO sync_apply_quarantine "
            "(entity_id, entity_type, event_type, payload, reason, detail"
            + (", auth_key" if keyed else "") + ") VALUES (?,?,?,?,?,?" + (",?" if keyed else "") + ") "
            "ON CONFLICT(entity_id, event_type" + (", auth_key" if keyed else "") + ") DO UPDATE SET "
            "payload=" + payload_set + ", reason=" + keep_case.format(col="reason")
            + ", detail=" + keep_case.format(col="detail"),
            (entity_id, entity_type, event_type, payload_json, reason, detail)
            + ((auth_key,) if keyed else ()) + evidence + supersedable + evidence + supersedable,
        )
        return False

    @staticmethod
    def _payload_is_older(new: dict, old: dict) -> bool:
        """True when BOTH payloads carry a comparable version (`row_version`, else
        `epoch`) and the new one is lower. Anything not comparable is not "older"."""
        for key in ("row_version", "epoch"):
            a, b = new.get(key), old.get(key)
            if (isinstance(a, int) and not isinstance(a, bool)
                    and isinstance(b, int) and not isinstance(b, bool)):
                return a < b
        return False

    @staticmethod
    def _evict_oldest_junk(conn, where: str, args: tuple) -> bool:
        """Delete the single OLDEST permanent-failure junk row matching `where`
        (review V4r N2). True when one was removed."""
        perm = tuple(sorted(_authority.PERMANENT_PARK_REASONS))
        row = conn.execute(
            "SELECT rowid FROM sync_apply_quarantine WHERE reason IN (" + ",".join("?" * len(perm)) + ")"
            + (" AND " + where if where else "") + " ORDER BY quarantined_at, rowid LIMIT 1",
            perm + args).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM sync_apply_quarantine WHERE rowid=?", (row[0],))
        return True

    @staticmethod
    def _warn_flood_once(key: tuple, message: str, *args) -> None:
        if key not in _FLOOD_WARNED:
            _FLOOD_WARNED.add(key)
            logger.warning(message + " (logged once per process)", *args)

    @staticmethod
    def _park_within_bounds(conn, ev: dict, reason: str, entity_type: str, entity_id: str) -> bool:
        """May a NEW row with this reason be added? (review V3r G2, V4r N1/N2). Makes
        room by evicting the OLDEST permanent junk row when a bound is full; a
        RECOVERABLE failure is never refused (see `authority.PERMANENT_PARK_REASONS`);
        returns False only when the newest row must be dropped."""
        vf = tuple(sorted(_authority.VERIFICATION_FAILURE_REASONS))
        vf_marks = ",".join("?" * len(vf))
        if reason in _authority.VERIFICATION_FAILURE_REASONS:
            scopes = [
                ("entity", f"SELECT COUNT(*) FROM sync_apply_quarantine WHERE entity_type=? AND entity_id=? "
                           f"AND reason IN ({vf_marks})", (entity_type, entity_id, *vf),
                 _authority.MAX_PARKED_VERIFICATION_FAILURES_PER_ENTITY,
                 "entity_type=? AND entity_id=?", (entity_type, entity_id)),
                ("total", f"SELECT COUNT(*) FROM sync_apply_quarantine WHERE reason IN ({vf_marks})", vf,
                 _authority.MAX_PARKED_VERIFICATION_FAILURES_TOTAL, "", ()),
            ]
            if reason in _authority.PERMANENT_PARK_REASONS:
                perm = tuple(sorted(_authority.PERMANENT_PARK_REASONS))
                scopes.append(("junk", "SELECT COUNT(*) FROM sync_apply_quarantine WHERE reason IN ("
                               + ",".join("?" * len(perm)) + ")", perm,
                               _authority.MAX_PARKED_PERMANENT_JUNK_TOTAL, "", ()))
            for name, count_sql, count_args, cap, where, wargs in scopes:
                held = conn.execute(count_sql, count_args).fetchone()[0]
                if held < cap:
                    continue
                if SyncService._evict_oldest_junk(conn, where, wargs):
                    SyncService._warn_flood_once(
                        ("evict", name, entity_type, entity_id) if name == "entity" else ("evict", name),
                        "sync: %d unverifiable events are already parked (%s bound, %s %s); evicting "
                        "the oldest unverifiable ones to make room", held, name, ascii(entity_type),
                        ascii(entity_id))
                    continue
                if reason in _authority.RECOVERABLE_PARK_REASONS:
                    continue   # never dropped: a legitimate event can look like this
                SyncService._warn_flood_once(
                    (name, entity_type, entity_id) if name == "entity" else (name,),
                    "sync: %d unverifiable events are already parked (%s bound, %s %s); dropping further ones",
                    held, name, ascii(entity_type), ascii(entity_id))
                return False
            return True
        # An EVIDENCE row from a VERIFIED signer: bounded per signer installation.
        inst = ev.get("_authority_signer_inst")
        if not inst:
            return True
        ev_reasons = tuple(sorted(_authority.EVIDENCE_PARK_REASONS - _authority.VERIFICATION_FAILURE_REASONS))
        try:
            held = conn.execute(
                "SELECT COUNT(*) FROM sync_apply_quarantine WHERE reason IN ("
                + ",".join("?" * len(ev_reasons)) + ") AND json_extract(payload, '$._auth.inst')=?",
                (*ev_reasons, inst)).fetchone()[0]
        except sqlite3.OperationalError:
            return True
        if held >= _authority.MAX_PARKED_EVIDENCE_PER_SIGNER:
            SyncService._warn_flood_once(
                ("signer", inst),
                "sync: %d rule-breaking events from signer installation %s are already parked; "
                "dropping further ones", held, ascii(inst))
            return False
        return True

    @staticmethod
    def _loads_or_none(raw):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _quarantine_has_auth_key(conn) -> bool:
        try:
            return any(r[1] == "auth_key"
                       for r in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall())
        except sqlite3.OperationalError:
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
                                   branch_fallback_sink: Optional[list] = None,
                                   schema_skew_sink: Optional[list] = None) -> None:
        """Re-attempts every row currently parked in `sync_apply_quarantine`,
        oldest first, and deletes exactly the ones that resolve this pass.
        Safe to call every `apply_pull_result()` -- an empty quarantine table
        is a cheap SELECT and an empty loop; a row that is STILL blocked is
        re-parked by `_quarantine_apply_event` (an upsert onto its own PRIMARY
        KEY: the row's reason/detail/payload follow the latest attempt and its
        `quarantined_at` stays at the first-seen time) rather than
        duplicated, so this is idempotent to call as often as convenient. `local_company_id` is only actually consumed by
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
        a second, separate WARNING.

        `schema_skew_sink`, when given, is forwarded to `_apply_event`
        unchanged, same reasoning as `branch_fallback_sink` above -- a row
        parked purely for schema-version skew is re-evaluated here against
        the CURRENT `local_schema_version_provider()` value on every retry,
        so it stops appearing in this summary (and its own `sync_apply_
        quarantine` row is deleted below) the very first pull tick after
        this device is upgraded past the version that originally refused
        it."""
        # `, rowid` breaks the tie: `quarantined_at` is CURRENT_TIMESTAMP, one-
        # second resolution, and a sale parked in the same second as its own
        # movements/journals MUST be retried first (the parked-children rule
        # depends on the parent leaving quarantine before the child is looked at).
        keyed = self._quarantine_has_auth_key(conn)
        # Review V4r N1: a pass is BOUNDED. Every pull used to retry the whole queue
        # inside the pull's write transaction, so a pile of parked junk made every
        # pull (and every concurrent write waiting on that lock) cost time in
        # proportion to the pile -- measured 8.6 s per pass at 4,000 rows, with an
        # ordinary write failing "database is locked". Now at most RETRY_PASS_MAX_ROWS
        # rows or RETRY_PASS_MAX_SECONDS per pass, oldest first. The start ROTATES: a
        # cursor (the last row touched, kept in memory on this service -- nothing new
        # in the schema, and a keyset rather than an offset because rows leave the
        # set as they resolve or turn permanent) makes the next pass continue after
        # it, so a queue longer than one pass is still reached in full over a few
        # pulls. A queue that fits in one pass has no cursor and is retried from its
        # oldest row every time.
        # Rows that can never become valid (`PERMANENT_PARK_REASONS`) are not retried
        # at all; they stay for a human.
        perm = tuple(sorted(_authority.PERMANENT_PARK_REASONS))
        not_perm = " WHERE reason NOT IN (" + ",".join("?" * len(perm)) + ")"
        select = ("SELECT entity_id, entity_type, event_type, payload, quarantined_at, rowid AS rid, "
                  + ("auth_key" if keyed else "'' AS auth_key")
                  + " FROM sync_apply_quarantine" + not_perm)
        order = " ORDER BY quarantined_at, rowid LIMIT ?"
        budget = _authority.RETRY_PASS_MAX_ROWS
        try:
            cursor = getattr(self, "_retry_cursor", None)
            rows = []
            if cursor is not None:
                rows = conn.execute(
                    select + " AND (quarantined_at, rowid) > (?, ?)" + order,
                    perm + (cursor[0], cursor[1], budget)).fetchall()
            if not rows:
                cursor = None
                rows = conn.execute(select + order, perm + (budget,)).fetchall()
        except sqlite3.OperationalError as exc:
            if self._is_missing_table_error(exc):
                return
            raise
        deadline = time.monotonic() + _authority.RETRY_PASS_MAX_SECONDS
        touched = 0
        key_where = "entity_id=? AND event_type=?" + (" AND auth_key=?" if keyed else "")
        for row in rows:
            if touched and time.monotonic() > deadline:
                break
            touched += 1
            key_args = (row["entity_id"], row["event_type"]) + ((row["auth_key"],) if keyed else ())
            # AUDIT-033 B2b: `rows` is a snapshot. An earlier retry in this same pass
            # may have applied a signed event that SUPERSEDED this parked one (see
            # `_supersede_parked_unsigned`) and deleted it. Re-trying it now would
            # re-park it (an upsert) and resurrect the very row that was just
            # cleared, so skip a row that is no longer there.
            if conn.execute(
                    f"SELECT 1 FROM sync_apply_quarantine WHERE {key_where}", key_args).fetchone() is None:
                continue
            ev = {
                "entity_id": row["entity_id"],
                "entity_type": row["entity_type"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]),
            }
            if self._apply_event_guarded(conn, ev, local_company_id,
                                          branch_fallback_sink=branch_fallback_sink,
                                          schema_skew_sink=schema_skew_sink,
                                          from_quarantine=True):
                # Keyed on the WHOLE primary key -- entity_id alone would risk
                # deleting a DIFFERENT still-blocked event_type that happens
                # to share this entity_id, and since the signature joined the
                # key (AUDIT-033 fix round) two signed events for one entity
                # each own a row that only THEIR retry may delete.
                conn.execute(f"DELETE FROM sync_apply_quarantine WHERE {key_where}", key_args)
        # Continue after the last row looked at, unless this pass reached the end of
        # the queue (then the next one starts again from the oldest).
        if touched and touched == len(rows) and len(rows) < budget:
            self._retry_cursor = None
        elif touched:
            last = rows[touched - 1]
            self._retry_cursor = (last["quarantined_at"], last["rid"])

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

    def _ensure_freshness_loaded(self) -> None:
        """Lazily loads `local_freshness_store`'s persisted values into
        `self._health` on first use -- called from `get_health()` and
        `_record_sync_success()`, never from `__init__` (see
        `local_freshness_store`'s own constructor-parameter docstring for
        why: `SyncService` is constructed at module-import time in
        products/retail/backend/app.py, before `init_app()` has run
        `init_retail()`, so `sync_freshness` does not exist yet at
        construction time on a fresh install).

        The DB read happens OUTSIDE `self._health_lock` (a query is not
        instant, and this lock guards a Flask request thread's
        `get_health()` from blocking on slow work -- the same reasoning
        `_pending_outbox_count` above already follows). Only the resulting
        dict update is taken under the lock, with `self._freshness_loaded`
        re-checked inside it: two threads racing to be "first use" both
        read the database (harmless -- it is a read), but only the first
        one to reach the lock actually applies its result, so a second
        thread's slightly-later read can never clobber a `_record_sync_
        success` write that happened in between. Idempotent after the
        first successful call -- `self._freshness_loaded` short-circuits
        every call after that with no DB access at all -- and a no-op
        immediately when `local_freshness_store` is None (the registry
        construction site)."""
        if self._freshness_loaded or self._local_freshness_store is None:
            return
        conn = self._get_conn()
        try:
            persisted = self._local_freshness_store.load(conn)
        finally:
            conn.close()
        with self._health_lock:
            if not self._freshness_loaded:
                self._health["push"]["last_success_at"] = persisted.get("push")
                self._health["pull"]["last_success_at"] = persisted.get("pull")
                # Stage 7c-ii: the standing override, if any, loaded the
                # same way and at the same time as the two success clocks
                # it will be compared against -- see SyncFreshnessStore's
                # own docstring for why `load` returns all three from one
                # query rather than a second lookup.
                self._health["offline_override_at"] = persisted.get("override_at")
                self._freshness_loaded = True

    def _record_sync_success(self, half: str) -> None:
        self._ensure_freshness_loaded()
        with self._health_lock:
            h = self._health[half]
            h["healthy"] = True
            h["consecutive_failures"] = 0
            now = datetime.now(timezone.utc).isoformat()
            h["last_success_at"] = now
            # Launch-readiness Phase 7 stage 7a: write-through to the
            # database INSIDE this same health_lock block, deliberately --
            # not queued for after it. Unlike self._lock (held across an
            # entire network round-trip; see that lock's own comment in
            # __init__), local_freshness_store.record is one short local
            # sqlite write, so the cost of holding health_lock across it is
            # small -- and the alternative (writing after releasing the
            # lock) would let a concurrent get_health() observe the
            # in-memory "just synced" state while the value that actually
            # survives a restart is still the OLD one, for however long the
            # write takes to land. A no-op when local_freshness_store is
            # None (the registry construction site).
            if self._local_freshness_store is not None:
                conn = self._get_conn()
                try:
                    self._local_freshness_store.record(conn, half, now)
                    conn.commit()
                finally:
                    conn.close()

    def record_offline_override(self) -> None:
        """Launch-readiness Phase 7 stage 7c-ii (docs/launch-readiness/
        phase7-offline-ux.md "Decision 2"): records a manager's approval,
        RIGHT NOW, to keep selling past the 72-hour hard stop. The ONE
        caller is the module-level `record_offline_override()` function
        below, itself the ONE thing retail_api.py's /sync/offline-override
        route calls.

        Mirrors `_record_sync_success` exactly, for the identical reason:
        the write-through to the database happens INSIDE this same
        `_health_lock` block, not queued for after it, so a concurrent
        `get_health()` can never observe the in-memory override while the
        value that actually survives a restart is still the old one. A
        no-op write-through when `local_freshness_store` is None (the
        registry construction site) -- the in-memory value is still set,
        exactly like `_record_sync_success`'s identical branch, but there
        is nothing to persist and nothing ever will read it back, since
        the registry SyncService is never the one create_sale's guard
        consults (retail.db's `_active_service`, not registry.db's, is
        what `get_active_health()` returns -- see app.py's own comment on
        why only the retail instance is given a freshness store at all).
        """
        self._ensure_freshness_loaded()
        with self._health_lock:
            now = datetime.now(timezone.utc).isoformat()
            self._health["offline_override_at"] = now
            if self._local_freshness_store is not None:
                conn = self._get_conn()
                try:
                    self._local_freshness_store.record_override(conn, now)
                    conn.commit()
                finally:
                    conn.close()

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

    def _schema_version_quarantine_count(self) -> int:
        """Rows currently parked in `sync_apply_quarantine` by the
        schema-version skew guard specifically (module docstring's dedicated
        section) -- the number `get_health()` surfaces below through the
        SAME dict the frontend's calm sync-state indicator already reads
        (`_pending_outbox_count`'s own docstring, just above), so a shop
        stuck mid-rollout (module docstring's "UPGRADE ORDERING" paragraph)
        has a number to look at, not just a log file. Filtered to this
        guard's own two reason codes -- `schema_version_too_new` /
        `schema_version_unverifiable` -- not a bare COUNT(*), because that
        table also holds unrelated missing-parent quarantine rows (a
        sale_item whose parent sale has not arrived yet) that this specific
        indicator has no business reporting on.

        Missing-table tolerant for the identical reason `_has_quarantined_
        events` is -- see that method's docstring; a `SyncService` with no
        `local_schema_version_provider` configured never writes either
        reason code, so this returning 0 for such an install is the correct
        steady state, not merely the tolerant fallback."""
        conn = self._get_conn()
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM sync_apply_quarantine "
                    "WHERE reason IN ('schema_version_too_new', 'schema_version_unverifiable')"
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if self._is_missing_table_error(exc):
                    return 0
                raise
            return row["n"] if row else 0
        finally:
            conn.close()

    def get_health(self) -> dict:
        self._ensure_freshness_loaded()
        with self._health_lock:
            push = dict(self._health["push"])
            pull = dict(self._health["pull"])
            offline_override_at = self._health["offline_override_at"]
        never_synced, seconds_since_last_success = self._elapsed_since_last_success(push, pull)
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
            # Schema-version skew guard (module docstring's dedicated
            # section): 0 for every install that has not opted into
            # `local_schema_version_provider` -- see
            # `_schema_version_quarantine_count`'s own docstring.
            "schema_version_quarantine_count": self._schema_version_quarantine_count(),
            # Launch-readiness Phase 7 stage 7a (docs/launch-readiness/
            # phase7-offline-ux.md "FINDING 1"): the elapsed-offline figure
            # 7b (stale-stock hiding) and 7c (24h warning, 72h hard stop)
            # consume. `never_synced` and `seconds_since_last_success` are
            # kept as TWO fields, not collapsed into one, so a fresh install
            # that has never synced successfully cannot be misread as "a
            # very long time offline" (which would wrongly trip 7c's
            # 72-hour hard stop on day one, before the shop has done
            # anything wrong) and cannot be misread as "perfectly fresh"
            # either (a fabricated zero would silently claim health that
            # was never actually observed). No policy lives here: no
            # thresholds, no blocking -- this stage reports facts only, and
            # 7b/7c are what turn `seconds_since_last_success` into a
            # decision.
            "never_synced": never_synced,
            "seconds_since_last_success": seconds_since_last_success,
            # Launch-readiness Phase 7 stage 7c-ii (docs/launch-readiness/
            # phase7-offline-ux.md "Decision 2"): the two facts create_sale's
            # 72-hour block consults. `offline_override_at` is the raw
            # persisted value (or None -- no standing override), included
            # for callers that want the instant itself (the audit trail,
            # a future UI); `offline_override_valid` is the ALREADY-
            # EVALUATED Step 2 comparison (`offline_override_at IS NOT
            # NULL AND offline_override_at > the most recent successful
            # sync instant`), computed here once so create_sale and any
            # other consumer can never each re-derive the comparison
            # slightly differently. Validated by comparison, never by
            # expiry -- see _offline_override_valid's own docstring.
            "offline_override_at": offline_override_at,
            "offline_override_valid": self._offline_override_valid(offline_override_at, push, pull),
            "push": push,
            "pull": pull,
        }

    @staticmethod
    def _offline_override_valid(override_at, push: dict, pull: dict) -> bool:
        """The Step 2 validity rule, and the whole point of storing the
        override next to the two success clocks it is compared against:

            valid  <=>  override_at IS NOT NULL
                        AND override_at > (the most recent successful sync instant)

        Reuses the exact "most recent of push/pull" reasoning
        `_elapsed_since_last_success` already applies to the SAME two
        timestamps, so the two can never disagree about which sync half
        was more recent.

        No timestamps at all (neither half has ever succeeded) returns
        True rather than False when `override_at` is set -- there is no
        successful sync instant an override could be stale AGAINST yet, so
        an override recorded in that state cannot be "invalidated by
        nothing". This branch is unreachable from create_sale's own guard
        in practice (STEP 3's `never_synced is False` precondition already
        means at least one success exists whenever this comparison runs),
        but a comparison function that raises or silently mis-answers on
        an input its only real caller happens never to send is exactly the
        kind of guard ENGINEERING.md warns is proven by nothing."""
        if override_at is None:
            return False
        timestamps = [
            ts for ts in (push["last_success_at"], pull["last_success_at"])
            if ts is not None
        ]
        if not timestamps:
            return True
        most_recent = max(datetime.fromisoformat(ts) for ts in timestamps)
        return datetime.fromisoformat(override_at) > most_recent

    @staticmethod
    def _elapsed_since_last_success(push: dict, pull: dict) -> tuple:
        """Returns `(never_synced, seconds_since_last_success)` from the
        push/pull health snapshots `get_health` just took. `never_synced`
        is True only when NEITHER half has ever recorded a success --
        exactly the fresh-install state `sync_freshness`'s seeded NULL/NULL
        row represents (`_migrate_add_sync_freshness`). Whenever at least
        one half has succeeded at least once, `seconds_since_last_success`
        is measured from the MORE RECENT of the two -- a device can easily
        have pushed successfully more recently than it last pulled, or vice
        versa (an empty outbox still counts as a push success; see
        run_once()'s own comment), and "elapsed since last sync" should
        mean the freshest evidence this device has that it can still talk
        to the relay at all, not whichever half happens to be listed
        first."""
        timestamps = [
            ts for ts in (push["last_success_at"], pull["last_success_at"])
            if ts is not None
        ]
        if not timestamps:
            return True, None
        most_recent = max(datetime.fromisoformat(ts) for ts in timestamps)
        elapsed = (datetime.now(timezone.utc) - most_recent).total_seconds()
        return False, elapsed

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

    def _run_on_tick_hook(self) -> None:
        """The `on_tick` constructor hook, isolated so a failure in it is
        logged loudly and can never stop `run_once()` (see `__init__`)."""
        if self._on_tick is None:
            return
        try:
            self._on_tick()
        except Exception:
            logger.error("SyncService on_tick hook FAILED; this tick's "
                         "push/pull still runs.", exc_info=True)

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
                self._run_on_tick_hook()
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


def record_offline_override() -> bool:
    """Records a manager's offline-sales-stop override (launch-readiness
    Phase 7 stage 7c-ii) on the currently registered SyncService, or is a
    no-op returning False when none is registered -- mirroring
    `get_active_health()`'s / `nudge()`'s own "inert without a registered
    service" precedent exactly. The ONE caller is retail_api.py's
    /sync/offline-override route, and it never reaches this function
    without first confirming `get_active_health()['configured']` is True
    (there is nothing to override on an install with no active service --
    Android, or SYNC_RELAY_BASE_URL unset), so the False branch here is a
    defensive backstop rather than a path any real request is expected to
    take."""
    service = _active_service
    if service is None:
        return False
    service.record_offline_override()
    return True


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
