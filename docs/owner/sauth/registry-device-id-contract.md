# Registry device id contract (AUDIT-033, S-AUTH fix round)

Status: implemented on `feat/retail-ux-p0`, not yet deployed. Owner revision
`5f2c8e1a9b37` (down_revision `54627d59dd59`).

## What problem this closes

Retail computers in one shop sign "shop authority" changes (who the owner is,
which computer is the main, permissions) with their own device key. A receiver
has to know **which computer** a signature belongs to. Until this change it
learned that by trust on first use: the first signed event naming a device id
bound that id to whatever installation signed it. Any computer of the shop
could therefore sign an event claiming the **main's** id first, be bound to it,
and from then on be treated as the main (verifier V finding #1, CRITICAL).

The device id is chosen by the computer. Nothing outside the shop vouched for it.
Owner is the one party every receiver already trusts (it signs every lease), so
Owner now vouches for it.

## The contract

### Request (client to Owner)

`POST /activations` and `POST /check-ins` may carry one new optional field:

| field | type | meaning |
|---|---|---|
| `registry_device_id` | canonical lowercase UUID string, at most 64 chars | the retail registry's `local_device_uuid` of this computer |

- It is part of the **device-signed body** (the device signature covers it).
- It is sent only when the computer has one. A computer without a registry
  identity yet, an old build, and Clinic send nothing.
- The retail app reads it with `device_context.peek_local_device_uuid` (peek,
  never create: AUDIT-032D).

### Assertion (Owner to client)

The Owner-signed lease payload gains one optional field, `registry_device_id`.
It is present **only** when Owner has a pin for the installation **and** the
request carried an id. It is never emitted as `null`.

Why "only when the request carried it": old clients reject unknown assertion
fields (`assertion_verifier.py` allowlist; the Phase 8 Part W outage). A client
that never sends the field must keep receiving byte-for-byte the lease it always
received.

### Owner's rules (`owner/app/licensing_service/registry_pin.py`)

In order:

1. No id sent: nothing happens, no key in the assertion.
2. Not a canonical UUID: event `REGISTRY_DEVICE_ID_INVALID`, nothing pinned.
3. The installation already has a pin: it is **immutable**. Same id again is a
   no-op; a different id records `REGISTRY_DEVICE_ID_CHANGE_REFUSED` and the lease
   keeps carrying the original pin.
4. Another installation of the **same licence** holds the id and can still speak
   (status not `DEACTIVATED`/`REPLACED`): event `REGISTRY_DEVICE_ID_CLASH`,
   nothing pinned, lease omits the key.
5. Otherwise pin it (inside a savepoint; a concurrent winner of the unique index
   is treated as rule 4).

**None of these fail the activation or check-in.** Owner never bricks licensing
over a pin problem. Every non-trivial outcome leaves an `ActivationEvent`
(visible in the installation detail timeline) and an audit row
(`REGISTRY_DEVICE_ID_*`).

Storage: `owner_installations.registry_device_id` and
`registry_device_id_pinned_at` (both nullable), plus the partial unique index
`uq_owner_installations_license_registry_device` on `(license_id,
registry_device_id)` where the id is not null and status is not
`DEACTIVATED`/`REPLACED`. A deactivated or replaced installation frees its id, so
a re-activated or slot-replaced computer that kept its app data can pin its own
id again. `replace_device_key` does not touch the pin (it lives on the
installation).

### Receivers (Crew A, for reference)

A receiver accepts a signer's device id only if the signer's Owner-signed lease
names that id (`device_id_not_attested` otherwise). The signer binding is a cache
of that Owner-attested fact: a newer lease for an id rebinds it, so Owner moving
an id between installations propagates.

## ROLLOUT GATE: Owner must be deployed first

| Owner | Client | Result |
|---|---|---|
| new | old | unaffected: the old client never sends the field, never receives the key |
| new | new | the first activation/check-in pins; the lease attests; the computer can sign |
| **old** | **new** | **the new client never gets an attested lease, so it cannot sign shop-authority events at all** (its changes go out unsigned and are held by receivers). Licensing itself is unaffected. |

So: deploy Owner (run the Alembic upgrade, then restart), confirm a check-in
returns a lease with `registry_device_id`, and only then distribute client
builds. The client records `REGISTRY_DEVICE_ID_NOT_ATTESTED` and logs a WARNING
whenever the lease does not name its id, so a wrong order is visible rather than
silent.

Old LAN hubs reject post-fix leases in auto-join (the allowlist), so a shop's
tills and hub must upgrade together, as already required.

## Upgrade window (residual R1, not eliminated)

For an installation that **existed before** this release, the first check-in that
claims an id wins at Owner. An insider who upgrades and checks in with the main's
registry id before the main does would be pinned to it, and receivers would then
accept that insider as the main until staff act. Mitigations in place:

- devices activated after this release pin at activation (race-free: a
  not-yet-activated device's id has never left the device);
- the retail shell asks Owner for one immediate check-in at boot while its lease
  does not attest (narrows the window from up to 5 minutes to seconds);
- the real main's refused pin raises `REGISTRY_DEVICE_ID_NOT_ATTESTED` locally and
  a `REGISTRY_DEVICE_ID_CLASH` event at Owner.

**Staff recovery:** deactivate or replace the squatter's installation (existing
actions). That frees the id; the real computer pins on its next check-in and
receivers rebind to the installation with the newest lease.

It cannot be closed further without proof of pre-upgrade history that does not
exist: Owner's relay `device_id` is the uploader, not the author, registry
`device` events exist only since registry v9, and relay history is pruned. (The
"earliest relay event" guard was considered and rejected for those reasons.)

## Not covered

- Owner compromise or a staff mistake mis-attests identities (Owner is the root of
  trust).
- The KMP unified app has no S-AUTH and its lease allowlist does not list the
  field; harmless today because it never sends it.
- The Android Keystore signing path and the Kotlin calls are proven by unit tests
  only; no physical phone was used.
