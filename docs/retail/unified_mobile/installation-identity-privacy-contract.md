# Installation Identity / Privacy Contract (M7.11)

Defines the future non-invasive identity design and audits what the
server currently actually expects. Explicitly rejects invasive
hardware fingerprinting, per the governing checkpoint's own
instruction.

## What Owner currently actually expects from a device

Real, required identity-adjacent fields in the activation request
(`owner/contracts/activation-request-v1.schema.json`):
`installation_id` (client-generated, explicitly documented as
throwaway — "the server response returns a DIFFERENT, server-assigned
installation_id to use for all future calls"), `device_public_key` +
`device_public_key_algorithm` (the real, cryptographic identity — an
Ed25519 public key, not a hardware fingerprint), `app_version`,
`platform`, `release_channel`.

Optional, real, but explicitly non-authoritative fields on
`Installation` itself: `device_label` (String(128), human-readable,
staff/user-supplied), `os_version`, `fingerprint_hash` (String(128),
nullable — its own model comment states explicitly: "privacy-safe
hash placeholder, not raw HW ID",
`app/models/installations.py:44`). `Installation.device_public_key`
(a second, unrelated Text column) is confirmed dead code — never
read or written by `activation.py`/`checkin.py`/`deactivation.py`/
`device_identity.py` (its own comment: "future device-identity
placeholder").

## Real conclusion: Owner's actual identity model is already
non-invasive

The real, live device-identity mechanism is **cryptographic proof of
key possession** (Ed25519 device key, per-installation, rotatable),
not a hardware fingerprint of any kind. `fingerprint_hash` exists as a
schema column but:
- is nullable,
- is never populated by the real activation/registration code path
  (confirmed by grep — no write site found in `activation.py`),
- and its own model comment explicitly disclaims it as "not raw HW
  ID."

No IMEI, serial number, MAC address, advertising ID, or any other
invasive hardware identifier field exists anywhere in
`Installation`, `DevicePublicKey`, or the JSON-schema contracts —
confirmed by grep for `imei|serial_number|mac_address|advertising_id|
android_id|udid` across `owner/app/models/installations.py`,
`owner/app/models/licensing_service.py`, and
`owner/contracts/*.schema.json` (zero matches).

## Future mobile design (non-invasive, matching what Owner already expects)

1. Generate a fresh Ed25519 keypair locally on first launch (per
   platform's own secure storage — deferred to a later milestone per
   the governing checkpoint, not implemented in M7).
2. Send only the **public** key + algorithm at activation; the private
   key never leaves the device.
3. Use a client-generated, random (not hardware-derived)
   `installation_id` for the activation request only — it is
   discarded server-side in favor of the server-assigned
   `installation_public_id` for every subsequent call.
4. `device_label`/`os_version`/`app_version` may be sent as
   human-readable, non-unique metadata (support/diagnostics value
   only) — never a stable cross-app identifier.
5. Do **not** populate `fingerprint_hash` with any raw hardware
   identifier if a future milestone chooses to use it at all — its
   own real schema comment already forbids that use.

## Real test coverage

`owner/tests/test_installations.py` — explicitly asserts no raw
hardware-ID columns are stored. `commercial_runtime/
licensing_contracts/tests/test_device_identity.py` — DPAPI-backed key
round-trip, corrupted-key handling (`LocalStateCorruptError`, no
silent regeneration — a real privacy/integrity safeguard: a corrupted
local key is never silently replaced with a new "clean" identity,
which would otherwise be a re-identification/anti-fraud loophole).

## Mobile contract implication

M7.17's `InstallationDescriptor` model must only carry
`installationId` (locally-generated, throwaway), `devicePublicKey`,
`devicePublicKeyAlgorithm`, `deviceLabel` (optional, human-readable),
`osVersion`, `appVersion`, `platform`, `releaseChannel` — no field
resembling a persistent hardware identifier.
