# Keep-selling: what the Owner server still has to do

Branch `feat/keep-selling` (product side only; `owner/` was out of scope for it).
Owner decision 2026-10-24 (Aura Growth Plan): when a subscription lapses UNPAID the shop keeps
selling and only loses multi-device sync, online extras and FEATURE updates; SAFETY fixes still
reach it; after renewal sync catches up with nothing lost.

## What the product side now does
- Signed `subscription_status` EXPIRED, or PAST_DUE past the signed `commercial_grace_end`, puts
  the device in `SUBSCRIPTION_LAPSED`: every local capability stays on.
- The device pauses its OWN sync (SyncService, hub ForwarderLoop, LAN hub `/push` `/pull`
  `/bootstrap`). Its outbox keeps filling and drains on the first sync after renewal.
- CANCELLED and SUSPENDED subscriptions still restrict the till — owner decision 2026-10-24: they
  stay locked. Note staff can SUSPEND by hand via `subscriptions.suspend`; doing that to a shop that
  merely has not paid LOCKS its till, so staff should leave unpaid shops to the expiry scan instead.
- Android now pauses its own sync too: every `SyncCoordinator` tick first asks the embedded Python
  backend (`GET /api/sync/_internal/pause-status`, the same `licence_pauses_sync()` rule) and stops
  before touching the outbox or any relay. It FAILS OPEN by design (a 404, non-200, exception or
  unreadable body means "not paused", so a probe that cannot read the licence lets the phone sync).
  That makes the Owner-side refusal in section 1 the authoritative gate, not the phone. A paused
  phone also refuses to join a LAN hub until it has caught up from the cloud after renewal.
- Not covered: the KMP `OfflineCommercialDecision.kt` still behaves as before.

## 1. Sync (authoritative gate belongs on Owner)
Today: `owner/app/sync/routes.py::_authenticate` checks signature, key, installation existence
and `installation.status` only; it never reads the subscription, so a lapsed shop can still sync
through the cloud (the product-side pause is the only gate, and on both Windows and Android it
fails open if the licence cannot be read).
Needed: in `_authenticate`, after the installation checks, refuse with
`SyncAuthError("SUBSCRIPTION_LAPSED")` (HTTP 400 `{"reason_code": ...}`, the existing shape) when
the subscription is EXPIRED, or PAST_DUE **past its commercial grace end** — use the same rule the
device uses (`commercial_ops/assertion_fields.py` signs `commercial_grace_end`). Do NOT refuse
PAST_DUE inside grace: the device treats that as fully active.
Do NOT implement this by changing `installation.status`: `sync/pruning.py` keeps the event log
only above the cursors of installations in `_SYNCABLE_INSTALLATION_STATUSES`; a device moved out
of that set is pruned past and gets 409 `RESYNC_REQUIRED` after renewal instead of catching up.
Risk: holding the prune watermark for a long-unpaid licence grows `owner_sync_events` for that
licence (row-count alarm at 20,000, `pruning.py`). Needs a retention decision for months-long
lapses.

## 2. Updates: let safety fixes through
Today: `owner/app/releases/distribution.py:139` refuses discovery and download for subscription
SUSPENDED / EXPIRED / CANCELLED (PAST_DUE is allowed). `discovery.py:147` shares that check.
`ProductVersion` has no field telling a safety fix from a feature release (`release_channel` is
STABLE/RC waves; `forced_upgrade_threshold` / `min_supported_version` mean "mandatory", not
"safety").
Needed: a release field (e.g. `release_kind` in FEATURE/SAFETY, default FEATURE) and, for an
unpaid subscription (same definition as section 1), filter discovery candidates to SAFETY releases
BEFORE picking the highest version, and allow download only of a SAFETY release. Loosening line
139 alone would open feature updates to unpaid shops.
NOTE: the phone's and web app's "updates are paused" wording for an unpaid shop depends on this
Owner-side update gate. Until it is built, a PAST_DUE-past-grace shop (PAST_DUE is allowed by line
139) is still offered feature updates today, so the wording promises more than the server enforces.

## 3. When a subscription becomes unpaid (for reference)
`commercial_ops/expiry_scan.py`: ACTIVE -> PAST_DUE once `end_date + past_due_start_days` is
reached (straight to EXPIRED when `past_due_start_days == 0`); PAST_DUE -> EXPIRED after
`payment_grace_days` more, only if `auto_expire_after_grace` is true — otherwise it stays PAST_DUE
forever, so every gate must handle PAST_DUE-past-grace, not just EXPIRED.

## 4. Renewal latency
The device learns of a renewal on its next licence check-in (`check_in_interval_seconds`, 24h by
default; the desktop UI also polls every 5 minutes while open). Consider a shorter interval while
lapsed, or a "check now" prompt after a renewal is confirmed.
