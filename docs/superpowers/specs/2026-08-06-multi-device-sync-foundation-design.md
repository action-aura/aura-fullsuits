# Multi-Device Sync — Foundation + Category Sync

## Problem

Today, every Aura Retail installation (Windows desktop, Android/iOS via
`mobile/aura-retail-unified`) holds its own completely isolated local
SQLite database. Two devices activated against the *same commercial
license* share nothing — no products, customers, categories, sales,
inventory. Confirmed by direct investigation: no cloud database,
replication, message queue, or business-data sync endpoint exists anywhere
in the repo. The only cross-device mechanism that exists today is
*license-state* sync (`commercial_runtime/licensing_contracts/`) — a
structurally separate system, hard-guarded so it can never carry business
data (an explicit forbidden-field allowlist rejects payloads containing
`stock_quantity`, `sale_total`, etc.). This gap is explicitly documented as
out of scope in the current mobile initiative
(`docs/retail/unified_mobile/complete-retail-capability-matrix.md`).

## Goal

Prove a real, production-shaped sync mechanism end to end — offline writes
always succeed locally, changes push to a central relay the moment
connectivity returns (not on a delay), other devices on the same license
pull and apply those changes automatically. Prove it with **Category**
(existing, working UI on both desktop and mobile) before touching anything
else. Products, Customers, Sales, Inventory, etc. are explicitly follow-up
sub-projects — not this one.

## Decisions made during brainstorming (binding for this sub-project)

- **Relay lives inside the Owner platform**, not a new standalone service.
  Owner already models license/device relationships
  (`owner/app/commercial_ops/device_slot_ops.py`) — that's the natural
  scoping key for "which devices share data." Owner's real cloud
  deployment is **explicitly out of scope here** — for this sub-project the
  relay runs locally on the developer's desktop machine, reached by the
  desktop Retail client directly (`127.0.0.1`) and by the mobile app over
  USB via `adb reverse` (WiFi on the test laptop is unreliable, so the
  cable is the only transport being validated right now). The relay code
  itself must be written as if it will run in production unchanged —
  moving it to a real host later is meant to be an ops step, not a rewrite.
- **Sync model: append-only event log**, not row-overwrite/last-write-wins.
  Every create/update/delete is a discrete, ordered event. Chosen
  specifically because later sub-projects (Sales, Inventory) need to
  *detect* conflicts like oversell, which a plain "newest row wins" model
  cannot do. Category doesn't strictly need this power, but the log format
  is being built once, correctly, rather than rebuilt when money tables
  arrive.
- **IDs: UUIDs generated on-device at creation time**, replacing
  autoincrement integers for any table sync touches. Two offline devices
  creating "category #12" independently must never collide.
- **Sync trigger: immediate push + short-interval pull**, not a persistent
  websocket/SSE channel. A local outbox queues events made while offline;
  the moment the device is online, the outbox drains immediately (no
  waiting for a timer) — this is what makes "once online, sync starts
  directly" literal. Pulling other devices' changes runs on a short poll
  (~10s) while online. A persistent push channel is deferred — more
  infrastructure to get right, and there's no deployed cloud yet to
  meaningfully test reconnect/heartbeat behavior against.
- **First entity: Category**, not Products. Investigated first:
  `mobile/aura-retail-unified` has a real Products repository/backend
  (`SqlDelightProductRepository.kt`) but **no working UI** — every Product
  route is stubbed `unavailable("Add product", "...post-M6")`
  (`ui/navigation/AuraNavHost.kt:32-39`). Category, by contrast, has a real
  working screen on mobile today (`ui/category/CategoryListScreen.kt`,
  `CategoryEditScreen.kt`). Using Category keeps this sub-project scoped to
  exactly one new capability (the sync engine) instead of bundling a net-new
  mobile UI feature into it. Products sync becomes a fast follow-up once
  the mechanism is proven — same event log, same client plumbing, no new
  engineering pattern.

## Architecture

### Relay (`owner/app/sync/`, new)

- New table `sync_events` (Alembic migration): `id` (UUID, **client-generated,
  unique constraint** — this is the dedup key, see below), `seq`
  (server-assigned autoincrementing bigint — the pull cursor), `license_id`
  (FK), `device_id`, `entity_type` (`'category'` for this sub-project),
  `entity_id` (UUID), `event_type` (`create` | `update` | `delete`),
  `payload` (JSON, full row snapshot — never a partial diff), `created_at`
  (client-supplied, display/audit only — **never** used for ordering or
  conflict resolution; `seq` is the only ordering authority), `received_at`
  (server-stamped, authoritative). Composite index on `(license_id, seq)` —
  the pull query's hot path.
- `POST /api/sync/push` — device submits a batch of its own new local
  events. Authenticated via the device's existing license-activation
  session (no new auth system) — `license_id` is **always derived from the
  authenticated session, never accepted from the request body**; a device
  cannot claim to belong to a license it isn't actually activated against.
  **Idempotent by the event's own client-generated `id`**: if the same
  event `id` arrives twice (client retried after a dropped response, never
  actually knowing whether the first attempt landed), the second insert is
  a no-op keyed on the unique constraint, not a duplicate row with a new
  `seq`. This is required correctness, not an optimization — without it, a
  single network blip on the open internet duplicates real events.
  Server assigns each **new** event the next `seq` value and stores it.
- `GET /api/sync/pull?since=<seq>` — returns events with `seq > since` for
  the *authenticated session's* `license_id` (never a client-supplied
  value), **excluding events authored by the requesting device itself**
  (avoids a pointless self-echo round trip). Response includes the highest
  `seq` returned, which becomes the client's new cursor.
- Both routes reject with 401/403 if the device's license session is
  invalid/expired/deactivated — sync must fail closed, not silently no-op.

### Clients (desktop Retail `products/retail/`, mobile
`mobile/aura-retail-unified/`)

- New local table `sync_outbox`: queued events not yet confirmed received
  by the relay. New local table (or single-row state) `sync_cursor`: last
  `seq` this device has pulled.
- Category's existing create/update/delete code paths (both platforms) get
  one new step: after the local write succeeds, append a corresponding
  event to `sync_outbox`. The local write and the outbox write happen in
  the same local transaction — an event is queued if and only if the local
  write actually committed.
- **Push loop**: attempt to drain `sync_outbox` to the relay whenever
  triggered (immediately after any local write, and on a reconnect
  signal). Success removes the event from the outbox. Failure leaves it
  queued, retried with backoff — "online" is defined operationally as "the
  next push attempt succeeds," not by a separate network-state listener.
- **Pull loop**: every ~10s while a push attempt has recently succeeded
  (i.e., while presumed online), `GET /api/sync/pull?since=<cursor>` and
  apply each returned event to the local `categories` table (upsert on
  create/update, hard delete on delete), then advance the cursor.
  Idempotent by `entity_id` + event `seq` — replaying an already-applied
  event is a safe no-op, so a crash between "apply" and "advance cursor"
  can never double-apply or lose an event.
- **Category `id` migration**: existing local rows (both platforms) get a
  UUID assigned on first upgrade, replacing the autoincrement integer PK.
  This must run once, safely, against real existing local data on both
  platforms — not just fresh installs.

### Conflict handling (Category specifically — a deliberately simple rule
for a low-stakes table)

Two devices edit the same category while both offline, both reconnect:
whichever event has the higher relay-assigned `seq` wins, full-row-replace
(never a field-level diff). If a delete event is followed by a later edit
event for the same `entity_id`, the edit implicitly recreates the category
— safe specifically because every event carries the complete row, so a
"resurrected" row is never half-populated. This rule is intentionally
simple; it does not need to generalize to Sales/Inventory, which get their
own conflict model in a later sub-project.

## Data flow (end to end)

1. User edits a category on Device A. Local write commits immediately —
   never blocked on network, works fully offline.
2. Same transaction queues the event in Device A's `sync_outbox`.
3. If Device A is online, the push loop drains the outbox to the relay
   right away. If offline, the event waits; the instant a push attempt
   next succeeds, the whole outbox drains — no polling delay on this side.
4. The relay assigns the event a `seq` and stores it, scoped to the
   license.
5. Device B's pull loop (polling every ~10s while online) fetches events
   with `seq` greater than its cursor, applies them, advances its cursor.
6. Device B now reflects Device A's change, typically within one poll
   interval of Device B being online — regardless of how long Device A was
   offline before it reconnected.

## Error handling

- Push/pull network failure: retried with backoff; nothing is ever
  dropped, since the outbox only clears on confirmed relay receipt and the
  cursor only advances after successful local apply.
- Duplicate/replayed event: idempotent by `(entity_id, seq)` — a no-op.
- Invalid/expired license session: sync endpoints reject explicitly
  (401/403); the client surfaces "sync paused — check license" rather than
  retrying forever against a request that can never succeed.
- Malformed event payload: reject at the relay with a 400 and log it;
  never silently drop or silently accept a corrupt row into the event log
  (the log is the source of truth other devices replay from).

## Cloud-deployment readiness requirements (binding, not optional)

Local-cable testing runs over a trusted, single-operator channel (loopback,
or USB you physically control). The real deployment target runs over the
open internet, with untrusted clients and real customer data. These
requirements exist so that transition is an ops step (point the client at
a real URL) — never a rewrite of sync logic:

- **No hardcoded loopback anywhere in application logic.** The relay base
  URL is a client-side config value (env var / settings file) on both
  desktop and mobile, defaulting to `http://127.0.0.1:<port>` only in the
  local dev configuration. Production config uses `https://`. The sync
  code itself must never branch on "am I local or cloud" — it only ever
  talks to "the configured relay URL."
- **TLS is mandatory in production.** The relay's production deployment
  terminates HTTPS (this sub-project doesn't set up the real cert/host —
  that's the deferred deployment task — but the client and relay code must
  not assume or require plaintext HTTP to function).
- **Auth reuses licensing's existing internet-grade session, not a
  local-only shortcut.** `licensing_contracts` already authenticates real
  devices over real networks (Ed25519-signed assertions, replay
  protection) — sync's device authentication must be the same mechanism,
  not a simplified stand-in that happens to work over a trusted cable.
  If anything about the reused session assumes a trusted/local caller,
  that's a finding to fix here, not to carry into the plan.
- **License scoping is a server-side security boundary, not a
  convenience filter.** Every push and pull derives `license_id` from the
  authenticated session exclusively (already reflected above) — this is
  the one thing standing between two different real customers' data once
  this is internet-facing. No code path may accept `license_id` as
  client-supplied input, not even for a "trusted" internal call.
- **Push is idempotent** (already reflected above) — required because
  at-least-once delivery over a real, unreliable internet connection is
  the normal case, not an edge case.
- **Ordering is server-assigned only** (`seq`, already reflected above) —
  client clocks are never trusted for anything but display.
- **Payload/batch size bounds.** The push endpoint must reject
  unreasonably large batches/payloads outright (exact limits are an
  implementation detail for the plan, not this spec) rather than being
  designed in a way that assumes a cooperative, unbounded caller — basic
  abuse resistance, not full production hardening (rate limiting itself
  can be a deployment-time concern, but the endpoint contract must not
  preclude adding it later).

## Local testing (cable-only, no cloud)

1. Run the Owner sync module locally on the desktop
   (`python owner/app.py` or equivalent dev entrypoint).
2. Desktop Retail's sync client points at `http://127.0.0.1:<port>`
   directly (same machine).
3. Enable USB debugging on the phone, connect via cable, authorize the
   desktop's RSA fingerprint, confirm with `adb devices`.
4. `adb reverse tcp:<port> tcp:<port>` — tunnels the phone's
   `127.0.0.1:<port>` to the desktop's `127.0.0.1:<port>` over USB. Mobile
   app's sync client points at the same `127.0.0.1:<port>` — identical URL
   scheme it will use against a real deployed server later.
5. Test matrix: create/edit/delete a category on desktop → confirm it
   appears on mobile within one poll interval. Same in reverse. Unplug the
   cable (simulates offline), make changes on mobile, reconnect, confirm
   they push immediately rather than waiting for a timer.

## Explicitly out of scope

- Real cloud deployment of Owner (separate future task; this sub-project's
  relay only ever runs locally).
- Any entity other than Category (Products, Customers, Suppliers, Sales,
  Inventory are follow-up sub-projects, each with their own conflict
  model where it matters).
- Persistent push channel (websocket/SSE) — deferred.
- Building mobile's missing Products UI — explicitly not bundled into this
  sub-project (was raised and deliberately rejected during brainstorming).
- Any change to `commercial_runtime/licensing_contracts/` itself — this
  sub-project only *reuses* its existing device/license auth session, does
  not modify it.
