# Registry device id contract (AUDIT-033, S-AUTH fix round)

Status: implemented on `feat/retail-ux-p0`, not yet deployed. Owner revisions
`5f2c8e1a9b37` (down_revision `54627d59dd59`) and, for the round-2 rules below
(review V3o G1/G2/G3), `7c41d2e9a0b5` (down_revision `5f2c8e1a9b37`).

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
4. **Reservation (R1/R2).** Another installation anywhere on the **licence chain**
   holds the id, in **any status** (ACTIVE, SUSPENDED, DEACTIVATED, REPLACED, key
   revoked): event `REGISTRY_DEVICE_ID_CLASH`, nothing pinned, lease omits the
   key. The chain is every licence connected through `replaced_by_license_id`, in
   both directions, so replacing the shop's licence does not reopen its ids.
   A pinned id is reserved for its installation for good.
5. **History guard (R3).** Owner looks in the shop's synced event log (`owner_sync_events`,
   across the licence chain) for the earliest event naming the id: an
   `entity_type='device'` event whose `entity_id` is the id, or an `admin_device`
   event whose payload `device_id` is the id. If that event's `client_created_at`
   is earlier than the installation's `created_at` minus 10 minutes
   (`HISTORY_SKEW_MARGIN`, the original computer's clock skew), the installation
   was activated after the id was already in the shop's history, so it cannot be
   the id's original computer: event `REGISTRY_DEVICE_ID_HISTORY_CONFLICT`, nothing
   pinned, lease omits the key. (Index: `ix_owner_sync_events_license_entity`.)
6. Otherwise pin it (inside a savepoint; a concurrent winner of the unique index
   is treated as rule 4).

### R4. Staff transfer: the only way an id moves

`transfer_registry_device_id(session, registry_device_id=, to_installation_id=,
actor_staff_user_id=, reason=)` in `registry_pin.py`, or from a shell:

    flask licensing transfer-registry-id --registry-device-id <id> --to <installation_id> --actor <staff email> --reason "<text>"

Both installations must be on the same licence chain, a holder must exist, the
target must not already be pinned to a different id, and a reason is required.
The old holder is cleared and the target pinned in one transaction, an
`ActivationEvent` `REGISTRY_ID_TRANSFERRED` is written on both installations and
an audit row records actor, reason, from and to. Typical uses: the main's
hardware died (slot replaced), or the shop's licence was replaced. After the
transfer the new holder's next check-in returns a lease naming the id. An Owner UI
button is a follow-up (Owner UI belongs to another owner of the code).

**None of these fail the activation or check-in.** Owner never bricks licensing
over a pin problem. Every non-trivial outcome leaves an `ActivationEvent`
(visible in the installation detail timeline) and an audit row
(`REGISTRY_DEVICE_ID_*`).

Storage: `owner_installations.registry_device_id` and
`registry_device_id_pinned_at` (both nullable), plus the partial unique index
`uq_owner_installations_license_registry_device` on `(license_id,
registry_device_id)` where the id is not null, in **every** status (migration
`7c41d2e9a0b5`; the first version of the index freed the id on
`DEACTIVATED`/`REPLACED`, which review V3o G2 showed handed it to a lurking
installation). `replace_device_key`, slot release and slot replacement never touch
the pin (it lives on the installation); only the staff transfer moves it.

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

## Residual risk after the round-2 rules

The pin is first-claim-wins only when Owner has nothing to compare against. What
remains, stated as it really is:

- **The race exists only for an installation older than the id's first synced
  event, plus LAN-only shops.** For an installation that existed before this
  release and has not pinned yet, the first check-in that claims an id wins unless
  the shop's synced history already names that id from before the claimant
  existed (rule 5). An insider whose installation is OLDER than the main's first
  synced event, and who checks in with the main's id first, still wins. Shops
  whose registry events never reached Owner (LAN-only, never relayed) have no
  history, so rule 5 cannot help there at all. History is also pruned over time
  (`owner/app/sync/pruning.py`), which only weakens the guard, never strengthens a
  claim.
- Rule 5 can **refuse a legitimate computer**: one whose registry row was created
  and relayed by other computers (LAN-joined) before the computer itself activated
  with Owner. Its lease omits the id, `REGISTRY_DEVICE_ID_HISTORY_CONFLICT` is
  recorded for staff, and the staff transfer (R4) resolves it.
- Mitigations unchanged: devices activated after this release pin at activation
  (race-free: a not-yet-activated device's id has never left the device); the
  retail shell asks Owner for one immediate check-in at boot while its lease does
  not attest; a refused real main raises `REGISTRY_DEVICE_ID_NOT_ATTESTED` locally
  and a `REGISTRY_DEVICE_ID_CLASH` / `..._HISTORY_CONFLICT` event at Owner.
- `relay device_id` is the uploader, not the author, so the guard reads
  `entity_id` / payload `device_id`, never the relay `device_id`.

**Staff recovery:** run the staff transfer (R4) to the real computer's installation.
Deactivating or replacing the squatter no longer frees the id (that was the V3o
G2 defect); it stays reserved to the squatter until transferred.

### Recorded, deliberately not fixed

- **Automatic continuity across a licence replacement was not built.** The plan
  proposed letting a computer on the replacement licence take its id over from the
  old licence's installation when its device key fingerprint matched. That can
  never fire: `owner_device_public_keys.fingerprint` is globally UNIQUE (a key
  belongs to one installation ever) and activation answers
  `DEVICE_ALREADY_REGISTERED` first. The staff transfer covers it.
- `activation.py` refuses the main on a replaced licence with
  `DEVICE_ALREADY_REGISTERED` while its key is still ACTIVE on the old licence:
  staff must revoke the old installation's key first. Unchanged.
- No Owner UI button for the transfer in this round (Owner UI is another person's
  area); CLI and service only.

## Not covered

- Owner compromise or a staff mistake mis-attests identities (Owner is the root of
  trust).
- The KMP unified app has no S-AUTH and its lease allowlist does not list the
  field; harmless today because it never sends it.
- The Android Keystore signing path and the Kotlin calls are proven by unit tests
  only; no physical phone was used.
