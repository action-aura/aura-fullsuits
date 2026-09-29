"""
Aura FullSuits -- per-device login / "one Admin Device" registry.

Phase 1 (this week): schema + read-only-safe CRUD only. Nothing in this
module is wired into any login path yet -- `mt_auth.py`, `auth_routes.py`,
`onboarding_routes.py`, and both products' `config.py` are explicitly
untouched this week, and enforcement stays default-OFF. See
docs/superpowers/specs/2026-08-10-per-device-login.md for the full Phase 0
decision record (requirement disambiguation, company_id authority decision,
and the per-registry.db limitation this table's invariant is scoped to).

Owns two tables:
  - `devices` -- one row per physical device known to this install's
    `registry.db`, keyed by a local UUID stable for the life of the
    install. `is_admin_device` is enforced to have at most one holder per
    `company_id` by a partial unique index (`idx_devices_one_admin`) -- the
    database enforces the invariant, not application code.
  - `user_devices` -- an explicit grant list: an account existing does not
    by itself authorize login from every device, a (user_id, device_id)
    pair must be explicitly granted.

Every function below takes an explicit `conn` (sqlite3.Connection,
`row_factory=sqlite3.Row` expected, matching registry_db.get_conn()) as its
first parameter. Unlike registry_db.py, this module has NO module-level
DB_PATH constant and does no import-time filesystem access -- callers own
connection lifecycle (typically via `registry_db.get_conn()`), this module
only ever operates on the connection it's handed.
"""
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from commercial_runtime.identity import text_safety


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_identity_device_schema(conn: sqlite3.Connection) -> None:
    """Additive-only schema for per-device login. Every statement is
    CREATE TABLE/INDEX IF NOT EXISTS -- no ALTER, no DROP, no data rewrite --
    so calling this twice (or a hundred times) against the same connection
    is always a clean no-op. Called from registry_db.init_registry_db() via
    commercial_runtime.security.migration_safety.ensure_schema_version(),
    which takes a live backup and runs PRAGMA integrity_check before and
    after, only advancing PRAGMA user_version on full success -- same
    paranoid pattern already used for retail.db/clinic.db, now applied to
    registry.db for the first time (REGISTRY_SCHEMA_VERSION in
    registry_db.py).
    """
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS devices (
            id                    TEXT PRIMARY KEY,       -- local UUID, stable for the life of the install
            company_id            TEXT NOT NULL,
            device_fingerprint    TEXT,                   -- licensing pubkey fingerprint, NULL until activated
            owner_installation_id TEXT,
            device_label          TEXT,
            platform              TEXT,                   -- 'WINDOWS' | 'ANDROID'
            is_admin_device       INTEGER NOT NULL DEFAULT 0,
            status                TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'revoked'
            first_seen_at         TEXT NOT NULL,
            last_seen_at          TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_fingerprint
            ON devices(device_fingerprint) WHERE device_fingerprint IS NOT NULL;
        -- THE "one Admin Device" invariant, enforced by the database, not application code:
        CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_one_admin
            ON devices(company_id) WHERE is_admin_device = 1;

        -- Deliberately NO foreign keys here (to devices.id / users.id), for
        -- the same reason registry_db.py's own `user_permissions` table
        -- (also a "grant" table referencing users.id) has none either:
        -- get_conn() runs with PRAGMA foreign_keys=ON, and create_admin's
        -- `DELETE FROM users WHERE role='admin'` (onboarding_routes.py)
        -- replaces the admin row outright when re-onboarding. An FK on
        -- user_id here (default SQLite action, no ON DELETE CASCADE) would
        -- make that DELETE fail outright the moment a grant row existed for
        -- that admin; CASCADE would silently destroy the audit trail this
        -- table exists to preserve (see revoke_device below). Omitting the
        -- FK, same as user_permissions, keeps that DELETE working exactly
        -- as it does today and keeps grant rows as an intentional,
        -- app-managed audit trail rather than a DB-enforced relationship.
        CREATE TABLE IF NOT EXISTS user_devices (
            user_id    TEXT NOT NULL,
            device_id  TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            granted_by TEXT,
            PRIMARY KEY (user_id, device_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_devices_device ON user_devices(device_id);
    ''')


# ── Device CRUD ──────────────────────────────────────────────────────────────

def upsert_local_device(conn: sqlite3.Connection, device_id: str, company_id: str,
                         device_label: str = None, platform: str = None,
                         device_fingerprint: str = None,
                         owner_installation_id: str = None) -> dict:
    """Insert a new device row, or refresh the mutable fields of an existing
    one identified by `device_id` (the local, install-stable UUID -- NOT the
    autoincrement-style id pattern used elsewhere in this suite).
    `first_seen_at` is stamped once, on first insert, and never overwritten;
    `last_seen_at` is refreshed on every call -- this is the "device checked
    in" write. A field left as None on an update leaves the existing stored
    value untouched (COALESCE), so a bare heartbeat call doesn't need to
    resend every field.
    """
    now = _now_iso()
    existing = conn.execute("SELECT id FROM devices WHERE id=?", (device_id,)).fetchone()
    if existing:
        conn.execute('''
            UPDATE devices
            SET company_id=?,
                device_fingerprint=COALESCE(?, device_fingerprint),
                owner_installation_id=COALESCE(?, owner_installation_id),
                device_label=COALESCE(?, device_label),
                platform=COALESCE(?, platform),
                last_seen_at=?
            WHERE id=?
        ''', (company_id, device_fingerprint, owner_installation_id,
              device_label, platform, now, device_id))
    else:
        conn.execute('''
            INSERT INTO devices
              (id, company_id, device_fingerprint, owner_installation_id,
               device_label, platform, is_admin_device, status,
               first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?, ?)
        ''', (device_id, company_id, device_fingerprint, owner_installation_id,
              device_label, platform, now, now))
    conn.commit()
    return get_device(conn, device_id)


def get_device(conn: sqlite3.Connection, device_id: str) -> dict:
    row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    return dict(row) if row else None


def list_devices(conn: sqlite3.Connection, company_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM devices WHERE company_id=? ORDER BY first_seen_at",
        (company_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_admin_device(conn: sqlite3.Connection, company_id: str, device_id: str) -> dict:
    """Atomically move the `is_admin_device` flag to `device_id`, clearing
    any previous holder for `company_id` first -- both statements run
    inside the SAME transaction, before a single commit.

    This ordering is not cosmetic: `idx_devices_one_admin` is a *partial*
    unique index (`WHERE is_admin_device = 1`). If the new admin row were
    set to 1 before the old one was cleared to 0, two rows would
    transiently satisfy the index's predicate at once and the second UPDATE
    would fail the unique constraint. Clearing every row for this
    `company_id` first, unconditionally, makes the ordering safe regardless
    of how many rows currently hold the flag (should only ever be 0 or 1).
    """
    conn.execute(
        "UPDATE devices SET is_admin_device=0 WHERE company_id=?",
        (company_id,),
    )
    cur = conn.execute(
        "UPDATE devices SET is_admin_device=1 WHERE id=? AND company_id=?",
        (device_id, company_id),
    )
    if cur.rowcount == 0:
        conn.rollback()
        raise ValueError(f"device {device_id!r} not found for company {company_id!r}")
    conn.commit()
    return get_device(conn, device_id)


def admin_device(conn: sqlite3.Connection, company_id: str) -> dict:
    """The device row currently holding `is_admin_device` for `company_id`,
    or None if this company has never claimed one (the fresh-install state).

    Returns the whole row, not a bool, because every caller that cares
    whether an admin device exists also wants to say WHICH device it is --
    a 409 telling an admin "another device already holds this" is only
    actionable if it names the holder (`device_label`/`platform`), otherwise
    the user is told "no" with no way to find the machine they have to go
    and use. `has_admin_device()` below is the bool-only shorthand.
    """
    row = conn.execute(
        "SELECT * FROM devices WHERE company_id=? AND is_admin_device=1 LIMIT 1",
        (company_id,),
    ).fetchone()
    return dict(row) if row else None


def has_admin_device(conn: sqlite3.Connection, company_id: str) -> bool:
    """True if some device already holds `is_admin_device` for this company."""
    return admin_device(conn, company_id) is not None


def claim_admin_device(conn: sqlite3.Connection, company_id: str, device_id: str):
    """FIRST claim only: make `device_id` this company's admin device, but
    ONLY if no other device already holds the flag. Returns the updated row
    on success, or None if another device is already the admin device.

    The difference from `set_admin_device()` above is the whole point of
    this function existing separately. `set_admin_device()` clears every
    other holder first and then takes the flag -- it is a *transfer*, and
    the caller must already have proven it is allowed to take the flag away
    from whoever has it. This one is the *bootstrap*: it may only ever move
    the flag from "nobody" to "this device", which is the only admin-device
    decision a company with no admin device yet is in a position to make.

    Atomicity comes from the database, not from a check-then-write here. A
    plain `has_admin_device()` guard followed by an UPDATE would leave a
    window where two devices both read "no admin yet" and both proceed, and
    the second would silently steal the flag from the first. Instead the
    UPDATE sets `is_admin_device=1` WITHOUT clearing anyone -- so if a
    second holder exists, the partial unique index `idx_devices_one_admin`
    (`ON devices(company_id) WHERE is_admin_device = 1`) rejects the write
    itself, and SQLite raises IntegrityError. That is the same "the database
    enforces the invariant, not application code" contract this module's
    docstring already claims, actually used as an enforcement mechanism
    rather than only as a safety net.

    Re-claiming from the device that ALREADY holds the flag is a no-op
    success, not a conflict: the UPDATE rewrites 1 -> 1 on the row that owns
    the existing index entry, so there is nothing for the unique index to
    collide with. That makes a retried/double-clicked claim idempotent
    instead of confusingly 409-ing the very device that won.

    Raises ValueError if `device_id` is not an active row for `company_id` --
    a revoked or unknown device must not become the admin device, and
    silently returning None there would be indistinguishable from "someone
    else already holds it", which is a materially different situation.
    """
    try:
        cur = conn.execute(
            "UPDATE devices SET is_admin_device=1 "
            "WHERE id=? AND company_id=? AND status='active'",
            (device_id, company_id),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    if cur.rowcount == 0:
        conn.rollback()
        raise ValueError(
            f"device {device_id!r} is not an active device for company {company_id!r}"
        )
    conn.commit()
    return get_device(conn, device_id)


def revoke_device(conn: sqlite3.Connection, device_id: str) -> dict:
    """Mark a device 'revoked' and clear its admin flag if it held one.

    Deliberately does NOT delete the device row, and does NOT touch
    `user_devices` -- any grants naming this device are left exactly as
    they were, an intentional audit trail of who had access to what, even
    though the device itself is no longer active. `allowed_on_device` is
    grant-existence-only (see below) and does not consult device status --
    combining "device revoked" with "login allowed" is a login-enforcement
    decision, out of scope this week (device_context.py, Tuesday).
    """
    conn.execute(
        "UPDATE devices SET status='revoked', is_admin_device=0 WHERE id=?",
        (device_id,),
    )
    conn.commit()
    return get_device(conn, device_id)


# ── User <-> device grants ───────────────────────────────────────────────────

def grant_user_device(conn: sqlite3.Connection, user_id: str, device_id: str,
                       granted_by: str = None) -> None:
    """Grant `user_id` authorization to log in from `device_id`. Re-granting
    an existing pair refreshes `granted_at`/`granted_by` rather than
    erroring (INSERT OR REPLACE on the (user_id, device_id) primary key)."""
    conn.execute('''
        INSERT OR REPLACE INTO user_devices (user_id, device_id, granted_at, granted_by)
        VALUES (?, ?, ?, ?)
    ''', (user_id, device_id, _now_iso(), granted_by))
    conn.commit()


def revoke_user_device(conn: sqlite3.Connection, user_id: str, device_id: str) -> None:
    """Remove a specific (user_id, device_id) grant. Unlike `revoke_device`,
    this genuinely deletes the row -- it's the grant itself being revoked,
    not the device being retired, so there is no separate audit-trail row
    to preserve here."""
    conn.execute(
        "DELETE FROM user_devices WHERE user_id=? AND device_id=?",
        (user_id, device_id),
    )
    conn.commit()


def allowed_on_device(conn: sqlite3.Connection, user_id: str, device_id: str) -> bool:
    """True iff `user_id` currently has an active grant for `device_id`.
    Grant-existence only -- does not consult `devices.status`. Combining
    this with device-status/company checks at the point an actual login
    decision is made is device_context.py's job (Tuesday, out of scope
    today).

    STILL NOT CALLED FROM THE LOGIN PATH, deliberately (2026-08-20, Phase 1
    Part A3 of docs/launch-readiness/multi-device-design.md). The design's
    §3 line "we finally call the dormant `user_device_is_authorized`" is
    correct as an intent and cannot be executed yet, for three reasons that
    are about missing machinery rather than about appetite:

      1. NOTHING IN PRODUCTION WRITES A GRANT. `grant_user_device` above has
         zero production callers -- no route, no login path, no UI. The
         `user_devices` table is therefore empty on every install in
         existence. A fail-closed check against an empty allowlist locks out
         100% of users on upgrade day, which is not a security improvement,
         it is an outage.

      2. THE ONLY NON-LOCKOUT WIRING IS THE BUG WE JUST FIXED ELSEWHERE.
         Grant-on-first-login ("trust on first use") would make the check
         issue the very grant it is checking for -- an authorization check
         that hands out the privilege it is testing. That is exactly the
         shape of the audit-log hole recorded in
         device_context.local_device_is_admin's docstring, where asking "am
         I the admin device?" is what made you the admin device. Repeating
         it in the login path would put it somewhere strictly worse.

      3. THE MISSING PIECE IS AN ENROLMENT DECISION, NOT A CHECK. Somebody
         has to answer "this new terminal wants in -- yes or no", and how
         many terminals this shop's licence allows. That is an admin-facing
         screen plus a licence-slot count that lives in
         licensing_contracts, neither of which exists. Design §3 calls a
         device a "licence slot"; until something can issue a slot, a slot
         check has nothing to read.

    `device_context.binding_enforced()` (default OFF) is the gate this
    wiring will hang off when the enrolment surface lands. Wiring the check
    behind an environment flag that is off in every shipped build would
    satisfy the letter of "it has a caller" while executing in exactly zero
    production requests -- a half-wire, and worse than an honest no, because
    the next person to read this would believe device binding was solved.
    """
    row = conn.execute(
        "SELECT 1 FROM user_devices WHERE user_id=? AND device_id=?",
        (user_id, device_id),
    ).fetchone()
    return row is not None


# ══════════════════════════════════════════════════════════════════════════════
# registry v9 DEVICE-SYNC -- devices become a synced entity, "main computer"
# becomes a synced assignment with an epoch.
#
# Everything below is ADDED; nothing above changed behaviour. The "apply"
# helpers (apply_*) never commit: they run inside the sync engine's
# transaction. Every helper that is a whole user action (claim / transfer /
# label / revoke / announce) commits, like the functions above do.
#
# ANDROID-SAFE: stdlib only. No cryptography, no licensing_contracts -- this
# module is imported eagerly by the phone build.
# ══════════════════════════════════════════════════════════════════════════════

WINDOWS_PLATFORM = 'WINDOWS'

#: A device announces itself at least this often. It is the ONLY thing that
#: heals a peer running an older build (a v8 build plain-skips `device` and
#: `admin_device` events and, the registry stream being unstamped, cannot park
#: them) -- the next announcement after that peer upgrades carries the row
#: again. Six hours keeps "last seen" honest on the Plan & devices page
#: without spamming the outbox.
ANNOUNCE_HEARTBEAT_SECONDS = 6 * 60 * 60

DEVICE_LABEL_MAX = 60

#: Registry schema version from which the books rows and presets exist. An
#: ACTIVE device reporting less (or nothing -- a v8 build never announces)
#: blocks preset/role editing: that build would store the Accountant
#: preset's books grants and never read them.
REGISTRY_ROLLOUT_MIN_VERSION = 9

# ── receive-side bounds (2026-09-29 security review, findings H1/H2) ─────────
# Everything a peer sends is untrusted until sender authentication exists
# (finding C1, the NEXT wave -- nothing here signs or authenticates anything).
# These bounds do not stop a forger who behaves plausibly; they stop the
# ABSURD values that were each shown to wedge a shop for good: an epoch nothing
# can ever beat, a timestamp that says "seen" until the year 9999, a version
# number SQLite cannot even store.

#: A peer's clock may run this far ahead of ours before we stop believing it.
FUTURE_SKEW_SECONDS = 10 * 60

#: An assignment epoch is a hand-over counter: 2**31 hand-overs is not a real
#: shop, and a bound keeps `epoch + 1` far from SQLite's 64-bit ceiling (an
#: OverflowError on bind was reachable, and it is not a ValueError, so it
#: escaped the engine's quarantine path).
ASSIGNMENT_EPOCH_MAX = 2 ** 31

#: Nothing in this product existed before this, so an assignment or device
#: timestamp older than it is forged or corrupt. This is a SANITY floor, not a
#: defence: a forger can pick 2024-01-02. It exists so "0001-01-01 wins every
#: tie" is not possible; the real answer to a plausible forgery is C1.
PLAUSIBLE_EARLIEST_UTC = datetime(2024, 1, 1, tzinfo=timezone.utc)

#: A device row_version is a per-row edit counter with the same bound logic.
DEVICE_ROW_VERSION_MAX = 2 ** 31

_ID_MAX_LEN = 128
_PLATFORM_RE = re.compile(r'^[A-Za-z0-9_.-]{1,20}$')

#: Machine-readable reasons carried in the ValueError text, so the engine's
#: quarantine row (and a test) can tell WHY a payload was refused.
REASON_ADMIN_TARGET_INELIGIBLE = 'admin_target_ineligible'
REASON_CANNOT_REVOKE_MAIN = 'cannot_revoke_main'


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _parse_utc(value):
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _utc_before(a, b) -> bool:
    """a strictly earlier than b. Compares as datetimes when both parse (a
    naive and an aware ISO string sort wrong as text), text otherwise."""
    pa, pb = _parse_utc(a), _parse_utc(b)
    if pa is not None and pb is not None:
        return pa < pb
    return str(a) < str(b)


def _max_utc(a, b):
    if not a:
        return b
    if not b:
        return a
    return b if _utc_before(a, b) else a


def _coerce_now(now=None) -> datetime:
    """`now` as an aware UTC datetime. Accepts None (the real clock), a
    datetime, or an ISO string -- the last two exist so a test can hold the
    receiver's clock still without patching anything."""
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, str):
        parsed = _parse_utc(now)
        if parsed is None:
            raise ValueError('now is not a timestamp')
        return parsed
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def clamp_future_utc(value, now=None):
    """A peer-supplied timestamp, kept only as far as we can believe it.

    An unreadable value becomes None (absent), and a value more than
    FUTURE_SKEW_SECONDS ahead of OUR clock is pulled back to exactly that
    limit. Never left as sent: a `last_seen_at` of year 9999 used to be MAX'd
    into the main computer's row, "seen just now" for ever, and a takeover of a
    genuinely dead main could never be allowed again (review finding H2)."""
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    limit = _coerce_now(now) + timedelta(seconds=FUTURE_SKEW_SECONDS)
    if parsed > limit:
        return limit.isoformat()
    return value


def clean_device_label(raw):
    """The ONE rule for a computer's name, used by the rename route and by the
    sync receiver alike. None/blank -> None; otherwise NFC text on a single
    line, Cc/Cf characters stripped (U+200C/U+200D kept), at most
    DEVICE_LABEL_MAX characters. Raises ValueError for a non-string, a line
    break, or too long a name."""
    return text_safety.clean_single_line(raw, max_len=DEVICE_LABEL_MAX, what='Name')


def clean_platform(raw):
    """A platform token as announced by a device ('WINDOWS', 'ANDROID', ...).
    Shape-only on purpose: the set of platforms grows with the product, so this
    refuses what cannot be a token (spaces, markup, 5000 characters) without
    naming which tokens exist. None/blank -> None; ValueError otherwise."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError('platform must be text')
    token = raw.strip()
    if not token:
        return None
    if not _PLATFORM_RE.match(token):
        raise ValueError('platform is not a valid token')
    return token


def _next_epoch(current_epoch) -> int:
    """`current + 1`, bounded. An epoch past ASSIGNMENT_EPOCH_MAX cannot be
    written by anything this build accepts, so refuse with ValueError instead
    of letting SQLite raise OverflowError at bind time."""
    nxt = int(current_epoch) + 1
    if nxt > ASSIGNMENT_EPOCH_MAX:
        raise ValueError('the main-computer assignment counter is exhausted')
    return nxt


def _audit_table_ready(conn) -> bool:
    return _has_table(conn, 'audit_logs')


def record_main_taken_over(conn, company_id: str, old_device_id, new_device_id,
                           by_user_uid=None) -> None:
    """Write the receiver-side WARNING audit row `MAIN_TAKEN_OVER`.

    audit_logs has no severity column, so the severity travels inside
    new_value_json. Best effort: an audit table that is missing or full must
    not undo a sync apply, but a lost row is logged rather than silent. No
    commit -- it rides the caller's transaction."""
    if not _audit_table_ready(conn):
        return
    try:
        conn.execute(
            "INSERT INTO audit_logs (id, company_id, user_id, action, entity_type, "
            "entity_id, new_value_json) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), company_id, None, 'MAIN_TAKEN_OVER', 'DEVICE',
             new_device_id,
             json.dumps({'severity': 'WARNING', 'old_device_id': old_device_id,
                         'new_device_id': new_device_id, 'by_user_uid': by_user_uid})),
        )
    except sqlite3.Error:
        import logging
        logging.getLogger('aura.security.identity').exception(
            'MAIN_TAKEN_OVER audit row was not written')


# ── outbox: one place each event is built ────────────────────────────────────

def device_sync_payload(row: dict) -> dict:
    """The `device` wire payload. Pure. NEVER carries `is_admin_device` (a
    derived local cache -- the synced fact is the assignment) and never
    `company_id` (the receiver uses its OWN)."""
    return {
        'device_id': row['id'],
        'device_label': row.get('device_label'),
        'platform': row.get('platform'),
        'status': row.get('status') or 'active',
        'owner_installation_id': row.get('owner_installation_id'),
        'device_fingerprint': row.get('device_fingerprint'),
        'first_seen_at': row.get('first_seen_at'),
        'last_seen_at': row.get('last_seen_at'),
        'row_version': int(row.get('row_version') or 1),
        'updated_at_utc': row.get('updated_at_utc'),
        'app_version': row.get('app_version'),
        'retail_schema_version': row.get('retail_schema_version'),
        'registry_schema_version': row.get('registry_schema_version'),
    }


def admin_device_sync_payload(assignment: dict) -> dict:
    """The `admin_device` wire payload. Pure. No company_id: the receiver's
    own company owns the row.

    `takeover: true` is present ONLY on an assignment made by the guarded
    takeover (an OPTIONAL key: an old build ignores it, and an ordinary
    hand-over stays byte-identical to what v9 already sent). Its one job is to
    let every receiver write a `MAIN_TAKEN_OVER` warning row, so a takeover is
    never indistinguishable from a hand-over in a peer's audit log."""
    payload = {
        'device_id': assignment['device_id'],
        'epoch': int(assignment['epoch']),
        'assigned_at_utc': assignment['assigned_at_utc'],
        'assigned_by_user_uid': assignment.get('assigned_by_user_uid'),
        'assigned_by_device_id': assignment.get('assigned_by_device_id'),
    }
    if assignment.get('takeover'):
        payload['takeover'] = True
    return payload


def queue_device_sync_event(conn: sqlite3.Connection, device_id: str, event_type: str,
                            extra: dict = None) -> None:
    """Queue a `device` event into registry.db's sync_outbox in the caller's
    transaction (no commit). Re-reads the row so the payload is what is
    stored. `entity_id` = the device's own uuid (Owner requires a uuid). A
    hand-built test schema with no outbox skips silently, like the user
    emitters do. `extra` merges optional keys into the payload (today only
    `reinstated_by_device_id`, see reinstate_device_synced)."""
    if not _has_table(conn, 'sync_outbox'):
        return
    row = get_device(conn, device_id)
    if row is None:
        return
    payload = device_sync_payload(row)
    if extra:
        payload.update(extra)
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), 'device', device_id, event_type,
         json.dumps(payload), _now_iso()),
    )


def announce_admin_assignment(conn: sqlite3.Connection, company_id: str, device_id: str) -> bool:
    """Put THIS device's own main-computer assignment on the wire again, if
    this device is the one the local assignment names. Returns True if an
    `admin_device` event was queued. Commits (it is a user-action helper).

    WHY (review item 12, HIGH). The v9 backfill gives every existing shop an
    epoch-1 assignment and, by design, emits nothing; a re-claim by the holder
    is a silent no-op; and the announce path only ever queued `device` events.
    So in an EXISTING shop the fact "device D is main" never left the main
    computer at all -- a peer that never saw the original claim (an old build
    that skipped it, a device that joined later) had no way to learn it, and
    two tills that both believed they were main under P1a never exchanged the
    epoch-1 rows the tie-break needs to converge them. Re-sending it on
    boot/changed/heartbeat is idempotent: a receiver that already holds it
    sees an identical assignment, which does not "win" and changes nothing.

    Only the named device speaks for it -- every other device's row reaches us
    through the stream, and a device that is not the main must not re-broadcast
    someone else's claim."""
    assignment = get_assignment(conn, company_id)
    if assignment is None or assignment['device_id'] != device_id:
        return False
    if not _has_table(conn, 'sync_outbox'):
        return False
    queue_admin_device_sync_event(conn, company_id, 'update')
    conn.commit()
    return True


def queue_admin_device_sync_event(conn: sqlite3.Connection, company_id: str, event_type: str) -> None:
    """Queue an `admin_device` event for the company's CURRENT assignment (no
    commit). `entity_id` is a fresh uuid4: the wire key is the assignment
    itself (one per company, ordered by epoch), and a company id is not
    guaranteed to be a uuid, which Owner requires of `entity_id`."""
    if not _has_table(conn, 'sync_outbox'):
        return
    assignment = get_assignment(conn, company_id)
    if assignment is None:
        return
    conn.execute(
        "INSERT INTO sync_outbox (id, entity_type, entity_id, event_type, payload, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), 'admin_device', str(uuid.uuid4()), event_type,
         json.dumps(admin_device_sync_payload(assignment)), _now_iso()),
    )


# ── the main-computer assignment ─────────────────────────────────────────────

def get_assignment(conn: sqlite3.Connection, company_id: str):
    """The company's admin_device_assignment row as a dict, or None (also
    None on a pre-v9 hand-built schema with no such table)."""
    if not _has_table(conn, 'admin_device_assignment'):
        return None
    row = conn.execute(
        "SELECT * FROM admin_device_assignment WHERE company_id=?", (company_id,)
    ).fetchone()
    return dict(row) if row else None


def admin_assignment_wins(local, incoming: dict) -> bool:
    """Pure ordering: does `incoming` replace `local`?

    A higher epoch wins. Equal epochs (two computers both claimed while
    apart, or two transfers from the same epoch) resolve by the EARLIER
    `assigned_at_utc`, then the SMALLER device_id -- deterministic, so every
    device reaches the same answer without talking to the others. An
    identical assignment does not "win" (nothing to apply, nothing to emit)."""
    if local is None:
        return True
    inc_epoch, loc_epoch = int(incoming['epoch']), int(local['epoch'])
    if inc_epoch != loc_epoch:
        return inc_epoch > loc_epoch
    # 2026-09-29 (review item 17): compare the two times as INSTANTS. The text
    # test used to send "...:14+00:00" vs "...:14.000000+00:00" (the same
    # moment, two spellings) into the time comparison, where neither is
    # earlier -- so the device_id rule never ran, and which of two devices won
    # depended on which spelling each side happened to hold. Equal instants
    # must fall through to the device_id rule on EVERY device.
    inc_at = _parse_utc(incoming['assigned_at_utc'])
    loc_at = _parse_utc(local['assigned_at_utc'])
    if inc_at is not None and loc_at is not None:
        if inc_at != loc_at:
            return inc_at < loc_at
    elif incoming['assigned_at_utc'] != local['assigned_at_utc']:
        # One side does not parse (only a hand-built row can): fall back to
        # text, as _utc_before always did, rather than crash.
        return _utc_before(incoming['assigned_at_utc'], local['assigned_at_utc'])
    return str(incoming['device_id']) < str(local['device_id'])


def recompute_admin_flag(conn: sqlite3.Connection, company_id: str) -> None:
    """Derive `devices.is_admin_device` from the assignment: clear every row
    for the company, then set the assigned device if it exists locally AND is
    active. Clear-then-set, same reason as `set_admin_device` (the partial
    unique index would reject two transient holders). No commit -- the caller
    owns the transaction. If the assigned device row has not arrived yet,
    nobody holds the flag until the device event lands and this runs again."""
    conn.execute("UPDATE devices SET is_admin_device=0 WHERE company_id=?", (company_id,))
    assignment = get_assignment(conn, company_id)
    if assignment is not None:
        conn.execute(
            "UPDATE devices SET is_admin_device=1 "
            "WHERE id=? AND company_id=? AND status='active'",
            (assignment['device_id'], company_id),
        )


def _assignment_has_takeover_column(conn) -> bool:
    return any(r[1] == 'takeover' for r in conn.execute(
        "PRAGMA table_info(admin_device_assignment)").fetchall())


def _write_assignment(conn, company_id, device_id, epoch, assigned_at_utc,
                      by_user_uid, by_device_id, takeover: bool = False) -> None:
    conn.execute(
        "INSERT INTO admin_device_assignment "
        "(company_id, device_id, epoch, assigned_at_utc, assigned_by_user_uid, assigned_by_device_id) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(company_id) DO UPDATE SET device_id=excluded.device_id, "
        "epoch=excluded.epoch, assigned_at_utc=excluded.assigned_at_utc, "
        "assigned_by_user_uid=excluded.assigned_by_user_uid, "
        "assigned_by_device_id=excluded.assigned_by_device_id",
        (company_id, device_id, int(epoch), assigned_at_utc, by_user_uid, by_device_id),
    )
    # The takeover mark is a v9 column added by device_sync_schema; a
    # hand-built older schema simply has nowhere to keep it.
    if _assignment_has_takeover_column(conn):
        conn.execute("UPDATE admin_device_assignment SET takeover=? WHERE company_id=?",
                     (1 if takeover else 0, company_id))


def _check_optional_id(value, what: str) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > _ID_MAX_LEN):
        raise ValueError(f'malformed admin_device payload: {what}')


def apply_admin_assignment(conn: sqlite3.Connection, company_id: str, incoming: dict,
                           *, now=None) -> bool:
    """SYNC APPLY for an `admin_device` event (for the sync engine). Stores
    the assignment if `admin_assignment_wins`, then recomputes the flag -- on
    the caller's connection, NO commit, so the caller's transaction covers the
    store and the recompute together. Returns True if it changed anything.
    `company_id` is the RECEIVER's own; the payload has none.

    Stores the assignment even when the named device row has not arrived
    (recompute then leaves nobody flagged; `apply_device_event` recomputes
    when the row lands). Raises ValueError -- and ONLY ValueError -- on
    anything it will not store, so the engine can quarantine it instead of
    guessing. Since the 2026-09-29 review (H1) that covers:

      - an epoch that is not a real int (a bool, a string, a float), below 1,
        or above ASSIGNMENT_EPOCH_MAX. `2**63-1` used to be accepted: it can
        never be outranked, so it made a forged main permanent, and
        `epoch + 1` on it overflowed SQLite (OverflowError, not ValueError);
      - an `assigned_at_utc` that does not parse, is more than
        FUTURE_SKEW_SECONDS ahead of the receiver's clock, or predates
        PLAUSIBLE_EARLIEST_UTC (a year-0001 stamp wins every equal-epoch tie);
      - a target the receiver KNOWS is ineligible: a revoked device, or one
        that reported a platform other than Windows
        (`admin_target_ineligible`). A target with no local row is stored
        as before -- its row simply has not arrived yet.

    NONE of this authenticates the sender. Someone who behaves plausibly
    (a sane epoch, a real Windows device) is still believed until events are
    signed (finding C1, a later wave). `now` is optional and exists so a test
    can hold the clock still."""
    if not isinstance(incoming, dict):
        raise ValueError('malformed admin_device payload: not an object')
    device_id = incoming.get('device_id')
    epoch = incoming.get('epoch')
    assigned_at = incoming.get('assigned_at_utc')
    if not device_id or not isinstance(device_id, str) or len(device_id) > _ID_MAX_LEN:
        raise ValueError('malformed admin_device payload: device_id')
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        raise ValueError('malformed admin_device payload: epoch is not an integer')
    if epoch < 1 or epoch > ASSIGNMENT_EPOCH_MAX:
        raise ValueError('malformed admin_device payload: epoch out of range')
    stamp = _parse_utc(assigned_at)
    if stamp is None:
        raise ValueError('malformed admin_device payload: assigned_at_utc')
    if stamp > _coerce_now(now) + timedelta(seconds=FUTURE_SKEW_SECONDS):
        raise ValueError('malformed admin_device payload: assigned_at_utc is in the future')
    if stamp < PLAUSIBLE_EARLIEST_UTC:
        raise ValueError('malformed admin_device payload: assigned_at_utc is implausibly old')
    _check_optional_id(incoming.get('assigned_by_user_uid'), 'assigned_by_user_uid')
    _check_optional_id(incoming.get('assigned_by_device_id'), 'assigned_by_device_id')
    takeover = incoming.get('takeover') is True

    target = get_device(conn, device_id)
    if target is not None:
        if target.get('company_id') != company_id:
            raise ValueError(f'{REASON_ADMIN_TARGET_INELIGIBLE}: device belongs to another company')
        if (target.get('status') or '').lower() == 'revoked':
            raise ValueError(f'{REASON_ADMIN_TARGET_INELIGIBLE}: device is revoked')
        platform = target.get('platform')
        if platform and str(platform).upper() != WINDOWS_PLATFORM:
            raise ValueError(f'{REASON_ADMIN_TARGET_INELIGIBLE}: device is not a Windows computer')

    local = get_assignment(conn, company_id)
    candidate = {'device_id': device_id, 'epoch': epoch, 'assigned_at_utc': assigned_at}
    if not admin_assignment_wins(local, candidate):
        return False
    try:
        _write_assignment(conn, company_id, device_id, epoch, assigned_at,
                          incoming.get('assigned_by_user_uid'), incoming.get('assigned_by_device_id'),
                          takeover=takeover)
        recompute_admin_flag(conn, company_id)
    except (OverflowError, TypeError, sqlite3.InterfaceError, sqlite3.DataError) as exc:
        # Belt and braces: the checks above should make these unreachable, but
        # the contract is "nothing escapes except ValueError", and an
        # exception of another type is what the engine would treat as a crash
        # rather than a payload to set aside.
        raise ValueError(f'malformed admin_device payload: {exc!r}') from exc
    if takeover:
        record_main_taken_over(conn, company_id, local['device_id'] if local else None,
                               device_id, incoming.get('assigned_by_user_uid'))
    return True


def _refuse_ineligible_main(target) -> None:
    """The rule for who may be the main computer, in ONE place, enforced by
    claim and transfer themselves and not only by the routes in front of them
    (review item 18): a route is one caller, and the takeover, a future
    screen or a script is another. Raises ValueError(`admin_target_ineligible:
    ...`) for a revoked device, or one that has reported a platform other than
    Windows. A device that has reported no platform yet is NOT refused (a row
    from before v9 must keep working until its boot announcement lands) --
    the same allowance device_routes._platform_can_be_main makes."""
    if (target.get('status') or '').lower() == 'revoked':
        raise ValueError(f'{REASON_ADMIN_TARGET_INELIGIBLE}: device is revoked')
    platform = target.get('platform')
    if platform and str(platform).upper() != WINDOWS_PLATFORM:
        raise ValueError(f'{REASON_ADMIN_TARGET_INELIGIBLE}: device is not a Windows computer')


def claim_admin_device_with_assignment(conn: sqlite3.Connection, company_id: str,
                                       device_id: str, *, by_user_uid: str = None):
    """The user-facing FIRST claim: same database-enforced bootstrap as
    `claim_admin_device` (the partial unique index rejects a second holder),
    plus the synced fact -- epoch 1 and an `admin_device` event, in ONE
    transaction so a peer can never see the flag without the assignment.

    Returns the device row, or None if another device holds the role (flagged
    locally OR named by a synced assignment this device received but could not
    yet flag because that device row has not arrived -- the flag alone would
    miss that and let a second main be born). Raises ValueError for a device
    that is not active for this company. Re-claiming from the holder is an
    idempotent success with NO new epoch and NO new event."""
    assignment = get_assignment(conn, company_id)
    if assignment is not None and assignment['device_id'] != device_id:
        return None
    before = get_device(conn, device_id)
    if before is not None and before.get('company_id') == company_id:
        # A revoked device is refused by the UPDATE below (status='active');
        # the platform rule has no other home, so it is checked here.
        # Raised before anything is written: nothing to roll back.
        if (before.get('status') or '').lower() != 'revoked':
            _refuse_ineligible_main(before)
    was_flagged = bool(before and before.get('is_admin_device') and before.get('company_id') == company_id)
    try:
        cur = conn.execute(
            "UPDATE devices SET is_admin_device=1 "
            "WHERE id=? AND company_id=? AND status='active'",
            (device_id, company_id),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    if cur.rowcount == 0:
        conn.rollback()
        raise ValueError(
            f"device {device_id!r} is not an active device for company {company_id!r}"
        )
    if not (was_flagged and assignment is not None):
        try:
            epoch = _next_epoch(assignment['epoch']) if assignment is not None else 1
        except ValueError:
            conn.rollback()
            raise
        if _has_table(conn, 'admin_device_assignment'):
            _write_assignment(conn, company_id, device_id, epoch, _now_iso(), by_user_uid, device_id)
            queue_admin_device_sync_event(conn, company_id, 'create' if epoch == 1 else 'update')
    conn.commit()
    return get_device(conn, device_id)


def transfer_admin_device_with_assignment(conn: sqlite3.Connection, company_id: str,
                                          target_id: str, *, by_user_uid: str = None,
                                          by_device_id: str = None,
                                          takeover: bool = False) -> dict:
    """Hand the main-computer role to `target_id`: epoch+1, the flag moves
    clear-then-set, the `admin_device` event is queued -- one transaction.
    Raises ValueError if the target is not this company's device, or if the
    epoch counter is exhausted (bounded arithmetic: no OverflowError), or --
    item 18, enforced HERE and not only in the route -- if the target is
    revoked or a known non-Windows device (`admin_target_ineligible`). A
    second caller (the takeover, a future screen) must not be able to skip
    a rule that only one route remembered.
    Transferring to the device that already holds the role is a no-op (no
    epoch burned, no event). `takeover=True` (only device_takeover passes it)
    marks the assignment so every receiver records MAIN_TAKEN_OVER."""
    target = get_device(conn, target_id)
    if not target or target['company_id'] != company_id:
        raise ValueError(f"device {target_id!r} not found for company {company_id!r}")
    assignment = get_assignment(conn, company_id)
    if assignment is not None and assignment['device_id'] == target_id and target.get('is_admin_device'):
        return target
    _refuse_ineligible_main(target)
    try:
        epoch = _next_epoch(assignment['epoch']) if assignment is not None else 1
    except ValueError:
        conn.rollback()
        raise
    conn.execute("UPDATE devices SET is_admin_device=0 WHERE company_id=?", (company_id,))
    conn.execute("UPDATE devices SET is_admin_device=1 WHERE id=? AND company_id=?",
                 (target_id, company_id))
    if _has_table(conn, 'admin_device_assignment'):
        _write_assignment(conn, company_id, target_id, epoch, _now_iso(), by_user_uid, by_device_id,
                          takeover=takeover)
        queue_admin_device_sync_event(conn, company_id, 'create' if epoch == 1 else 'update')
    conn.commit()
    return get_device(conn, target_id)


# ── the device rows: announce, label, revoke, apply ──────────────────────────

def _bump_device(conn, device_id, sets: dict, now: str, *, touch_seen: bool = True) -> None:
    """One write path for every synced change to a device row: the given
    columns, row_version+1, updated_at_utc, and (only when the device itself
    is speaking) last_seen_at. Centralised so no caller can change a synced
    field without moving the version peers gate on (same reasoning as
    user_accounts._touch_user). A remote rename/revoke passes touch_seen=False:
    the owner renaming a phone says nothing about when the phone was last on."""
    cols = ''.join(f'{k}=?, ' for k in sets)
    seen = ', last_seen_at=?' if touch_seen else ''
    params = [*sets.values(), now] + ([now] if touch_seen else []) + [device_id]
    conn.execute(
        f"UPDATE devices SET {cols}row_version=COALESCE(row_version,1)+1, "
        f"updated_at_utc=?{seen} WHERE id=?",
        params,
    )


def announce_device_fields(conn: sqlite3.Connection, device_id: str, *,
                           device_label=None, platform=None, app_version=None,
                           retail_schema_version=None, registry_schema_version=None,
                           force: bool = False, now: str = None,
                           heartbeat_seconds: int = ANNOUNCE_HEARTBEAT_SECONDS):
    """A device announces ITSELF. Returns (row, announced).

    Announces (row_version+1, one `device` event, committed) when ANY of:
      - `force` (boot: a device that just started, possibly on a newer build
        than its peers last heard, always says so -- this heals an old peer
        that plain-skipped everything we sent before it upgraded),
      - the row has never announced (`updated_at_utc` NULL),
      - a provided field differs from what is stored,
      - the last announcement is `heartbeat_seconds` old or older.
    Otherwise it changes nothing and emits nothing (a call per request would
    flood the outbox). Fields passed as None are left alone."""
    row = get_device(conn, device_id)
    if row is None:
        raise ValueError(f'device {device_id!r} not found')
    now = now or _now_iso()
    wanted = {
        'device_label': device_label, 'platform': platform, 'app_version': app_version,
        'retail_schema_version': retail_schema_version,
        'registry_schema_version': registry_schema_version,
    }
    changed = {k: v for k, v in wanted.items() if v is not None and row.get(k) != v}
    last = _parse_utc(row.get('updated_at_utc'))
    current = _parse_utc(now)
    stale = last is None or (current is not None and (current - last).total_seconds() >= heartbeat_seconds)
    if not (force or changed or stale):
        return row, False
    first = row.get('updated_at_utc') is None
    _bump_device(conn, device_id, changed, now)
    queue_device_sync_event(conn, device_id, 'create' if first else 'update')
    conn.commit()
    return get_device(conn, device_id), True


def set_device_label_synced(conn: sqlite3.Connection, company_id: str, device_id: str, label: str):
    """Rename a device (the owner, from the main computer). row_version+1 and
    a `device` event, one transaction. None if the device is not this
    company's."""
    row = get_device(conn, device_id)
    if not row or row['company_id'] != company_id:
        return None
    _bump_device(conn, device_id, {'device_label': label}, _now_iso(), touch_seen=False)
    queue_device_sync_event(conn, device_id, 'update')
    conn.commit()
    return get_device(conn, device_id)


def revoke_device_synced(conn: sqlite3.Connection, company_id: str, device_id: str):
    """Revoke (never delete) a device: status 'revoked', flag cleared,
    row_version+1, `device` event. Returns the row; None if not this
    company's. Raises ValueError for the current main computer -- its role
    must be handed over first, or the company is left with no main at all.
    Revoking an already revoked device is an idempotent success with no new
    version or event."""
    row = get_device(conn, device_id)
    if not row or row['company_id'] != company_id:
        return None
    if (row.get('status') or '').lower() == 'revoked':
        return row
    assignment = get_assignment(conn, company_id)
    if row.get('is_admin_device') or (assignment and assignment['device_id'] == device_id):
        raise ValueError('main computer cannot be revoked')
    _bump_device(conn, device_id, {'status': 'revoked', 'is_admin_device': 0},
                 _now_iso(), touch_seen=False)
    queue_device_sync_event(conn, device_id, 'update')
    conn.commit()
    return get_device(conn, device_id)


def reinstate_device_synced(conn: sqlite3.Connection, company_id: str, device_id: str,
                            *, by_device_id: str):
    """Undo a revoke -- THE ONLY WAY A REVOKED COMPUTER BECOMES ACTIVE AGAIN.

    Revoked is sticky on the receive side (device_sync_apply_plan): a peer's
    plain "active" never turns a revoked row back. That stays true, because a
    revoked phone announcing itself at the same version must not resurrect.
    This is the one deliberate door: the event carries
    `reinstated_by_device_id`, and a receiver honours it ONLY when it names
    the main computer that receiver already knows. Here, on the sending side,
    the same rule: `by_device_id` must be the assignment's device.

    Row_version+1, status 'active', one `device` event, one commit. Returns the
    row; None if the device is not this company's. Raises ValueError if the
    caller is not the main computer, or the device is not revoked (a device
    that is not revoked has nothing to reinstate, and a silent no-op would let
    the owner believe something happened)."""
    row = get_device(conn, device_id)
    if not row or row['company_id'] != company_id:
        return None
    assignment = get_assignment(conn, company_id)
    if assignment is None or not by_device_id or assignment['device_id'] != by_device_id:
        raise ValueError('only the main computer can reinstate a computer')
    if (row.get('status') or '').lower() != 'revoked':
        raise ValueError('that computer is not removed')
    _bump_device(conn, device_id, {'status': 'active'}, _now_iso(), touch_seen=False)
    queue_device_sync_event(conn, device_id, 'update',
                            extra={'reinstated_by_device_id': by_device_id})
    conn.commit()
    return get_device(conn, device_id)


def device_sync_apply_plan(local_same_id, local_same_fingerprint, incoming: dict,
                           local_main_device_id=None) -> dict:
    """PURE decision for an incoming `device` event. Returns
    {'action': 'insert'|'update'|'touch'|'skip'|'quarantine',
     'reason': str|None, 'set': {column: value}}.

      quarantine device_identity_clash         same id, DIFFERENT
                 owner_installation_id (two installs sharing one device id).
                 Both sides must be non-empty to clash -- an unknown
                 installation is not evidence.
      quarantine duplicate_device_fingerprint  another local id already owns
                 this fingerprint (the unique index would reject the write;
                 surface it instead).
      insert   no local row: everything from the payload, is_admin_device 0.
      update   incoming row_version greater: label/platform/status/versions/
               row_version/updated_at from the payload; identity columns only
               fill blanks (never blank out); last_seen_at = MAX.
      touch    not newer, but a later last_seen_at: only last_seen_at moves.
      skip     nothing to do.
    REVOKED IS STICKY: an incoming 'revoked' applies even when its
    row_version is not greater, and a local 'revoked' is never turned back to
    'active' by a peer -- otherwise a revoked phone announcing at the same
    version as the revoke would silently resurrect. Never deletes.

    THE ONE EXCEPTION is an explicit reinstatement
    (reinstate_device_synced): a strictly newer event that carries
    `reinstated_by_device_id` equal to `local_main_device_id`, the main
    computer THIS receiver already knows. Anything else -- no key, another
    device's id, a receiver that does not know a main yet -- stays revoked.
    A new device row that arrives with the same owner_installation_id as a
    revoked one is inserted revoked too (a revoked install must not walk back
    in under a fresh device id); the owner reinstates it on purpose if wanted."""
    inc_id = incoming['device_id']
    inc_owner = incoming.get('owner_installation_id')
    inc_fp = incoming.get('device_fingerprint')
    if local_same_id is not None:
        loc_owner = local_same_id.get('owner_installation_id')
        if inc_owner and loc_owner and inc_owner != loc_owner:
            return {'action': 'quarantine', 'reason': 'device_identity_clash', 'set': {}}
    if inc_fp and local_same_fingerprint is not None and local_same_fingerprint['id'] != inc_id:
        return {'action': 'quarantine', 'reason': 'duplicate_device_fingerprint', 'set': {}}

    inc_version = int(incoming.get('row_version') or 1)
    if local_same_id is None:
        return {'action': 'insert', 'reason': None, 'set': {
            'device_label': incoming.get('device_label'), 'platform': incoming.get('platform'),
            'status': incoming.get('status') or 'active',
            'owner_installation_id': inc_owner, 'device_fingerprint': inc_fp,
            'first_seen_at': incoming.get('first_seen_at') or incoming.get('last_seen_at') or _now_iso(),
            'last_seen_at': incoming.get('last_seen_at'), 'row_version': inc_version,
            'updated_at_utc': incoming.get('updated_at_utc'),
            'app_version': incoming.get('app_version'),
            'retail_schema_version': incoming.get('retail_schema_version'),
            'registry_schema_version': incoming.get('registry_schema_version'),
        }}

    last_seen = _max_utc(local_same_id.get('last_seen_at'), incoming.get('last_seen_at'))
    local_revoked = (local_same_id.get('status') or '').lower() == 'revoked'
    inc_revoked = (incoming.get('status') or '').lower() == 'revoked'
    if inc_version > int(local_same_id.get('row_version') or 1):
        reinstated_by = incoming.get('reinstated_by_device_id')
        reinstated = bool(local_revoked and not inc_revoked and reinstated_by
                          and local_main_device_id and reinstated_by == local_main_device_id)
        keep_revoked = inc_revoked or (local_revoked and not reinstated)
        return {'action': 'update', 'reason': None, 'set': {
            'device_label': incoming.get('device_label'), 'platform': incoming.get('platform'),
            'status': 'revoked' if keep_revoked else (incoming.get('status') or 'active'),
            'owner_installation_id': local_same_id.get('owner_installation_id') or inc_owner,
            'device_fingerprint': local_same_id.get('device_fingerprint') or inc_fp,
            'row_version': inc_version, 'updated_at_utc': incoming.get('updated_at_utc'),
            'app_version': incoming.get('app_version'),
            'retail_schema_version': incoming.get('retail_schema_version'),
            'registry_schema_version': incoming.get('registry_schema_version'),
            'last_seen_at': last_seen,
        }}
    if inc_revoked and not local_revoked:
        return {'action': 'update', 'reason': None, 'set': {
            'status': 'revoked', 'last_seen_at': last_seen}}
    if last_seen != local_same_id.get('last_seen_at'):
        return {'action': 'touch', 'reason': None, 'set': {'last_seen_at': last_seen}}
    return {'action': 'skip', 'reason': None, 'set': {}}


_DEVICE_TEXT_FIELDS = ('owner_installation_id', 'device_fingerprint', 'app_version')
_DEVICE_TEXT_MAX = 256


def _validated_device_payload(incoming: dict, now=None) -> dict:
    """A COPY of a peer's `device` payload with every field made safe to store,
    or ValueError. The same rules the local routes apply, applied on receipt:
    until the 2026-09-29 review a peer could set any label (with bidi
    overrides, markup, 5000 characters), any platform, and any timestamp.

      label            clean_device_label (single line, Cc/Cf stripped, <= 60)
      platform         clean_platform (a token)
      last_seen_at,
      first_seen_at,
      updated_at_utc   clamp_future_utc: never more than FUTURE_SKEW_SECONDS
                       ahead of the receiver
      row_version      a real int, 1..DEVICE_ROW_VERSION_MAX (a bool or a
                       string is refused, not coerced)
      identity fields  text, bounded length

    Sanitising never REWRITES meaning: a value that is merely untidy is
    cleaned, one that cannot be made safe raises."""
    out = dict(incoming)
    out['device_label'] = clean_device_label(incoming.get('device_label'))
    out['platform'] = clean_platform(incoming.get('platform'))
    for key in ('last_seen_at', 'first_seen_at', 'updated_at_utc'):
        if incoming.get(key) is not None:
            out[key] = clamp_future_utc(incoming.get(key), now)
    if incoming.get('row_version') is not None:
        version = incoming.get('row_version')
        if isinstance(version, bool) or not isinstance(version, int) \
                or version < 1 or version > DEVICE_ROW_VERSION_MAX:
            raise ValueError('malformed device payload: row_version')
    for key in _DEVICE_TEXT_FIELDS:
        value = incoming.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > _DEVICE_TEXT_MAX):
            raise ValueError(f'malformed device payload: {key}')
    for key in ('status', 'reinstated_by_device_id'):
        value = incoming.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > _ID_MAX_LEN):
            raise ValueError(f'malformed device payload: {key}')
    return out


def _installation_was_revoked(conn: sqlite3.Connection, company_id: str, owner_installation_id) -> bool:
    """True when this company already holds a REVOKED device row for the same
    install (same non-empty owner_installation_id). A revoked install that
    walks back in under a fresh device id (a forged or replayed `device`
    event: proof P6b) must not come back active; it is stored revoked and the
    owner reinstates it on purpose."""
    if not owner_installation_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM devices WHERE company_id=? AND owner_installation_id=? "
        "AND LOWER(COALESCE(status,''))='revoked' LIMIT 1",
        (company_id, owner_installation_id),
    ).fetchone()
    return row is not None


def apply_device_event(conn: sqlite3.Connection, company_id: str, incoming: dict,
                       *, now=None) -> dict:
    """SYNC APPLY for a `device` event (for the sync engine): look up, decide
    with `device_sync_apply_plan`, write, then recompute the main-computer
    flag (a device row arriving or being revoked is exactly when the derived
    flag can change). `company_id` = the RECEIVER's own; `is_admin_device` is
    never taken from the payload. NO commit. Returns the plan (its 'action'
    and 'reason' tell the engine whether to quarantine). A malformed payload
    raises ValueError.

    2026-09-29 review (H2), all refused/limited HERE on the receive side:
      - a `revoked` status for the device the local assignment names as MAIN
        is refused (`cannot_revoke_main`), exactly as the local revoke route
        refuses it: a forged event could otherwise strip the main flag from
        the real main and leave the shop with no main at all;
      - timestamps are clamped (see _validated_device_payload);
      - label/platform obey the same rules as the rename route;
      - a revoked device is un-revoked ONLY by an event naming the main this
        receiver already knows (reinstate_device_synced).
    `now` is optional (tests). Nothing here authenticates the sender (C1)."""
    if not isinstance(incoming, dict):
        raise ValueError('malformed device payload: not an object')
    device_id = incoming.get('device_id')
    if not device_id or not isinstance(device_id, str) or len(device_id) > _ID_MAX_LEN:
        raise ValueError('malformed device payload: device_id')
    incoming = _validated_device_payload(incoming, now)
    assignment = get_assignment(conn, company_id)
    main_id = assignment['device_id'] if assignment else None
    if (incoming.get('status') or '').lower() == 'revoked' and main_id == device_id:
        raise ValueError(f'{REASON_CANNOT_REVOKE_MAIN}: the main computer cannot be removed')
    local = get_device(conn, device_id)
    if local is not None and local['company_id'] != company_id:
        # A row under another company on THIS registry is not ours to edit.
        return {'action': 'quarantine', 'reason': 'device_identity_clash', 'set': {}}
    fp = incoming.get('device_fingerprint')
    by_fp = None
    if fp:
        r = conn.execute("SELECT * FROM devices WHERE device_fingerprint=?", (fp,)).fetchone()
        by_fp = dict(r) if r else None
    plan = device_sync_apply_plan(local, by_fp, incoming, local_main_device_id=main_id)
    action, values = plan['action'], plan['set']
    if action == 'insert' and _installation_was_revoked(conn, company_id, values.get('owner_installation_id')):
        values['status'] = 'revoked'
    if action == 'insert':
        cols = ['id', 'company_id', 'is_admin_device', *values]
        conn.execute(
            f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (device_id, company_id, 0, *values.values()),
        )
    elif action in ('update', 'touch'):
        conn.execute(
            f"UPDATE devices SET {', '.join(k + '=?' for k in values)} WHERE id=?",
            (*values.values(), device_id),
        )
    if action in ('insert', 'update'):
        recompute_admin_flag(conn, company_id)
    return plan


# ── reads used by the routes ─────────────────────────────────────────────────

def device_has_open_clash(conn: sqlite3.Connection, device_id: str) -> bool:
    """True if the sync engine holds an open quarantine row that is a clash
    (`device_identity_clash` / `duplicate_device_fingerprint`) for this
    device: keyed by its id, or (the duplicate-fingerprint case, where the
    quarantined event names the OTHER device) its `detail` names it. A
    clashing device must not be handed the main role -- two installs would
    each believe they are the same computer."""
    if not _has_table(conn, 'sync_apply_quarantine'):
        return False
    row = conn.execute(
        "SELECT 1 FROM sync_apply_quarantine WHERE entity_type='device' "
        "AND reason IN ('device_identity_clash','duplicate_device_fingerprint') "
        "AND (entity_id=? OR instr(COALESCE(detail,''), ?) > 0) LIMIT 1",
        (device_id, device_id),
    ).fetchone()
    return row is not None


def is_joined_device(conn: sqlite3.Connection, company_id: str, device_id: str) -> bool:
    """Did THIS device join an existing shop (as opposed to founding it)?
    Such a device must never bootstrap itself into the main computer: the
    role belongs to whoever the shop already gave it to, and a joined device
    has (or will receive by sync) that answer.

    NO DDL -- there is no "joined" column, and adding one would be a schema
    change for a fact the registry already implies. It FAILS CLOSED: when in
    doubt the device is treated as joined, because the wrong answer the other
    way hands the main-computer role to a machine that only joined.

      (0) an `admin_device_assignment` row names THIS device -- it already
          holds the role, so a re-claim is not a bootstrap. Not joined.
      (a) an `admin_device_assignment` row names ANOTHER device -- the shop
          already has a main computer and it is not this one. Certain.
      (b) this install never created the shop's admin: no `ADMIN_CREATED`
          audit row (`create_admin` writes one on the founding device only).
          A JOINED_DEVICE_SEEDED row (written by
          company_rebind.seed_company_settings_for_joining_device) is the
          positive form of the same fact and is checked first.

    2026-09-29 review (item 13): arm (b) used to ALSO require
    `company_settings.country IS NULL`, and treated a missing settings row or
    missing audit table as "not joined". Both were escapes: a joined device
    only had to have a country typed into its settings (or lack the row) to
    become claimable, and the country is not evidence of anything -- it is a
    field the operator can set. The country test is gone and so are both
    escapes.

    RESIDUAL RISK, stated: a genuine founding device whose ADMIN_CREATED audit
    write failed (that write is best-effort by design) is now treated as
    joined and refused a first claim. That is a false refusal, not a false
    grant, and its recovery is the guarded takeover
    (device_takeover.take_over_main) -- or, on a shop that has no main at all,
    the owner-side reset."""
    assignment = get_assignment(conn, company_id)
    if assignment is not None:
        return assignment['device_id'] != device_id
    if not _has_table(conn, 'audit_logs'):
        return True
    joined_marker = conn.execute(
        "SELECT 1 FROM audit_logs WHERE company_id=? AND action='JOINED_DEVICE_SEEDED' LIMIT 1",
        (company_id,),
    ).fetchone() is not None
    if joined_marker:
        return True
    founded_here = conn.execute(
        "SELECT 1 FROM audit_logs WHERE company_id=? AND action='ADMIN_CREATED' LIMIT 1",
        (company_id,),
    ).fetchone() is not None
    return not founded_here


def registry_rollout_blockers(conn: sqlite3.Connection, company_id: str) -> list:
    """Active devices that cannot be trusted to honour the v9 books grants:
    reporting `registry_schema_version` below 9, or nothing at all (an older
    build never announces). Preset cards and role edits stay disabled while
    this is non-empty. A registry not yet migrated to v9 has no such column;
    that cannot occur on a running install (ensure_schema_version runs first)
    so it reads as "nothing blocks" rather than crashing the device list."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(devices)").fetchall()}
    if 'registry_schema_version' not in cols:
        return []
    rows = conn.execute(
        "SELECT id, device_label, platform, registry_schema_version FROM devices "
        "WHERE company_id=? AND status='active' "
        "AND (registry_schema_version IS NULL OR registry_schema_version < ?) "
        "ORDER BY first_seen_at",
        (company_id, REGISTRY_ROLLOUT_MIN_VERSION),
    ).fetchall()
    return [dict(r) for r in rows]
