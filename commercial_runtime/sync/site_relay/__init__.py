"""The LAN site relay: a local implementation of Owner's cloud sync wire
contract (`/api/sync/v1/push|pull`), hosted by the main till's own backend
process so paired devices on one shop's wifi converge with no internet.

See `docs/launch-readiness/lan-restaurant-design.md` sec3 ("A. Topology: the
main till is the hub") and sec5 ("D. Security") for the full design, and
`ROADMAP.md`'s "2026-09-14 - retail schema v30 CLAIMED for the LAN site
relay (R-LAN)" entry for the tables.

THE ONE IDEA WORTH CARRYING INTO ANY OF THESE MODULES: this is not a
per-device "use the LAN when offline, the cloud when online" switch. A device
that talked to two relays would hold one `sync_cursor` against two different
sequence-spaces and would fork its own history. LAN devices point at the hub
and ONLY at the hub; the hub alone talks to the cloud, through the forwarder.
Design sec6 states it as the invariant the whole thing rests on: "every device
talks to exactly ONE relay, ever."

What is here:

    * `store.py`      -- raw sqlite3 DAO for the six `site_*` tables (retail
      schema v30, `products/retail/backend/database/schema.py`'s
      `_migrate_add_site_relay`).
    * `replay.py`     -- timestamp-freshness + nonce-burn replay protection
      against `site_sync_nonces`, ported from
      `owner/app/licensing_service/replay.py`. Carries the TTL > 2*skew
      invariant, with an import-time guard.
    * `auth.py`       -- the verify-then-resolve port of
      `owner/app/sync/routes.py::_authenticate`, against this hub's own
      `site_paired_devices` roster instead of Owner's Postgres.
    * `routes.py`     -- the Flask blueprint serving `/push` and `/pull` in
      the byte-identical wire shape, so the shipped `SyncRelayClient` reaches
      a hub with no changes beyond its base URL.
    * `tls_identity.py`   -- the hub's long-lived self-signed EC P-256
      identity and the SPKI pin a paired device trusts.
    * `pinned_transport.py` -- a `requests` transport that will reach ONLY a
      server presenting that pinned public key.
    * `listener.py`   -- the LAN-facing TLS listener: a SEPARATE, minimal
      Flask app carrying only the relay blueprint, so the product's UI and
      session surface stays on loopback where it is.
    * `forwarder.py`  -- the hub's bridge to Owner's real cloud relay: pushes
      the site log up and pulls the cloud stream down, owning
      `site_forward_cursor` (both directions) as its private state. Rows
      pulled down are marked `origin_device_id = UPSTREAM_ORIGIN`, which is
      both how they reach the LAN devices and how the next upstream pass
      knows never to send them back -- the echo guard.

NOT BUILT YET (see the design doc sec9's effort table, and say so plainly
rather than letting someone assume the feature is complete): the Owner-signed
roster fetch/verify/enforce path (`site_roster` is created but nothing writes
it yet, so authorization currently rests on local pairing alone), the QR
pairing UX and the UDP addressing beacon, hub promotion, and site-log pruning
against `site_device_cursors`.
"""
