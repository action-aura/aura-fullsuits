# LAN-local operation + edition mechanics — Restaurant edition, design

**Status:** DESIGN, extends `docs/launch-readiness/restaurant-edition-plan.md`
(d20d5ff). Written 2026-08-31 against `feat/launch-readiness` (worktree
`ci-hardening-w0.3-continue`), while the v26 modifiers wave is being built
concurrently in `products/retail`. No code is changed by this document.

**Owner's decision, verbatim:** *"the restaurant can work at both online and
offline by being at the same local network as the other tablets or devices.
and make it the same product but different versions. one for retails. one for
restaurants."*

Part 1 of that confirmed the prior plan's recommendation (same product, two
editions) — settled, not re-argued here; §1 designs what "edition" means
mechanically. Part 2 — LAN-local operation with offline continuity — is new,
and is the substance of this document (§2–§8).

Every load-bearing claim below was verified against the code this session,
not carried forward from memory. The verification anchors:

| Fact | Where verified |
|---|---|
| Relay wire contract: signed body (`installation_id`, `timestamp`, `nonce`, `signature`), `since` inside the signed body, 400+`reason_code` on rejection | `commercial_runtime/sync/relay_client.py` (module docstring + `_signed_body`), `owner/app/sync/routes.py` |
| Relay auth needs Owner's Postgres: `Installation` lookup, active device key, nonce store, status gate (`SUSPENDED/DEACTIVATED/REPLACED` refused), `license_id` resolved server-side only | `owner/app/sync/routes.py::_authenticate` |
| Relay dedups pushed events on their client-generated UUID `id` (fast-path existence check + per-event SAVEPOINT) | `owner/app/sync/routes.py::_store_events` |
| Pull excludes the puller's own events: `WHERE device_id != installation.id`; per-licence seq via Postgres IDENTITY + per-licence advisory lock | `owner/app/sync/routes.py::pull`, `_lock_license_stream` |
| Client keeps exactly ONE cursor (`sync_cursor.last_seq`, single row) and ONE relay URL — one seq-space per device, structurally | `commercial_runtime/sync/sync_service.py::read_cursor`, `products/retail/backend/config.py:97` (`SYNC_RELAY_BASE_URL`) |
| Client apply is idempotent: upserts guarded `WHERE excluded.row_version > row_version` (equal ⇒ discard-as-stale no-op), conflicts recorded, quarantine for orphans | `sync_service.py::_apply_event` (~L1130, L1388), `_record_sync_conflict` |
| Outbox drains oldest-first by rowid, ack-after-successful-push only | `sync_service.py::read_outbox` / `ack_outbox` |
| Desktop offline licensing: `WARN_ONLY` never restricts by elapsed time alone | `commercial_runtime/licensing_contracts/policy_evaluator.py:166` |
| Default policy seed: check-in 24 h, retry 1 h, grace 14 d, warning from 10 d, `hard_expiry_behavior="WARN_ONLY"` | `owner/app/licensing_service/offline_policy.py::seed_default_offline_policy` |
| CA-bundle machinery for a private-LAN TLS peer exists and is honored even in frozen builds (`AURA_SYNC_RELAY_CA_BUNDLE`) | `products/retail/backend/config.py:104-115` |
| `validate_sync_relay_url`: https required, cleartext http allowed ONLY for loopback | `products/retail/backend/config.py:118-140` |
| Product backend binds `127.0.0.1` only — nothing listens on the LAN today | `products/retail/backend/app.py:850-853` |
| `required_entitlement` has zero production callers under `products/` | re-grepped this session: no hits outside tests |
| Android: Python never holds the signing key; Kotlin (`SyncRelayClient.kt`) makes every signed call via the `/_internal` seam | `commercial_runtime/sync/internal_routes.py` module docstring |
| Trust anchor bundled inside `commercial_runtime/licensing_contracts/` on every install, both products | `products/retail/backend/config.py:74-81` |
| A `nudge()` exists to push promptly out-of-tick; sync tick default 10 s | `sync_service.py::nudge`, `start(interval_seconds=10.0)` |

---

## 1. What "edition" means mechanically

The owner confirmed: one product (`AURA_RETAIL`), one binary, one licence
format, two named editions. What actually differs, layer by layer:

| Layer | Retail edition | Restaurant edition | Mechanism |
|---|---|---|---|
| Licence / plan row | existing Retail plan | a `Plan` row named "Aura Restaurant" under the same `AURA_RETAIL` product | Catalog data in Owner (`_CANONICAL_PRODUCTS` untouched). Zero product-side code. |
| Install | identical installer, identical exe | identical | Nothing differs. One build, one update channel, one test matrix — the whole point of the edition decision. |
| First-run | first-run defaults as today | first-run (or Settings) picks **edition = restaurant**: seeds restaurant demo preset when `IS_DEMO_MODE`, surfaces kitchen-ticket printer settings, takeaway/dine-in toggle, modifier-group management prominently | **A per-install setting** — one row in the existing settings storage, default `'retail'` |
| UI | POS as today | same POS; edition flag controls *visibility and defaults*, never capability | Frontend reads the setting; hidden ≠ disabled |
| Demo preset | retail sample data | menu + modifier groups + kitchen printer stub | Keyed off the edition setting + `IS_DEMO_MODE` |
| Feature gating | none | none | Deliberately none — see below |

**Entitlement wiring: not needed, and the per-install setting is the honest
answer.** Re-verified this session: `required_entitlement` has zero
production callers under `products/`. Wiring it for the edition would mean
building tier-gating for a model that sells no tiers — both editions cost
the same 250/50 JOD, and every restaurant capability follows the
invisible-unless-opted-in doctrine (a grocery that never opens the modifier
screen pays nothing for its existence). An edition flag that *gates* would
also create a new failure class: a restaurant whose flag is lost or wrong
stops being able to print kitchen tickets mid-service. A flag that only
changes defaults and visibility fails soft: worst case the UI looks like
retail until someone flips a setting.

One refinement worth taking: at activation, if the licence's plan code is the
restaurant plan, pre-set the edition setting from the activation response so
the restaurant owner never sees the choice at all. That is a nice-to-have
(needs the plan code surfaced in the activation/assertion payload — small
Owner-side addition, collaborator's area); the Settings toggle is the
mechanism of record either way.

---

## 2. The LAN requirement, restated precisely

Waiter tablets, tills, and a kitchen screen on one shop wifi must:

1. converge with each other (catalogue, config, stock, money ledger) **without
   internet**, and
2. keep converging via Owner's cloud when internet is present, without
   double-applying or forking history, and
3. keep *selling* individually even when both the internet AND the local hub
   are down (this already works — every device is a full install with its own
   SQLite and WARN_ONLY licensing).

What exists gets us surprisingly far: the sync client is transport-complete
(configurable URL, CA-bundle pinning support, signed idempotent protocol,
outbox/cursor, idempotent apply), and offline *selling* is already solved.
What does **not** exist is anything for devices to talk **to** on the LAN:
the relay lives inside Owner's Flask/Postgres app and authenticates against
Owner's installation registry, and the product backend binds loopback only.

**So the cheap version — "point `SYNC_RELAY_BASE_URL` at a locally-hosted
relay instance" — does not exist.** "A locally-hosted relay instance" today
means a full Owner Control Center on customer hardware: Postgres, the
installations and device-key tables, the licensing service — the licensing
authority sitting on a till in a café. That is a non-starter operationally
(Postgres on a POS till, a second deployment artefact, no story for how its
installation registry stays current) and a non-starter for security (Owner's
customer/licence data on customer premises). The good news that IS real: the
wire contract is two endpoints, the client needs zero changes beyond
configuration, and the hardest protocol problems (replay, idempotency,
ordering, staleness) are already solved and tested. The work is building a
small product-side implementation of the relay contract — not designing a
sync system.

---

## 3. A. Topology: the main till is the hub (embedded site relay)

**Decision: the main till's existing backend process hosts a "site relay" —
a second Flask blueprint implementing the exact `/api/sync/v1/push|pull`
wire contract against a SQLite event log. Every other device on the LAN
points its ordinary, unchanged `SyncService` at the hub. The hub's own
`SyncService` points at its own site relay via loopback. One new mover — a
"forwarder" on the hub — bridges the site log to Owner's cloud relay when
internet is available.**

```
 waiter tablet A ──┐  (https, pinned)                     (https, public CA)
 waiter tablet B ──┼──► SITE RELAY (SQLite log) ◄── forwarder ──► Owner cloud relay
 second till     ──┘        on the MAIN TILL                        (Postgres)
                                 ▲ loopback http
                          main till's own SyncService
```

### Why this and not the alternatives

* **A dedicated local relay process/box** adds a second thing that can be
  off, unplugged, or broken at 8 pm, a second thing to install and update,
  and answers no question the till-as-hub leaves open. A Jordanian café will
  not run a Raspberry Pi appliance; it will run its till. Rejected.
* **Peer-to-peer** means N² pairings, no single ordering authority (the
  entire existing design leans on one relay assigning one monotone seq per
  licence), distributed conflict topology, and partition-merge logic. That is
  genuinely new distributed-systems work replacing infrastructure that
  already works, in the least forgiving environment the product will ever
  run in. Rejected without hesitation.
* **Locally-hosted Owner relay** — rejected above (§2).

### The single-point-of-failure question, answered honestly

If the hub till is switched off mid-service:

* **Every device keeps selling.** Each is a full install: own database, own
  outbox (which simply queues), WARN_ONLY licensing. This is the existing,
  tested offline behaviour — the hub going away looks to a tablet exactly
  like the internet going away looks to a shop today.
* **What stops is convergence**: till B stops seeing tablet A's new sales /
  stock movements until the hub returns. For R1 counter-service that is a
  reporting lag, not a service stoppage — each counter rings, prints, and
  banks independently. For R2 table service it is real (shared table state
  freezes) — which is one reason tables are R2 and the LAN wave precedes
  them (§7).
* **The hub is the device the restaurant cannot run without anyway** — it is
  the main till with the cash drawer. Making the most-essential device the
  hub means "hub down" is almost always a subset of "restaurant already has
  a bigger problem".
* **Recovery: manual hub promotion, v1.** Any activated device can be
  promoted to hub (Settings → "make this the hub"): it does a one-time full
  pull from cloud (or from its own already-applied state) to seed a fresh
  site log, shows the pairing QR, and devices re-pair. Automatic election is
  deliberately rejected for v1: two hubs that each accepted writes during a
  partition is the split-brain problem, and event-UUID dedup upstream makes
  it *survivable* (nothing is lost — both hubs forward to cloud, dedup by
  id) but locally confusing (two divergent views until internet mediates).
  A restaurant with a dead till performs one deliberate, guided action; it
  does not need Raft.

### Mechanics of the site relay (hub side — all new, all product-side)

* **Storage:** three new tables in the hub's product database (versioned
  migration, per the `migration_safety.py` pattern): `site_sync_events`
  (AUTOINCREMENT `seq`, event UUID unique, entity/event/payload/created_at,
  `origin_device_id`), `site_sync_nonces` (replay store, TTL-pruned),
  `site_device_cursors` (per-paired-device acked seq, for pruning the site
  log the same way `owner/app/sync/pruning.py` prunes the cloud log).
* **Ordering:** SQLite is single-writer and the site relay runs inside one
  process — the Postgres advisory-lock dance (`_lock_license_stream`)
  collapses to the process's existing lock discipline plus SQLite's writer
  serialization. The seq-visibility race routes.py documents cannot occur
  with one writer connection; say so in the code comment rather than
  cargo-culting the lock.
* **Verification logic:** a straight port of `_authenticate`'s
  verify-then-resolve order (shape → timestamp freshness → burn nonce →
  key lookup → Ed25519 verify → status gate) with the key/status source
  being the **site roster** (§5), not Postgres.
* **Listener:** the hub backend additionally binds a LAN-facing TLS listener
  (separate port; the loopback UI listener stays exactly as it is). This is
  a deliberate, installer-visible change: Windows Firewall inbound rule,
  shipped by the packaging, documented in the runbook. Do not silently
  rebind the existing `127.0.0.1` server — the UI surface stays loopback.

### Client side (near-zero change)

A device joins the LAN by configuration only: `SYNC_RELAY_BASE_URL` →
`https://<hub>:<port>`, `SYNC_RELAY_CA_BUNDLE`-equivalent pin from pairing
(§4). `SyncService`, outbox, cursor, apply — untouched. The hub's own
`SyncService` uses `http://127.0.0.1:<site-port>` — the one cleartext case
`validate_sync_relay_url` already correctly allows.

For table-service latency later (R2): push already has `nudge()`; the site
relay adds a cheap long-poll or shortened tick for LAN peers so a fired
order reaches the kitchen in ~1 s rather than the 10 s default tick. Not
needed for R1.

---

## 4. B. Discovery and pairing: QR to trust, beacon to find

**Decision: a one-time QR pairing step establishes *trust*; a signed UDP
broadcast beacon maintains *addressing*. mDNS is rejected as the primary
mechanism. Manual IP entry survives as the last-resort fallback.**

* **Pairing (once per device):** the hub's Settings shows a QR encoding
  `{site relay URL, hub TLS public-key (SPKI) pin, hub installation_id, a
  short-lived pairing code}`. The tablet scans it (camera exists on every
  tablet; on a desktop second till, a copy-paste string equivalent). The
  device stores the pin + URL and starts syncing. A non-technical owner's
  entire setup: *"On the till press Connect a device. Scan the code with the
  tablet."* No IP addresses are ever typed or seen.
* **Address changes (router reboot, new DHCP lease):** the hub broadcasts a
  small UDP beacon every few seconds on the local subnet:
  `{installation_id, current URL, SPKI pin, hub wall-clock time, Ed25519
  signature by the hub's device key}`. Paired devices verify the signature
  against the hub key they learned at pairing (or from the roster) and
  update the stored URL. Identity lives in keys, not addresses — a new IP is
  a non-event. The beacon carries no secrets; an attacker replaying it can
  only redirect devices to a host that must then present the pinned key,
  which it cannot.
* **Why not mDNS/Bonjour:** it is the standards-shaped answer and the
  worse-support answer here. Multicast is filtered or broken on a large
  fraction of consumer/ISP routers; Windows' mDNS resolution is
  version-dependent; Android NSD is its own bug tracker; and wifi **client
  isolation** (default on many guest SSIDs) silently breaks it with no
  diagnosable symptom. A self-emitted broadcast beacon has one moving part
  we own end-to-end and fails identically to mDNS where multicast/broadcast
  is blocked — at which point the fallback is the same for both: last-known
  address, then manual entry.
* **Deployment prerequisite to document loudly:** all POS devices on one
  SSID/subnet with client isolation OFF — practically, "the POS gets its own
  wifi network, not the customer wifi". Restaurants already accept this for
  receipt printers. The pairing screen should detect and say "cannot reach
  the hub" with a checklist, because this will be the #1 support call.
* **Clock skew — a real LAN-offline failure mode:** the signed protocol
  validates timestamp freshness. A tablet that has been offline for weeks
  has no NTP and drifts; the hub would start rejecting its requests as
  stale. The site relay therefore uses a **generous skew window**
  (minutes, not seconds — the LAN threat model tolerates it; the nonce
  store still kills replay), and the beacon's hub-time field lets devices
  display a "device clock is N minutes off the till" warning. Without this
  paragraph the design fails in month 2 in exactly the environment it was
  built for.

---

## 5. D. Security: same protocol, new verifier, Owner-signed roster

The requirement: a laptop joining the café wifi must not be able to read a
day's takings from the hub, and a device Owner has suspended must not sync
forever just because it is local. Layered:

**Layer 1 — transport.** LAN traffic is HTTPS only. The hub generates a
long-lived self-signed keypair at promotion; clients pin the **public key
(SPKI)**, delivered via the pairing QR — never a "trust any cert" mode, and
never cleartext (`validate_sync_relay_url` already refuses non-loopback
http; that rule stands unmodified). Pinning the key rather than a hostname
sidesteps the IP-SAN-changes-on-reboot trap entirely: the pin is the
identity. Mechanics: OkHttp's `CertificatePinner` does this natively on
Android; desktop needs a small custom `requests` HTTPAdapter (~50 lines)
that verifies the presented leaf's SPKI against the pin instead of hostname
matching. This is stronger, not weaker, than public-CA verification for a
peer whose identity we learned out-of-band. Why TLS at all when bodies are
signed: **pull responses are unsigned** — over cleartext, anyone on the wifi
could MITM a pull and inject fabricated events into a device. Signed
requests alone do not protect the download direction.

**Layer 2 — authentication.** Identical to the cloud relay: every push/pull
body carries `installation_id`, fresh `timestamp`, fresh `nonce`, Ed25519
`signature` over the canonical body. The client already does this — zero
client change. The hub verifies with the same verify-then-resolve order as
`_authenticate`, against its own nonce store.

**Layer 3 — authorization: the Owner-signed site roster.** The hub does not
have Owner's installation registry, so Owner issues one, scoped and signed:
a **roster** listing, for this licence, every installation's id, device
public key, platform, and status — signed by Owner's signing key in the
exact envelope pattern the licence assertion already uses, verified against
the **trust anchor already bundled in every install**. The hub fetches the
roster during any online period (piggybacked on check-in cadence), caches
it, and enforces:

* an installation not on the roster → refused (the café laptop, an
  unactivated clone, a device from another licence);
* roster status `SUSPENDED/DEACTIVATED/REPLACED` → refused, mirroring the
  cloud relay's post-signature status gate.

This is Owner-side work (collaborator's area — a handover): one new signed
endpoint reusing the assertion-signing machinery, plus roster regeneration
on installation-status transitions.

**Staleness, stated rather than hand-waved:** a device suspended in Owner
keeps LAN access until the hub next reaches Owner and refreshes the roster.
That bound is the check-in cadence when internet exists and unbounded when
it does not — which is exactly the trust model the offline licensing design
already accepted for the licence itself (WARN_ONLY). Local mitigation for
the local threat: the hub's own Settings can revoke a paired device
immediately (drop it from the accepted set ahead of the roster), because the
realistic urgent case — a fired employee's tablet — is standing in the
restaurant, not in Owner's console.

**What this deliberately does not add:** per-event provenance signatures.
The hub forwards LAN events upstream under its own installation signature,
so cloud-side `device_id` attribution coarsens to "the hub" for forwarded
events. Threat delta versus today: none that matters — any activated device
can already push arbitrary events into its licence's stream, and the hub is
the customer's own most-trusted device. Original-device attribution
*within* the shop survives in the payloads themselves (sales carry user/
terminal attribution). If per-event provenance is ever wanted, the event
envelope can grow an optional origin signature — out of scope here.

**Residual risks, named:** (1) hub compromise = licence-stream compromise —
equal to today's single-till compromise, but say it; (2) roster refresh
requires internet at least once after any suspension to take effect locally;
(3) UDP beacon reveals that an Aura hub exists on the LAN (metadata only);
(4) rate limiting on the site relay inherits the cloud relay's "documented
deferred gap" status.

---

## 6. C. When the internet returns: store-and-forward, one relay per device

**The invariant that makes this safe: every device talks to exactly ONE
relay, ever.** The client structurally enforces this already — one
`SYNC_RELAY_BASE_URL`, one `sync_cursor.last_seq`, one seq-space. LAN
devices sync against the hub's seq-space; only the hub holds a cursor
against the cloud's seq-space. No device ever sees two orderings, so the
"two paths conflict" problem is dissolved rather than solved.

**Upstream (LAN → cloud):** the hub's forwarder reads site-log events whose
`origin_device_id` is a LAN device or the hub itself (never upstream-origin
rows), batches them oldest-first, and pushes to the cloud relay under the
hub's installation signature **preserving the original event UUIDs**. The
cloud relay is idempotent on event id (`_store_events`: existence check +
SAVEPOINT), so retries, crashes mid-batch, and even a device that once
pushed directly to cloud before joining the LAN all collapse to no-ops —
verified behaviour, not hoped-for. The forwarder keeps its own
"forwarded-up-to" watermark and only advances it on confirmed push, exactly
mirroring `push_once`'s ack-after-success discipline.

**Downstream (cloud → LAN):** the hub pulls from the cloud as an ordinary
device. The cloud's pull already excludes `device_id == hub`, so **nothing
the hub forwarded up ever comes back down** — no echo, no loop, by existing
server behaviour rather than new filtering. Pulled events are (a) applied to
the hub's own database via the unchanged `apply_pull_result`, and (b)
inserted into the site log with `origin_device_id = upstream`, where LAN
devices receive them on their next pull. A LAN device never re-receives its
own events either: the site relay's pull excludes its `origin_device_id`,
and its events never return from the cloud (they were pushed by the hub, and
the hub's pull excludes them).

**Belt and braces:** even if an event somehow arrived twice (hub rebuild,
roster re-pair, promoted-hub overlap), apply is idempotent — creates are
uid-keyed upserts, updates are guarded `WHERE excluded.row_version >
row_version`, so an equal-version replay is a recorded no-op. Conflicts
remain what they are today: row_version reject-stale with conflict logging.
LAN changes the *window* (seconds instead of internet-restore lag), not the
model.

**Where the existing design genuinely bends (not breaks):**

1. **Cloud-side attribution coarsens** for forwarded events (§5, accepted).
2. **`SyncDeviceCursor` on the cloud** now advances only for the hub;
   LAN-device rows go stale. `pruning.py`'s watermark is MIN over active
   devices' cursors — stale LAN-device cursors would **freeze cloud pruning
   for that licence forever**. The Owner-side fix must ship with the roster
   work: devices marked LAN-attached (roster knows) are excluded from the
   pruning MIN, or the hub's cursor stands in for its flock. This is the
   one place LAN operation reaches into existing Owner-side logic; it is
   small but it is not optional.
3. **Bootstrap history:** a hub's site log starts empty. At promotion the
   hub seeds it with a full pull from cloud (seq 0 → head), so a
   freshly-activated tablet can catch up entirely over the LAN afterwards.
   A fresh device still needs internet once anyway (activation is
   online-only), so the ordering "activate online → pair on LAN" is already
   the natural flow. Whatever history cloud pruning has already discarded is
   equally unavailable on both paths — LAN makes this no worse.

---

## 7. E. Licensing, the device meter, and the waiter-tablet question

**Decision: a waiter tablet is a FULL INSTALL — its own database, its own
activation, its own 50 JOD device slot. There is no thin-client tier. The
kitchen "screen" in R1 is a printed ticket (a printer, no slot); a
kitchen-display device, when it ships, is a full install consuming a slot.**

Why full install, argued from the code and the 8 pm test:

* **The thin client does not exist and is not nearly-free.** The backend
  binds `127.0.0.1` only (app.py:850-853); a browser-tablet client means
  LAN-exposing the Flask session/UI surface, a browser TLS story, session
  hardening for hostile-wifi conditions, and a new support class — all of it
  *new* work, not configuration.
* **A thin client fails exactly when the owner is buying resilience.** A
  browser tab against the till dies with every wifi blip and holds no local
  queue; a full install rides through blips on its outbox and keeps taking
  orders when both hub and internet are gone. The whole LAN requirement is
  "keep serving at 8 pm" — the thin client is the one architecture that
  cannot promise that.
* **The meter is enforceable only for installs.** Activation, the Ed25519
  device binding, and `device_limit` meter *activated installations*. A
  browser tab has no activation to meter — a no-slot thin client is
  unenforceable as revenue and quietly re-opens the "why buy slots?" hole.
* **Revenue vs support, quantified:** a restaurant at till + 2 handhelds +
  kitchen device = 250 + 3×50 = **400 JOD** versus 250 for a corner shop —
  the 1.5–2× the prior plan promised, with zero new licensing machinery.
  The cost is support surface: each install is a database, an activation,
  an update target. That cost is mostly already paid — sync, updates, and
  activation are built and automated — whereas the thin client's support
  cost (browser sessions on hostile wifi) would be net-new. Full installs
  win on both axes; this is not a close call.
* Kitchen display: R1 ships printed tickets (already scoped, no licensing
  question). A KDS device later = full install + slot, consistent with the
  prior plan's §5.2 ruling and with enforceability. Do not build a
  device-class discount; price a screen as a slot.

**How long may a LAN-only restaurant run with no internet at all?**
**Indefinitely, with warnings — by verified current behaviour.** The desktop
evaluator (`policy_evaluator.py:166`) never restricts on elapsed time under
`WARN_ONLY`, and the default Owner policy seed is WARN_ONLY with warning
from day 10 and grace 14 d (`offline_policy.py`) — past grace the state
parks in GRACE_PERIOD and keeps warning, never blocking. The genuinely
internet-requiring moments are: **activation (once per device), roster
refresh (for suspension to reach the LAN), and any licence admin**. Two
consequences to put in front of the owner: (1) leave WARN_ONLY as the
default for restaurant sites — a policy flip to
RESTRICT_COMMERCIAL_FEATURES would turn "router died in week 3" into "the
restaurant stopped taking orders", indefensible for a lifetime-priced
product; (2) LAN mode makes near-permanently-offline sites *more* likely,
which sharpens the prior plan's §5.4 Care/relay-cost question — a site that
never touches the cloud relay costs nothing to run but also never
check-ins; the commercial answer (Care line) should be decided before the
restaurant edition is marketed hard, as already flagged.

---

## 8. F. What this changes about R1 versus R2

**R1 stands as scoped. The LAN wave becomes its own release ("R-LAN")
between R1 and R2, and it is a hard prerequisite of R2.** Honest reading of
the owner's words, though: *"waiter tablets … work together with the
kitchen screen"* is the table-service picture. If by "waiter tablet" he
means **taking table orders that flow to a kitchen**, that is precisely the
synced open-order lifecycle — R2 — and no amount of LAN plumbing changes
that: LAN moves the *bytes*; the order entity that accumulates lines over
rounds and settles late still does not exist (§3/#8 and §4.4 of the prior
plan). What LAN work does do is remove R2's biggest unknown in advance —
shared state converging in seconds on shop wifi, internet or not — so R2
becomes a schema-and-lifecycle wave on proven transport instead of a
distributed-systems gamble.

So the sequence, amended from the prior plan's §6:

1. Go-live work still owed → 2. v26 modifiers (in flight) → 3. kitchen
tickets → 4. split tender + tips → 5. modifier config sync M2 → **R1
sellable (counter-service, cloud-synced, offline-selling as today)** →
**6. R-LAN (this document)** → 7. R2 tables/open orders (now committed, not
"maybe after pilots", *if* the owner confirms the waiter-tablet picture) →
8. transfers (v24) slots after R-LAN rather than before, unless a chain deal
forces it earlier.

R1's counter-service value on LAN day one, stated plainly: multi-till cafés
already converge via the cloud relay when the internet is up; R-LAN's
incremental gift to R1 is **offline convergence and independence from the
droplet mid-service** — real, but incremental. R-LAN's gift to R2 is
**existence** — table service on shop wifi is not shippable without it.

---

## 9. G. Effort — relative to the modifiers wave (2–2.5 units)

Genuinely new versus configuration-of-what-exists, called honestly:

| Piece | New or config? | Size (variants-wave units) |
|---|---|---|
| Site relay blueprint: SQLite event log + nonce store + device cursors (versioned migration), port of `_authenticate` verify order, push/pull handlers, LAN TLS listener + firewall rule in packaging | **New** (logic heavily cribbed from `owner/app/sync/routes.py`, rewritten for raw sqlite3 — no SQLAlchemy here) | 0.75–1 |
| Owner-signed site roster: Owner endpoint (assertion-envelope pattern) + hub fetch/cache/verify against bundled trust anchor + status enforcement + the pruning-watermark exclusion (§6 item 2) | **New**; ~half is Owner-side → collaborator handover | 0.5–0.75 |
| Hub forwarder (site log ↔ cloud, watermark, echo-free by existing server semantics) + hub promotion & site-log seeding flow | **New** | 0.5–0.75 |
| Pairing UX (QR render/scan), hub keypair + SPKI pinning transports (custom requests adapter on desktop, `CertificatePinner` on Android/KMP), UDP beacon + clock-skew handling | **New** | 0.75–1 |
| Client transport & config: relay URL per device, pin storage, loopback hub self-sync | **Configuration of what exists** (`SYNC_RELAY_BASE_URL`, CA/verify machinery, `validate_sync_relay_url`) | ~0.1 |
| Offline selling, outbox/cursor, idempotent apply, conflict handling, WARN_ONLY licensing | **Already built and verified** — the reason this design is possible at all | 0 |
| Adversarial verification wave: hub-off mid-service, promotion during partition, replay across both relays, roster staleness, skewed clocks, isolation-enabled wifi | **New** (and non-negotiable — this is the least forgiving environment the product will run in) | 0.5 |

**Total ≈ 3–4 waves — larger than the modifiers wave, comparable to R2
itself.** The loud version of the honest answer: this is **not** "point the
existing relay at a local host and most of it is configuration" — the relay
cannot be detached from Owner's Postgres, so the hub side is genuinely new
distributed-systems-adjacent work. But it is *bounded* new work: the wire
contract, the client, the idempotency and ordering guarantees, the trust
anchor, and the offline licensing are all reused as-is, which is roughly
half the system pre-paid. The failure modes that remain (hub death, split
promotion, clock drift, wifi isolation) are named above and each has a
designed answer or an explicit v1 exclusion — none is hand-waved.

---

## 10. Open questions — only the owner can answer

1. **What does a waiter tablet do?** Take table orders that flow to the
   kitchen (→ R2 is the real target and gets committed after R-LAN), or act
   as extra counter tills (→ R1 + R-LAN suffices)? This single answer sets
   the roadmap's back half.
2. **Confirm the meter:** every tablet, till, and (future) kitchen-display
   device is a full install at 50 JOD per extra slot; no browser thin
   clients are offered. (Recommended yes — §7.)
3. **Suspension latency:** acceptable that a device suspended in Owner keeps
   LAN access until the site next reaches the internet, with the hub-side
   local revoke as the immediate remedy? (Recommended yes — it matches the
   already-accepted offline trust model.)
4. **The Care/relay question** (prior plan §5.4) gains urgency: LAN sites
   may legitimately never touch the cloud. Decide the year-3 support/relay
   funding answer before marketing the restaurant edition hard.
5. **Hub promotion is manual in v1** (a guided Settings action, no automatic
   failover). Acceptable? (Recommended yes; automatic election is the single
   most expensive thing this design declines to build.)
6. **Deployment doctrine:** is he prepared to require a dedicated POS
   wifi/SSID (client isolation off) as an installation prerequisite, with
   the support script built around it? Guest-network isolation is the #1
   predicted failure in the field.

---

## 11. What this document deliberately does not do

No schema version is claimed (v26 is in flight; R-LAN's migrations claim
their numbers in ROADMAP.md at dispatch, per the single-writer rule). No
Owner-side code is ordered — the roster endpoint, roster-aware pruning, and
the restaurant `Plan` row are handovers to the collaborator's area. Nothing
here re-argues the edition decision. And per this repo's documented failure
mode: re-run the verification anchors in §0 before building against this —
starting with whether the modifiers wave changed `sync_service.py`'s
handled-entity set, and whether the relay contract grew since
`owner/app/sync/routes.py` was read on 2026-08-31.
