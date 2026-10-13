"""SYNC-BOOT Phase 2: a late or lost device gets the shop's data as a COPY, not as
a replay of history the relay has already pruned.

Why this package exists: a relay (Owner's cloud, or the LAN hub's site log) keeps
only a tail once it prunes. A device that joins, or comes back, below that tail
used to receive a partial, gap-free-looking history and carry on -- no error,
wrong books. Phase 1 made the relay say `RESYNC_REQUIRED` instead. This package
is what the device does about it: ask a peer that HOLDS the shop's data (here the
LAN hub, from its own database) for a scrubbed copy that states the exact relay
position it is complete up to (`as_of`), install it, and resume pulling from
there.

  * `classes`   -- the vocabulary: every table of a product database is exactly
                   one class (SYNCED / DERIVED / AUTHORITY / LOCAL_CARRY /
                   LOCAL_DROP / BLOCK_IF_ROWS), and what each class means for the
                   producer's scrub and the installer's carry. The PRODUCT supplies
                   the table-to-class map (shared code must not import products).
  * `producer`  -- builds the copy: VACUUM INTO a temp file, `as_of` read from the
                   copy's OWN cursor, scrub by class, zip with a manifest that
                   carries an authority-signed envelope.
  * `installer` -- verifies, refuses (wrong shop, newer schema, open cash drawer),
                   stages, and swaps at the next start with restore_backup's
                   rollback discipline.
  * `transport_hub` -- the device's request to the LAN hub's `POST /bootstrap`.
  * `board`     -- the hub's in-memory list of devices asking the MAIN computer to
                   re-announce the shop's authority state.

Authority data (users, permissions, till numbers, who is main) is NEVER copied
from a peer: a copy would bypass the signing that stops one computer rewriting
another's staff. It is dropped from the copy and returns signed, from the main
computer, as ordinary events (`reannounce.force_main_reannounce`).
"""
