# Installation Authority Contract (M7.6)

Exact real `Installation` model and confirmation of the "one License →
zero-or-more Installations → one independent credential/lease per
Installation" claim, verified against real code — not assumed.

## Real model

`app/models/installations.py:17-56`, table `owner_installations`.
Columns: `id`, `customer_id` (FK, NOT NULL), `subscription_id` (FK,
nullable), `license_id` (FK → `owner_licenses.id`, nullable),
`product_id` (FK, NOT NULL), `platform_id` (FK, NOT NULL),
`installation_label`, `device_label`, `os_version`, `app_version`,
`release_channel_id` (FK, nullable), `status` (default
`"REGISTERED"`), `first_registered_at`, `last_check_in_at`,
`activation_count` (default 0), `deactivated_at`, `support_notes`,
`fingerprint_hash` (String(128), nullable — explicitly a
"privacy-safe hash placeholder, not raw HW ID" per its own comment,
`installations.py:44`), `device_public_key` (Text, nullable —
explicitly a dead/unused "future device-identity placeholder" per its
own comment, `installations.py:45`; confirmed unread/unwritten by
`activation.py`/`checkin.py`/`deactivation.py`/`device_identity.py`).

Real state machine, `VALID_TRANSITIONS`
(`app/installations/services.py:18-25`):

```
REGISTERED         -> {PENDING_ACTIVATION, ACTIVE, SUSPENDED, DEACTIVATED}
PENDING_ACTIVATION -> {ACTIVE, DEACTIVATED}
ACTIVE              -> {SUSPENDED, DEACTIVATED, REPLACED}
SUSPENDED           -> {ACTIVE, DEACTIVATED}
DEACTIVATED         -> {}   (terminal)
REPLACED            -> {}   (terminal)
```

`SLOT_CONSUMING_STATUSES = (REGISTERED, PENDING_ACTIVATION, ACTIVE,
SUSPENDED)` (`services.py:16`) — the set counted against
`License.device_limit`.

## Confirmed: one Installation, one independent credential

**Confirmed true — real per-device credential, not a shared
per-license token.** Evidence:

- `DevicePublicKey` (table `owner_device_public_keys`,
  `app/models/licensing_service.py:35-51`) has `installation_id`
  (FK, NOT NULL) — a 1:N-over-time relationship (an Installation may
  rotate through multiple `DevicePublicKey` rows, but each row belongs
  to exactly one Installation).
- `fingerprint` (String(64)) is **globally unique** (line 42) — this
  is the real constraint that prevents the same physical device
  identity from ever mapping to more than one live `DevicePublicKey`
  row, confirmed via `get_device_key_by_fingerprint()`'s own docstring
  (`device_identity.py:85-94`).
- `register_device_key()` (`device_identity.py:68-76`) creates a new
  `DevicePublicKey` row scoped to one `installation_id` during
  activation (`activation.py:250-258` for brand-new registrations).
- Verification (check-in, deactivation) always resolves the specific
  device key for that specific installation
  (`get_most_recent_device_key`, `deactivation.py:65-71`;
  `device_identity.verify_signature()`, `checkin.py:78`,
  `deactivation.py:70`) — never a license-wide shared secret.

**Confirmed false: "one shared token per License."** No code path in
`activation.py`/`checkin.py`/`deactivation.py`/`device_identity.py`
issues or checks a credential scoped to `license_id` alone; every
credential check resolves through `installation_id` →
`DevicePublicKey`.

## Real activation-count / re-registration behavior

`Installation.activation_count` increments on each real activation
event (confirmed present as a column; incrementing call site is inside
`process_activation()`'s registration branch). A device retrying with
the same signed request (idempotency key) reuses the existing
Installation and device key rather than creating a duplicate row
(`activation.py:196-215`).

## Real audit trail

`InstallationStatusHistory` (staff-driven transitions, via
`transition_installation()`, `app/installations/services.py:58-82`)
and `ActivationEvent` (`installations.py:89-107`, every activation/
deactivation/check-in decision, `event_type`/`result`/`reason_code`/
`correlation_id`).

## Real test coverage

`owner/tests/test_installations.py` (no raw hardware-ID columns
stored; `VALID_TRANSITIONS` exercised, line 58; activation event
recorded on registration), plus the full activation/check-in/
deactivation/concurrency suites cited in
`owner-licensing-authority-audit.md`.

## Mobile contract implication

The shared `InstallationDescriptor`/`InstallationStatus` models
(M7.17) must model Installation as owning its own, rotatable device
credential — never assume or design for a license-wide shared secret.
