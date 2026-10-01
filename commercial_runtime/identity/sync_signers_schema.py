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
      last_signed_seen_at_utc TEXT NOT NULL          -- RECEIVER's clock, refreshed on every verified event
    )

FIRST BINDING WINS, in both directions (the PRIMARY KEY and the UNIQUE
installation_id are what make "both directions" a database fact, not just code):
a device id already bound to installation A refuses installation B, and an
installation already bound to device X refuses to also speak as device Y. The
receiver pre-binds ITSELF, so nobody can claim its own id.

Additive only: one CREATE TABLE IF NOT EXISTS. Nothing is backfilled and
nothing is emitted -- the table is DEVICE-LOCAL by design (it records what THIS
receiver has heard, on THIS receiver's clock; syncing it would let a peer set
the clock the takeover-silence rule reads). It is appended LAST in
registry_db._migrate_registry_schema and applied through `ensure_schema_version`
like every other registry step. An older build ignores the table entirely.
"""
from __future__ import annotations

import sqlite3

SYNC_SIGNERS_DDL = """
CREATE TABLE IF NOT EXISTS sync_signers (
    device_id               TEXT PRIMARY KEY,
    company_id              TEXT NOT NULL,
    installation_id         TEXT NOT NULL UNIQUE,
    key_fingerprint         TEXT NOT NULL,
    first_bound_at_utc      TEXT NOT NULL,
    last_signed_seen_at_utc TEXT NOT NULL
)
"""


def apply_sync_signers_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SYNC_SIGNERS_DDL)
    conn.commit()
