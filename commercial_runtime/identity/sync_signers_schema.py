"""registry.db v10 -- `sync_signers`: which installation speaks as which device.

AUDIT-033 / S-AUTH (ROADMAP.md "registry v10 CLAIMED"). Every AUTHORITY sync
event (commercial_runtime/sync/authority.py) now carries an Ed25519 signature
by the sending device's licensing key. The receiver derives WHO sent it from
that signature -- never from a payload field -- and this table is how it
remembers, per device id, which installation and key legitimately speak for it:

    sync_signers(
      device_id               TEXT PRIMARY KEY  -- registry device id the signer speaks as
      company_id              TEXT NOT NULL
      installation_id         TEXT NOT NULL UNIQUE   -- Owner installation public id
      key_fingerprint         TEXT NOT NULL          -- sha256 hex of the raw public key
      first_bound_at_utc      TEXT NOT NULL
      last_signed_seen_at_utc TEXT NOT NULL          -- RECEIVER's clock; moved ONLY by `authority.record_liveness`
      lease_issued_at_utc     TEXT                   -- issued_at of the newest Owner lease that vouched for this binding
    )

THE BINDING IS A CACHE OF AN OWNER-ATTESTED FACT (AUDIT-033 fix round, verifier
V finding #1). The first version of this table was trust-on-first-use: whoever
presented a verified lease first won the device id, and an insider could win it
for the main computer's id. Now a signer is only accepted when its Owner-signed
lease NAMES the registry device id it speaks as (`registry_device_id`, pinned by
Owner per installation), so this table only remembers what Owner already said.
A device id already bound to installation A is moved to installation B only by
a NEWER Owner lease naming it (`lease_issued_at_utc`), never by arrival order.

FIX-ROUND EXTENSION (the unreleased v10 is extended rather than a v11 claimed;
the orchestrator amended ROADMAP first). Every part is shape-gated and
idempotent, so a database that already reached v10 before this fix is brought
forward the next time the migration chain runs:

  * `sync_signers.lease_issued_at_utc TEXT` (finding #1, newest lease wins);
  * `sync_signed_seen(sig_sha256 PK, device_id, lease_issued_at_utc,
    first_seen_at_utc)` -- the signatures already counted as proof of life, so a
    replayed or retried event can never refresh `last_signed_seen_at_utc`
    (finding #6);
  * `users.main_row_version INTEGER` -- the highest row_version applied from a
    MAIN-signed user event, DEVICE-LOCAL, never synced (finding #4);
  * `sync_apply_quarantine.auth_key TEXT NOT NULL DEFAULT ''` and the primary
    key `(entity_id, event_type, auth_key)`, so a signed event can no longer be
    overwritten by another one for the same entity (findings #7 and #8).
    Rebuilt with the standing `*_new` idiom in one transaction;
    `rebuild_quarantine_with_auth_key` is shared with retail.db's v42 step.

FIRST BINDING STILL WINS inside one installation: an installation already bound
to device X refuses to also speak as device Y (Owner never re-pins an
installation), and a device id already bound to installation A moves to B only
on a newer Owner lease. The receiver pre-binds ITSELF, so nobody can claim its
own id.

The table is DEVICE-LOCAL by design (it records what THIS receiver has heard,
on THIS receiver's clock; syncing it would let a peer set the clock the
takeover-silence rule reads). It is appended LAST in
registry_db._migrate_registry_schema and applied through `ensure_schema_version`
like every other registry step. An older build ignores the table entirely.
"""
from __future__ import annotations

import json
import sqlite3

SYNC_SIGNERS_DDL = """
CREATE TABLE IF NOT EXISTS sync_signers (
    device_id               TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL,
    installation_id         TEXT NOT NULL UNIQUE,
    key_fingerprint         TEXT NOT NULL,
    first_bound_at_utc      TEXT NOT NULL,
    last_signed_seen_at_utc TEXT NOT NULL,
    lease_issued_at_utc     TEXT
)
"""

SYNC_SIGNED_SEEN_DDL = """
CREATE TABLE IF NOT EXISTS sync_signed_seen (
    sig_sha256          TEXT PRIMARY KEY,
    device_id           TEXT NOT NULL,
    lease_issued_at_utc TEXT NOT NULL,
    first_seen_at_utc   TEXT NOT NULL
)
"""
SYNC_SIGNED_SEEN_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_sync_signed_seen_lease ON sync_signed_seen(lease_issued_at_utc)")

QUARANTINE_NEW_DDL = """
CREATE TABLE sync_apply_quarantine_new (
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    auth_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (entity_id, event_type, auth_key)
)
"""


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def rebuild_quarantine_with_auth_key(conn: sqlite3.Connection) -> bool:
    """AUDIT-033 fix round (verifier V findings #7/#8): give a pre-fix
    `sync_apply_quarantine` the `auth_key` column and the three-column primary
    key. Shared by registry.db (v10 extension) and retail.db (v42 extension).

    WHY the key had to change. It was `(entity_id, event_type)`, so a second
    signed event for the same entity OVERWROTE the first parked one: an insider
    could displace a legitimate parked main-signed owner change with its own
    junk, and an unsigned copy could rewrite a forgery signal into a reason a
    later signed event may clear. `auth_key` names the signature (see
    `authority.quarantine_auth_key`), so different signed events each keep their
    own row, and the unsigned copy (key '') is never stored beside a signed one.

    Rows are copied with `auth_key` computed in Python from the stored payload
    (a signed row gets its signature's key, everything else ''), so the retry
    loop sees exactly what a live park would have written. The old table is
    swapped with DROP + RENAME of the `_new` name, in ONE transaction (the
    `_migrate_categories_to_uuid` idiom): a failure rolls back and leaves the
    old shape for a clean retry. Returns True when it rebuilt, False when the
    table is absent or already has the column (idempotent)."""
    cols = _columns(conn, "sync_apply_quarantine")
    if not cols or "auth_key" in cols:
        return False
    from commercial_runtime.sync.authority import quarantine_auth_key
    own_tx = not conn.in_transaction
    try:
        if own_tx:
            conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT entity_id, entity_type, event_type, payload, reason, detail, quarantined_at "
            "FROM sync_apply_quarantine").fetchall()
        conn.execute("DROP TABLE IF EXISTS sync_apply_quarantine_new")
        conn.execute(QUARANTINE_NEW_DDL)
        for r in rows:
            try:
                payload = json.loads(r[3])
            except (TypeError, ValueError):
                payload = None
            conn.execute(
                "INSERT INTO sync_apply_quarantine_new (entity_id, entity_type, event_type, payload, "
                "reason, detail, quarantined_at, auth_key) VALUES (?,?,?,?,?,?,?,?)",
                (r[0], r[1], r[2], r[3], r[4], r[5], r[6], quarantine_auth_key(payload)))
        conn.execute("DROP TABLE sync_apply_quarantine")
        conn.execute("ALTER TABLE sync_apply_quarantine_new RENAME TO sync_apply_quarantine")
        if own_tx:
            conn.commit()
    except Exception:
        if own_tx:
            conn.rollback()
        raise
    return True


def apply_sync_signers_schema(conn: sqlite3.Connection) -> None:
    """Idempotent; every part is gated on the live shape (see the docstring)."""
    conn.execute(SYNC_SIGNERS_DDL)
    if "lease_issued_at_utc" not in _columns(conn, "sync_signers"):
        conn.execute("ALTER TABLE sync_signers ADD COLUMN lease_issued_at_utc TEXT")
    conn.execute(SYNC_SIGNED_SEEN_DDL)
    conn.execute(SYNC_SIGNED_SEEN_INDEX)
    user_cols = _columns(conn, "users")
    if user_cols and "main_row_version" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN main_row_version INTEGER")
    conn.commit()
    rebuild_quarantine_with_auth_key(conn)
