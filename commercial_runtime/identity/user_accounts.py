"""
Aura FullSuits -- registry account model: roles, capability codes, and PINs.

The *rules* half of registry.db v3. It splits from `account_schema.py` exactly
the way `verification.py` splits from `verification_schema.py`: that module
owns the DDL and the one-time backfill, this one owns the behaviour the
running product applies from then on. Implements
docs/launch-readiness/multi-device-design.md §3 ("Account model" /
"Permissions" / "PIN vs password").

Three things live here.

1. ROLES. `users.role` widens from {admin, employee} to
   {admin, manager, cashier}. The legacy value 'employee' is kept as a
   read-time ALIAS for 'cashier' rather than outlawed, for two reasons that
   are both load-bearing:
     - `onboarding_routes.create_employee` still writes 'employee' when the
       caller does not name a role, because Clinic (out of scope for this
       phase) keys its own RBAC off `clinic_role`, not `role`, and its test
       suite selects staff rows with `WHERE role='employee'`. Changing that
       default would break a product this phase is not allowed to touch.
     - The v3 migration rewrites the rows that exist AT migration time, but
       `user_version` never comes back, so any row minted by that legacy
       default afterwards would be stranded outside the widened set. Routing
       every capability decision through `normalize_role()` means a legacy
       row and a migrated row resolve identically, so the stranded row is a
       cosmetic inconsistency rather than a privilege hole.

2. CAPABILITIES. The eight codes named in design §3, stored as rows in the
   EXISTING `user_permissions` table (registry_db.py:122-129) with the code
   living in its `subsystem` column. Deliberately not a new table: that table
   already has the shape we need -- (user_id, subsystem, access_level) with a
   UNIQUE(user_id, subsystem) -- an admin-facing route that edits it
   (`onboarding_routes.update_perms`), and a reader
   (`mt_auth.mt_require_subsystem`). Codes are namespaced ('retail.sell'), so
   they cannot collide with the single legacy subsystem value ('retail') that
   reader looks up today; seeding them alone changed no existing
   authorization decision. They are now READ by
   `mt_auth.mt_require_capability` on every mutating retail route -- see
   products/retail/backend/api/retail_api.py's "Capability gating" header for
   the route-to-code mapping and the reasoning behind each tier, and
   products/retail/tests/retail_route_capability_matrix_test.py for the
   frozen matrix that keeps a newly added route from inheriting blanket
   access.

3. PINs. A PIN is ATTRIBUTION, never AUTHORIZATION -- see the PIN section
   below, which is the whole point of that distinction being written down in
   code rather than only in the design document.

4. SYNC EMISSION (Phase 5 wave B2 stage 2b,
   docs/launch-readiness/phase5-waveb2-user-sync.md). `_queue_user_sync_event`
   is the one place a `user` sync_outbox row is ever built -- every write site
   across this module and `onboarding_routes.py`/`auth_routes.py` that changes
   an allowlisted field calls it, in the SAME transaction as the row write,
   never after a separate commit. It re-reads the row from `conn` rather than
   trusting values the caller already has in hand, which is what makes the
   payload correct regardless of whether the caller's write took one
   statement or two (`set_user_pin`/`clear_user_pin` write `pin_hash` and then
   bump `row_version` via `_touch_user` as a SEPARATE statement) -- and it is
   what makes a stripped `row_version` bump elsewhere show up here as an
   UNBUMPED payload rather than silently going unnoticed: nothing has to
   police every call site, the re-read simply reports what is actually true.

   Stage 3 (same document) adds `_queue_user_permission_sync_event`, the
   analogous single place a `user_permission` sync_outbox row is built. See
   its own docstring for the trap unique to this table: the payload carries
   the owning user's `uid`, NEVER `user_id` (a local, per-device value the
   receiver mints fresh for every user it applies -- naming it on the wire
   would either resolve to nothing or, far worse, to a different person).
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from datetime import datetime, timezone

from commercial_runtime.security.passwords import (
    PasswordPolicyError,
    hash_password,
    verify_password,
)

#: Exactly the wave B2 field allowlist (design Decision 3) -- `email`,
#: `employee_id`, `role`, `status`, `require_password_change`, `language`,
#: `password_hash`, `pin_hash` -- plus the three identity/version fields
#: every event carries (`uid`, `row_version`, `updated_at_utc`) and
#: `session_version` (MAX'd on apply, never a plain last-write-wins field --
#: see sync_service.py's `user` branch). NEVER `clinic_role`,
#: `failed_login_count`, `locked_until`: each is excluded on the RECEIVING
#: side (sync_service.py's `user` branch reads only this exact column list),
#: so queuing them here would be pointless even before the receiver ignores
#: them -- but this constant is the SECOND place, not just the first, that
#: has to know the boundary, and it is named once here rather than repeated
#: at every call site.
#:
#: `branch_scope_uid` (registry v7, launch-readiness account-hierarchy
#: design §9 item 7) joins the allowlist deliberately: a scope set on one
#: device (the owner's) must reach every other device, or a peer till would
#: keep enforcing a stale (or no) scope for that account. The one
#: non-response-byte difference this makes for Clinic, named rather than
#: hidden: the `user` events Clinic already queues (and never drains -- the
#: recorded ROADMAP posture) now carry one extra, always-NULL
#: `branch_scope_uid` key in their JSON payloads. No Clinic behaviour,
#: response, or test observes outbox payload bytes.
_SYNCED_USER_COLUMNS = (
    "uid", "email", "employee_id", "role", "status", "require_password_change",
    "language", "password_hash", "pin_hash", "row_version", "updated_at_utc",
    "session_version", "branch_scope_uid",
)

# ── Roles ────────────────────────────────────────────────────────────────────

ROLE_ADMIN = 'admin'
ROLE_MANAGER = 'manager'
ROLE_CASHIER = 'cashier'

#: The widened role domain (design §3). Ordered most- to least-privileged.
ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_CASHIER)

#: Pre-v3 value. Still writable by `create_employee`'s default -- see the
#: module docstring for why that default was not changed in this phase.
LEGACY_ROLE_EMPLOYEE = 'employee'

#: Roles an admin may assign when creating or editing an employee. `admin` is
#: absent on purpose: this product allows exactly one admin account per
#: install (`create_admin` is gated on "no valid admin exists yet",
#: onboarding_routes.py:120-125), so "promote an employee to admin" is not an
#: operation that exists, and offering it here would be offering a second
#: owner account through the side door.
ASSIGNABLE_ROLES = (ROLE_MANAGER, ROLE_CASHIER, LEGACY_ROLE_EMPLOYEE)


def normalize_role(raw) -> str:
    """Map any stored `users.role` value onto the widened domain.

    'employee' -> 'cashier' (the legacy alias, see module docstring). Anything
    unrecognised -- including NULL, '' and a value some future/other writer
    invented -- also resolves to 'cashier', the LEAST privileged role. That
    direction is the only safe one: a role we cannot interpret must never be
    read as "manager" or "admin" by default.

    Note this is a read-time interpretation, not a rewrite. The v3 migration
    rewrites the values it recognises; it deliberately leaves values it does
    not understand alone rather than destroying data it cannot explain.
    """
    value = str(raw or '').strip().lower()
    if value in ROLES:
        return value
    return ROLE_CASHIER


# ── Capability codes ─────────────────────────────────────────────────────────

CAP_SELL = 'retail.sell'
CAP_REFUND = 'retail.refund'
CAP_DISCOUNT = 'retail.discount'
CAP_STOCK_ADJUST = 'retail.stock.adjust'
CAP_REPORTS = 'retail.reports'
CAP_CASH_CLOSE = 'retail.cash.close'
CAP_CASH_APPROVE = 'retail.cash.approve'
CAP_EMPLOYEES = 'retail.employees'

#: Exactly the eight codes design §3 names, in that order. The tuple is the
#: seeding contract: every account gets a row for every code, so "this user
#: was never provisioned" and "this user is explicitly denied" stay
#: distinguishable instead of both reading as a missing row.
CAPABILITY_CODES = (
    CAP_SELL,
    CAP_REFUND,
    CAP_DISCOUNT,
    CAP_STOCK_ADJUST,
    CAP_REPORTS,
    CAP_CASH_CLOSE,
    CAP_CASH_APPROVE,
    CAP_EMPLOYEES,
)

#: `user_permissions.access_level` values. The table's own DEFAULT is 'none'
#: and `mt_require_subsystem` already treats 'none' as a refusal, so these are
#: the existing vocabulary, not a new one.
ACCESS_FULL = 'full'
ACCESS_NONE = 'none'

#: Default grant per role -- the three tiers the product owner described:
#: "a cashier sells and takes refunds against a local sale; a manager
#: additionally discounts, adjusts stock, closes a drawer and reads reports;
#: approving a cash variance and managing employees are owner-level."
#:
#: `cashier` holds selling, refunding, and this terminal's own drawer.
#:
#:   - REFUND is a cashier default, which an earlier pass of this module
#:     denied on the general principle that refunds move money without a
#:     sale behind them. That principle is right about refunds in general and
#:     wrong about THIS route: `create_return` (retail_api.py) is sale-bound
#:     -- it resolves a real `sales` row of this company or 404s, recomputes
#:     every figure from the original `sale_items`, and refuses more than was
#:     sold net of what came back already. A cashier cannot conjure a refund
#:     from nothing here, so denying it by default only means every shop must
#:     switch it on before the returns counter works.
#:   - CASH_CLOSE is a cashier default because the person who counted the
#:     drawer is the person who closes it. Whether the VARIANCE that close
#:     records is then accepted is a different authority (CAP_CASH_APPROVE),
#:     which is the entire reason the design gives them separate codes.
#:
#: `manager` adds the four supervisor levers -- discount, stock adjustment,
#: reports, and the drawer -- and holds NEITHER owner code:
#:
#:   - CAP_EMPLOYEES, because creating and disabling accounts is the owner's
#:     authority, and design §4 makes `users` an admin-device single-writer
#:     table, so a manager who could mint accounts would be writing to a table
#:     their device is not the writer for.
#:   - CAP_CASH_APPROVE, because a role holding it alongside CAP_CASH_CLOSE
#:     could count its own drawer and then sign off its own shortfall. That is
#:     exactly the self-approval hole AUDIT-032 closed on the Owner side, and
#:     leaving it open in the DEFAULTS would reintroduce it one shop at a time
#:     without any route being obviously wrong. The only role that holds the
#:     approval is the owner, who holds everything by definition.
#:
#: None of this is a ceiling IN PRINCIPLE. Every code is per-user editable
#: through POST /api/admin/employees/<id>/permissions -- the route is real,
#: reachable, and tenant-scoped -- but that route has NO screen anywhere in
#: the product today. An owner who does not want a particular cashier
#: refunding cannot turn that row off from any UI they can actually open;
#: only a hand-made API call can reach it. So until a screen calls it, these
#: per-role defaults are not merely a starting point an owner can tune away
#: from, they are the access every account of that role actually has, full
#: stop -- the escape hatch this reasoning leans on exists in the server and
#: not, yet, for the person the reasoning is about.
ROLE_CAPABILITIES = {
    ROLE_ADMIN: frozenset(CAPABILITY_CODES),
    ROLE_MANAGER: frozenset(CAPABILITY_CODES) - {CAP_EMPLOYEES, CAP_CASH_APPROVE},
    ROLE_CASHIER: frozenset({CAP_SELL, CAP_REFUND, CAP_CASH_CLOSE}),
}


def capabilities_for_role(role) -> frozenset:
    """The default capability set for a stored role value, legacy aliases and
    unrecognised values included (see `normalize_role`)."""
    return ROLE_CAPABILITIES[normalize_role(role)]


def seed_capabilities_for_user(conn: sqlite3.Connection, user_id: str, role, *,
                                emit_sync: bool = False) -> int:
    """Write one `user_permissions` row per capability code for `user_id`,
    granting the ones `role` implies and explicitly denying the rest. Returns
    the number of rows actually inserted.

    INSERT OR IGNORE against the table's UNIQUE(user_id, subsystem), so this
    is idempotent AND non-destructive: an admin who has already tuned a
    capability for this user keeps their edit, because the seed cannot
    overwrite an existing row. That is what makes it safe to call from the
    v3 migration (which may re-run if a later step fails and `user_version`
    never advanced) and from account creation with the same code path.

    Does NOT commit -- both callers (the migration, and `create_employee`)
    own a transaction that has other statements in it, and a commit here
    would split those in half.

    `emit_sync` (Phase 5 wave B2 stage 3, docs/launch-readiness/
    phase5-waveb2-user-sync.md, Decision 3/5) -- queues a `user_permission`
    sync event for every row THIS CALL actually inserts, so a cashier hired
    on one till arrives on another with a WORKING permission set instead of
    an account that logs in and can do nothing there. Gated per-code on
    THIS statement's own `cur.rowcount`, never the aggregate `inserted`
    count -- the count alone says how many rows landed, not WHICH
    `(user_id, subsystem)` pairs they were, and a code that already had a
    row (rowcount 0 -- the account was seeded before, or an admin already
    tuned it) is correctly never re-emitted, matching this function's own
    non-destructive/idempotent contract.

    Deliberately keyword-only with NO default the caller can silently
    inherit through positional args, but DOES default to `False` -- the
    safe, backward-compatible posture for the many existing direct callers
    (tests across `products/retail/tests` and `commercial_runtime/identity/
    tests` build minimal hand-rolled schemas with no `sync_outbox`/`users`
    table at all, e.g. `retail_registry_v3_accounts_test.py`'s
    `test_a_manager_gets_neither_owner_capability`) that call this function
    purely for its capability-seeding side effect and have no reason to
    know sync exists. The THREE real production write sites that create or
    reset a live account's grants -- `onboarding_routes.create_admin`,
    `create_employee`, `update_role` -- pass `emit_sync=True` explicitly.
    The ONE call site that must NOT emit -- `account_schema.py`'s
    `_seed_capability_rows`, the v3 migration step -- relies on this
    default (and states so explicitly at its own call site): a migration
    backfilling capability rows for accounts that ALREADY exist locally is
    catching this device's own data up to its current shape, not a new
    grant a peer device needs to learn about -- identical reasoning to why
    stage 2b's own v3/v4 backfills never emit either (see this module's
    docstring, item 4, and account_schema.py's own module docstring).
    """
    granted = capabilities_for_role(role)
    inserted = 0
    for code in CAPABILITY_CODES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO user_permissions (id, user_id, subsystem, access_level) "
            "VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, code, ACCESS_FULL if code in granted else ACCESS_NONE),
        )
        rowcount = cur.rowcount or 0
        inserted += rowcount
        if emit_sync and rowcount:
            _queue_user_permission_sync_event(conn, user_id, code, "create")
    return inserted


def user_has_capability(conn: sqlite3.Connection, user_id: str, code: str) -> bool:
    """Read one capability grant. Fail-closed on a missing row: a user with no
    row for a code has not been granted it.

    THE one reader. `mt_auth.mt_require_capability` and
    `mt_auth.session_has_capability` both delegate here rather than writing
    the SELECT themselves, so "an absent row means denied" is stated once. It
    matters that it is stated once and stated this way round: every migrated
    and newly created account is seeded with a row per code (`full` or
    `none`), so a MISSING row means somebody wrote a user by hand -- and an
    authorization check must not read silence as consent.
    """
    row = conn.execute(
        "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
        (user_id, code),
    ).fetchone()
    if not row:
        return False
    # Index by position, not by name: this module is handed connections both
    # with and without `row_factory=sqlite3.Row` (registry_db.get_conn sets
    # it, a bare sqlite3.connect in a migration fixture does not), and
    # positional access is the only form both shapes answer.
    return row[0] not in (None, '', ACCESS_NONE)


def user_holds_subsystem(conn: sqlite3.Connection, user_id: str, subsystem: str) -> bool:
    """Does `user_id` hold the coarse `subsystem` gate ('retail' / 'clinic')
    that `mt_auth.mt_require_subsystem` reads on ~80 routes?

    THE one reader for that gate, the same way `user_has_capability` is for
    the namespaced codes -- and it exists because of a real, shipped lockout:

    Nothing the product writes ever produced the legacy `subsystem='retail'`
    row that gate used to demand. `create_employee` inserts only what the
    request body's `permissions` carries, and both real screens (the desktop
    Employees page, employees.js, and the Android EmployeesScreen) send
    `{email, role}` and nothing else; `seed_capabilities_for_user` writes the
    eight namespaced codes and nothing else. So every cashier and manager
    created through the product's own screens logged in holding
    `retail.sell=full` and was refused every retail route with "Access denied
    to retail" -- on the desktop AND on the phone, same account, same reason
    (observed 2026-09-05 on a two-device rehearsal). Twenty-seven retail test
    fixtures hid it by hand-inserting `('retail', 'full')` before every
    request, manufacturing exactly the row production never has.

    multi-device-design.md §3 says the blanket literal is REPLACED by the
    capability codes. This makes the blanket gate a derived view of them:

      1. An explicit row for `subsystem` itself decides. A 'none' there is a
         revocation an admin made on purpose through `update_perms` (which
         still accepts the legacy values), and a revocation must keep working.
      2. No such row: held iff at least one `<subsystem>.<code>` row is
         granted. A cashier who may sell may reach the app that sells.
      3. No rows at all: False. Silence is not consent -- identical reasoning
         to `user_has_capability` above.

    Prefix matching is done in Python on `startswith(subsystem + '.')` after
    a LIKE prefilter, so a LIKE wildcard in a value can never widen the match
    and 'retailx.sell' can never count towards 'retail'.
    """
    rows = conn.execute(
        "SELECT subsystem, access_level FROM user_permissions "
        "WHERE user_id=? AND (subsystem=? OR subsystem LIKE ?)",
        (user_id, subsystem, subsystem + '.%'),
    ).fetchall()
    prefix = subsystem + '.'
    any_code_granted = False
    for row in rows:
        # Positional, not by name -- see user_has_capability for why.
        name, level = row[0], row[1]
        granted = level not in (None, '', ACCESS_NONE)
        if name == subsystem:
            return granted
        if name.startswith(prefix) and granted:
            any_code_granted = True
    return any_code_granted


# ── PINs ─────────────────────────────────────────────────────────────────────
#
# THE RULE, from design §3, restated here because this is where it is
# enforced rather than merely described:
#
#   A PIN is ATTRIBUTION. It switches the acting user on a terminal that is
#   ALREADY authenticated by a password, and its only product effect is which
#   user id gets stamped on the rows that follow. It is NEVER authorization.
#
# Which is why this module exposes `verify_user_pin` (does this PIN identify
# this user?) and `requires_password_reprompt` (is this action one a PIN can
# never stand in for?), and deliberately exposes NO function that turns a PIN
# into a permission. There is no `pin_grants(...)` to call, so there is
# nothing to misuse.
#
# There is also deliberately NO "look up which user this PIN belongs to". Two
# reasons: it would make the PIN a credential (type four digits, become
# somebody) rather than a confirmation of a user the operator already picked
# from a list; and it would mean verifying the typed PIN against every account
# in the company, which both leaks account existence through timing and costs
# one full PBKDF2 evaluation per account per keypress.

PIN_LENGTH = 4


class PinPolicyError(ValueError):
    """Raised when a PIN fails the documented format (exactly four digits)."""


def _normalize_pin(raw):
    """Fold any locale's decimal digits onto ASCII, or return None when `raw`
    is not exactly PIN_LENGTH digits.

    This product ships Arabic and is RTL, and an Arabic soft keyboard emits
    ARABIC-INDIC digits (U+0660..U+0669), not ASCII. Without folding, a PIN
    set from an Arabic keypad hashes to a different value than the same PIN
    typed as "1234" on the next terminal, and the cashier is locked out of
    their own attribution by a keyboard layout. Folding at BOTH set and
    verify is what makes the two spellings one PIN.

    The obvious `^\\d{4}$` does not merely fail to do this -- it is actively
    worse than nothing, because Python's `\\d` ALREADY matches U+0661 U+0662
    U+0663 U+0664. The naive regex therefore accepts that input, stores it
    verbatim, and guarantees it can never be verified from an ASCII keypad:
    input accepted, account broken, no error raised anywhere.

    (The Arabic-Indic digits are named by code point rather than pasted as
    glyphs on purpose -- this file is otherwise pure LTR source, and a
    bidirectional run inside a docstring reorders unpredictably depending on
    the reader's editor, which is not a property source code should have.)

    Restricted to Unicode category Nd (decimal digit) rather than anything
    `unicodedata.digit()` will answer for, so superscripts and other numeric
    forms ('²') are refused instead of quietly folding to a digit.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) != PIN_LENGTH:
        return None
    folded = []
    for ch in text:
        if unicodedata.category(ch) != 'Nd':
            return None
        value = unicodedata.digit(ch, None)
        if value is None:  # pragma: no cover - every Nd character has a digit value
            return None
        folded.append(str(value))
    return ''.join(folded)


#: Two of the four password-only operations design §3 names have no capability
#: code of their own (they are gated by route-level rules today), so they get
#: action identifiers here -- named in one place so the Phase 2 route wiring
#: attaches to a list rather than re-deriving the policy from prose. The other
#: two ARE capability codes and are reused as-is below.
ACTION_VOID_CLOSED_SALE = 'retail.sale.void_closed'
ACTION_EDIT_COST_OR_PRICE = 'retail.product.cost_or_price.edit'

#: Actions a PIN may NEVER stand in for -- the caller must re-prompt for the
#: acting user's PASSWORD.

PASSWORD_ONLY_ACTIONS = frozenset({
    CAP_CASH_APPROVE,          # cash-variance approval (AUDIT-032's bar stands on top of this)
    CAP_EMPLOYEES,             # employee management
    ACTION_VOID_CLOSED_SALE,   # void of an already-closed sale
    ACTION_EDIT_COST_OR_PRICE, # cost / price edit
})


def requires_password_reprompt(action: str) -> bool:
    """True when `action` is one a PIN switch must never satisfy on its own.

    Design §3 names four: void of a closed sale, cost/price edit, employee
    management, and cash-variance approval. Each is either irreversible, moves
    money after the fact, or hands out authority -- the three shapes where
    "someone was standing at the till" is not enough evidence about who
    actually authorised it.
    """
    return action in PASSWORD_ONLY_ACTIONS


def now_utc_iso() -> str:
    """The one timestamp format `users.updated_at_utc` is ever written in.

    Timezone-AWARE UTC ('...+00:00'), not `datetime.utcnow()`'s naive string.
    This column is a cross-device comparison key (design §4 compares it
    alongside `row_version`), and a mix of naive and aware ISO strings in one
    column sorts wrong the moment the two forms meet -- '2026-08-20T10:00:00'
    and '2026-08-20T09:00:00+00:00' compare as text, and the text says the
    later one is earlier. Every writer -- the v3 backfill, account creation,
    and `_touch_user` below -- goes through here so that cannot happen.
    """
    return datetime.now(timezone.utc).isoformat()


def _queue_user_sync_event(conn: sqlite3.Connection, user_id: str, event_type: str) -> None:
    """Queue a `user` sync_outbox event into registry.db, in the SAME
    transaction as the row write it describes -- matching
    products/retail/backend/api/retail_api.py's `_queue_sync_event` shape
    (identical `sync_outbox` columns: id, entity_type, entity_id, event_type,
    payload, created_at), placed here rather than imported from retail
    because `users` lives in registry.db, not retail.db (design Decision 1),
    and `commercial_runtime.identity` must stay product-agnostic.

    Deliberately takes a full `Connection`, never a caller's own `Cursor`:
    this issues its own `conn.execute(...)` calls, so it can never disturb a
    caller's `cur.lastrowid`/`cur.rowcount` from a PRIOR statement on that
    same cursor -- the exact trap this project has a real, previously-shipped
    bug from (see `_default_branch` in retail_api.py). Every call site in
    this module and in `onboarding_routes.py` passes `conn`, never `cur`.

    Re-reads the row's CURRENT state from `conn` rather than building the
    payload from values the caller already has -- see this module's
    docstring, item 4, for why that is what makes the payload correct
    regardless of how many statements the write took, and what makes a
    stripped `row_version` bump elsewhere show up as an unbumped payload
    instead of going unnoticed.

    `entity_id` is `uid`, NEVER `users.id` -- Owner parses `entity_id` as a
    UUID and rejects anything else (design doc, "Settled before
    implementation"); `users.id` is a private per-device detail with no such
    guarantee. A row with no `uid` yet is skipped rather than queuing a
    malformed event -- unreachable in practice, since both insert sites
    (`create_admin`, `create_employee`) set one at creation, but a guard
    costs nothing and a future insert site that forgot to set `uid` would
    otherwise queue an event Owner refuses on the wire, at a point far more
    expensive to discover than here.
    """
    row = conn.execute(
        "SELECT {} FROM users WHERE id=?".format(", ".join(_SYNCED_USER_COLUMNS)),
        (user_id,),
    ).fetchone()
    if row is None or not row["uid"]:
        return
    payload = {col: row[col] for col in _SYNCED_USER_COLUMNS}
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "user", row["uid"], event_type, json.dumps(payload), now_utc_iso()),
    )


def _queue_user_permission_sync_event(conn: sqlite3.Connection, user_id: str, subsystem: str,
                                       event_type: str) -> None:
    """Queue a `user_permission` sync_outbox event into registry.db, in the
    SAME transaction as the row write it describes -- identical shape and
    identical reasoning to `_queue_user_sync_event` above (Phase 5 wave B2
    stage 3, docs/launch-readiness/phase5-waveb2-user-sync.md, "Decision 5
    -- user_permissions is in scope").

    THE TRAP this function exists to design around (identified while
    reviewing stage 2a, see the design doc's own "trap stage 3 must not
    walk into" section): `user_permissions.user_id` is the LOCAL `users.id`
    -- a value the receiving device MINTS FRESH for every user it applies
    (`str(uuid.uuid4())` in sync_service.py's `user` branch). It is NOT the
    wire identity and is never valid to carry across devices; a payload
    naming the SENDING device's `user_id` would, on the receiver, either
    resolve to nothing or -- far worse -- to a DIFFERENT PERSON who happens
    to have been minted that same local id. So the payload NEVER carries
    `user_id` at all. It carries the owning user's `uid` (read fresh from
    `users` here, never trusted from a caller who might be holding a stale
    copy), and the apply side (`sync_service.py`'s `user_permission`
    branch) resolves `uid` back to ITS OWN local `users.id` before writing
    anything -- exactly the way wave A's `sale_item` branch resolves
    `sale_uid` via `_local_id_by_uid`.

    `entity_id` is a freshly minted uuid4 on every call, unlike
    `_queue_user_sync_event`'s `uid` (a real, STABLE, cross-device
    identity). `user_permissions` has no such column of its own -- design
    Decision 2 states this table's real wire key is the composite
    `(user_uid, subsystem)` carried IN THE PAYLOAD, and the local
    `user_permissions.id` this device happens to have "is a per-device
    value and means nothing on the wire" (same document). Owner only
    requires `entity_id` to be a well-formed UUID
    (`owner/app/sync/routes.py::_build_event`); it plays no role in this
    table's own conflict resolution on the receiver (`ON CONFLICT(user_id,
    subsystem)`, never `ON CONFLICT` on `entity_id`), so there is nothing
    to gain from trying to keep it stable across a row's create/update/
    delete lifecycle the way a table that actually upserts on its own uid
    needs to.

    `event_type='delete'` (design Decision 4) carries no `access_level` in
    its payload -- there is nothing left to report once the grant is gone,
    only WHICH `(user, subsystem)` pair the receiver must also stop
    granting. Callers issue this AFTER their own DELETE has run (matching
    every other emit-after-write call site in this module), but the
    OWNING USER'S `users` ROW must still exist at the moment this runs,
    because `uid` is read from it here -- `onboarding_routes.create_admin`'s
    re-onboarding path is the one call site where this ordering is
    load-bearing (the old admin's `user_permissions` rows, and this call,
    both run BEFORE its `users` row is hard-deleted, never after).

    `event_type` in ('create', 'update') re-reads the row's CURRENT
    `access_level` from `conn` -- same "never trust what the caller already
    has in hand" reasoning as `_queue_user_sync_event`'s own docstring, and
    what makes calling this from `seed_capabilities_for_user`'s per-code
    loop (which never holds the row open on its own cursor) correct with
    no second read needed anywhere else. A row that has already vanished by
    the time this runs (should be unreachable -- every real call site emits
    immediately after its own INSERT, in the same transaction) is skipped
    rather than queuing a payload with no `access_level` at all.

    A user row with no `uid` yet (should be unreachable -- every real
    account gets one at INSERT, and account_schema.py's v3 backfill covers
    every row that predates that) is skipped exactly like
    `_queue_user_sync_event`'s own guard: no malformed event is ever queued
    for Owner to reject, and -- as with that function -- neither `password_
    hash` nor `pin_hash` is ever anywhere near this payload to begin with,
    so there is no credential to leak even in the unreachable case.
    """
    user_row = conn.execute("SELECT uid FROM users WHERE id=?", (user_id,)).fetchone()
    if user_row is None or not user_row["uid"]:
        return
    user_uid = user_row["uid"]
    if event_type == "delete":
        payload = {"user_uid": user_uid, "subsystem": subsystem}
    else:
        perm_row = conn.execute(
            "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
            (user_id, subsystem),
        ).fetchone()
        if perm_row is None:
            return
        payload = {
            "user_uid": user_uid,
            "subsystem": subsystem,
            "access_level": perm_row["access_level"],
        }
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), "user_permission", str(uuid.uuid4()), event_type,
         json.dumps(payload), now_utc_iso()),
    )


def _touch_user(conn: sqlite3.Connection, user_id: str) -> None:
    """Bump the row's `row_version` and stamp `updated_at_utc`, then queue
    the `user` sync event the bump implies.

    `users` is a shared, admin-device-single-writer table in design §4, and
    `row_version` is the reject-stale marker the sync apply path will compare.
    Every write to a user row therefore has to move it, or the row silently
    looks unchanged to a peer that already has an older copy -- and, as of
    Phase 5 wave B2 stage 2b, has to queue an event too, or a peer never
    learns of the change at all regardless of what the local row says.
    `_queue_user_sync_event` is called HERE, inside the shared helper, rather
    than separately at each of this function's two call sites
    (`set_user_pin`/`clear_user_pin`) -- so neither call site can add a write
    that bumps the version without also emitting; there is exactly one
    bump-and-emit helper, not two things to keep in sync by hand.

    The account-management routes in `onboarding_routes.py` (create, invite
    setup, enable/disable, role change, permission change, password reset)
    apply the same bump inline rather than calling this helper, because each
    of them already has the row open in an UPDATE of its own and a second
    statement would be a second write for no reason -- each of THOSE sites
    calls `_queue_user_sync_event` directly instead. `auth_routes.set_language`
    used to be the one documented gap here ("language...has no cross-device
    meaning today, and Phase 5 has to decide whether it travels"): stage 2b's
    field allowlist settles that question -- `language` IS one of the synced
    fields -- so that route now bumps inline (matching every other
    onboarding_routes.py site) and queues its own event; it is no longer a
    gap.
    """
    conn.execute(
        "UPDATE users SET row_version=COALESCE(row_version, 1) + 1, updated_at_utc=? WHERE id=?",
        (now_utc_iso(), user_id),
    )
    _queue_user_sync_event(conn, user_id, "update")


def set_user_pin(conn: sqlite3.Connection, user_id: str, pin: str) -> None:
    """Store a hashed PIN for `user_id`. Never commits (see
    `seed_capabilities_for_user`).

    Hashed with `commercial_runtime.security.passwords.hash_password` -- the
    exact function and parameters the password column uses (PBKDF2-HMAC-SHA256,
    per-value random salt, 600k iterations). Never plaintext, never a bare
    digest.

    Using the full password KDF for four digits is not overkill, it is the
    only thing making a four-digit secret defensible at all: the keyspace is
    10,000, so an unsalted or cheap hash of a leaked registry.db is broken
    instantly, while ~600k iterations per guess puts a full sweep of one
    account's keyspace into hours rather than milliseconds. It also rate-limits
    the online path for free -- one verification costs a few hundred
    milliseconds, which is invisible to a cashier switching tills and
    ruinous to a script.
    """
    normalized = _normalize_pin(pin)
    if normalized is None:
        raise PinPolicyError(f"PIN must be exactly {PIN_LENGTH} digits.")
    try:
        pin_hash = hash_password(normalized)
    except PasswordPolicyError as exc:  # pragma: no cover - unreachable, _normalize_pin guarantees 4 chars
        raise PinPolicyError(str(exc)) from exc
    conn.execute("UPDATE users SET pin_hash=? WHERE id=?", (pin_hash, user_id))
    _touch_user(conn, user_id)


def clear_user_pin(conn: sqlite3.Connection, user_id: str) -> None:
    """Remove a user's PIN. Their account is unaffected -- password login is
    the credential; the PIN is only the terminal-side attribution shortcut."""
    conn.execute("UPDATE users SET pin_hash=NULL WHERE id=?", (user_id,))
    _touch_user(conn, user_id)


def verify_user_pin(conn: sqlite3.Connection, user_id: str, pin: str) -> bool:
    """Does `pin` identify `user_id`? ATTRIBUTION ONLY -- see the section
    header above. A True here means "stamp this user id on the rows that
    follow", never "let this user do the thing".

    False -- not an exception -- for a user with no PIN set, an unknown
    user id, and a malformed PIN. In particular a user whose `pin_hash` is
    NULL can never be matched by any input, so "has not set a PIN yet" is not
    a state that quietly accepts everything.
    """
    normalized = _normalize_pin(pin)
    if normalized is None:
        return False
    row = conn.execute("SELECT pin_hash FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        return False
    stored = row[0]  # positional -- see user_has_capability's note on row_factory
    if not stored:
        return False
    return verify_password(normalized, stored)
