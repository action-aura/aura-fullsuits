# Per-Device Login / "One Admin Device" — Phase 0 Decision Spec

**Status:** Phase 0 decisions locked for this week's Phase 1 (backend schema
only). Phase 0-b/0-c deferred, need sync-stream owner sign-off before being
picked up. Stream is deliberately isolated: `commercial_runtime/identity/`
has zero diff against `master` today and no other in-flight stream touches
it.

## Problem

The demo requirement, as relayed, is "each device needs its own login
credentials." That sentence has two structurally different readings, and
this spec exists because picking the wrong one silently locks in the wrong
architecture for the rest of the week.

## a) Requirement disambiguation — Reading A vs. Reading B

**Reading A (literal): separate credential stores per device.** Each device
runs its own Flask process against its own `registry.db`, and each device's
operator provisions its own login independently. This reading is not a
feature to build — **it is the current status quo, and it is already a bug**:

- `create_admin` (`commercial_runtime/identity/onboarding_routes.py:137`)
  derives the tenant id as `company_id = cfg.get('company_id') or
  hashlib.md5(admin_email.encode()).hexdigest()` — a pure function of the
  email string typed on *that* device, with no cross-device coordination.
- If a second physical device runs its own `create_admin` (e.g. a shop's
  second till, or a manager's laptop), it computes its own `company_id` from
  whatever email is typed there. Same business, same license — two
  disconnected `company_id` values, because nothing about "device B" was
  ever communicated to device A or to any shared authority.
- The practical symptom: sync (`commercial_runtime/sync/`) partitions by
  `company_id`. Two devices with two different `company_id`s never see each
  other's data no matter how correctly the sync outbox itself is wired —
  the tenant has fragmented before sync even runs.
- There is also no admin-side visibility or revocation: an admin on device A
  cannot see, let alone revoke, an account that only ever existed in device
  B's local `registry.db`, because there is no shared account list to view.

**Reading B (recommended, and what this stream implements): company-scoped
accounts, device-scoped authorization.** One account list per tenant
(`company_id`), already the reality once `company_id` is derived
consistently (see decision (b) below). Each physical device is registered
with its own stable local identity. A login is authorized per **(user,
device)** pair — an account existing does not by itself mean it can log in
from every device; it must be explicitly granted access to that device by
whoever administers the tenant. This is what `devices` +
`user_devices` (Step 2 below) exist to represent.

**If the actual requirement was Reading A, this entire design should be
reconsidered** — Reading A is not an authorization model, it is the absence
of one, and hardening it further (e.g. "make each device's separate
credential store more secure") would be polishing a bug rather than fixing
it. This spec proceeds on the assumption that Reading B is what was meant,
because it is the only reading that produces a coherent one-tenant,
multi-device product. Flag explicitly to product/demo owner if that
assumption is wrong before Tuesday.

## b) `company_id` authority — decision: option 0-a for this week

Three options were considered for how a device knows which `company_id` it
belongs to:

- **0-a (adopted this week):** Don't change how `company_id` is derived
  today (still the `create_admin`-time value, `md5(email)` or whatever was
  in `config.json`). Instead, treat the *device row* as the thing that
  detects drift: `resolve_local_device()` (Tuesday's `device_context.py`,
  out of scope today) will read the local device's row from `devices`
  keyed by its stable local id, compare its stored `company_id` against the
  registry's current `company_id`, and **refuse to silently re-bind** a
  device whose registry `company_id` has changed since it was first seen —
  it raises an explicit error rather than quietly creating a second device
  row under the new `company_id`. This does not fix Reading A's root cause;
  it makes the symptom loud and diagnosable instead of silent, which is the
  right size of fix for a schema-only week.
- **0-b (deferred — needs sync-stream owner sign-off):** Derive `company_id`
  from the license/installation id once Owner licensing is actually wired
  up for a given install (`OWNER_LICENSING_BASE_URL` set,
  `commercial_runtime/licensing_contracts/` active). This is the real fix
  for Reading A's root cause — it makes `company_id` externally authoritative
  instead of a local hash — but it depends on licensing enforcement being
  on, which per `CLAUDE.md` is explicitly not the default local-dev/testing
  state, and it touches `licensing_contracts/`, which is out of scope for
  this stream this week by explicit instruction.
- **0-c (deferred — needs sync-stream owner sign-off):** A "join existing
  installation" onboarding path for device 2+ that, instead of running
  `create_admin` again, adopts the license's existing `company_id` (a new
  onboarding route/flow, not a schema change). This composes with 0-b — 0-c
  is the UX, 0-b is the authority it reads from — and both are real
  follow-on work, not this week's problem.

0-a was chosen because it requires zero changes to `mt_auth.py`,
`auth_routes.py`, `onboarding_routes.py`, or `licensing_contracts/**` — all
explicitly off-limits this week — and it converts an already-existing silent
failure mode into an explicit, catchable one, which is a strict improvement
even before 0-b/0-c land.

## c) Limitation to state plainly: this is per-registry.db, not cross-device

The "one Admin Device per company" invariant this Phase 1 enforces
(`idx_devices_one_admin`, Step 2) is enforced **inside a single
`registry.db`** — i.e., per physical device. There is, this week, **no
cross-device propagation of device state at all**:

- The sync outbox (`commercial_runtime/sync/`, `sync_outbox`/`sync_cursor`)
  only ever carries `category`/`product`/`customer`/`supplier` events today
  (confirmed against the sync stream's current scope in
  `docs/superpowers/specs/2026-08-07-retail-catalog-party-sync-expansion-design.md`).
  It does not carry device rows, admin-device flag changes, or grants.
- Consequently, two devices on the same `company_id` could each
  independently mark themselves `is_admin_device=1` in their own local
  `registry.db` — each database enforces "at most one admin device"
  *locally*, but nothing today prevents two locally-consistent, mutually
  contradictory truths from existing across two machines.

This is **acceptable for Phase 1**: the operator configures which physical
device is the admin device on that device, for that device, and the demo
narrative only requires one admin device to exist and be enforced correctly
on the device being demoed. It must **not** be presented as, or mistaken
for, a cross-device guarantee — that would require either a sync channel
for device state (a real follow-on scope addition to the sync stream) or
routing admin-device decisions through Owner as an external authority
(closer to 0-b's shape). Both are out of scope this week.

## Residual/deferred items

- 0-b: derive `company_id` from licensing/installation id once licensing is
  actually configured. Needs sync-stream owner sign-off (touches the shared
  tenant-identity contract sync already depends on).
- 0-c: "join existing installation" onboarding path for device 2+,
  composes with 0-b. Needs sync-stream owner sign-off for the same reason.
- Cross-device propagation of `devices`/`user_devices` state (so "one Admin
  Device" becomes a real cross-device guarantee, not a per-registry.db one)
  — requires either extending the sync outbox's entity types or routing
  through Owner; not scoped this week.
- `device_context.py` (`resolve_local_device()`, the 0-a drift check
  described above) — Tuesday.
- `device_routes.py` + blueprint registration in either product's `app.py`
  — Wednesday.
- `.env.example` documentation for any new config this feature ends up
  needing — Wednesday.
- Actual login enforcement wiring (`mt_auth.py`, `auth_routes.py`,
  `onboarding_routes.py`, either product's `config.py`) — explicitly not
  this week; enforcement stays default-OFF until a later, separately
  reviewed step.
