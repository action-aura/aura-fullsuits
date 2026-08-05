# Installation Reinstall and Identity Recovery Contract (M8.5)

Exact behavior definitions for the three real reinstall scenarios,
built from M7's real device-identity findings
(`installation-authority-contract.md`,
`installation-credential-contract.md`) — no automatic identity-
transfer mechanism is invented.

## A. Identity preserved (app reinstall/upgrade retains secure storage)

- The device's Ed25519 key pair and `InstallationIdentity` survive the
  reinstall/upgrade (real platform secure storage is a later
  milestone, per gap #8/#9 — this case describes behavior once that
  storage exists).
- The same Owner `Installation` may be recovered: the device-key
  fingerprint still matches the existing `DevicePublicKey` row, so a
  fresh activation attempt is recognized as
  `DeviceSlotOutcome.SAME_INSTALLATION_RETRY`
  (`multi-device-scenario-contract.md` #12/#18) — no new slot is
  consumed, per the real, tested server behavior in
  `activation-idempotency-contract.md`.
- `InstallationIdentityStatus` transitions `PERSISTED_SECURELY →
  PERSISTED_SECURELY` (unchanged) — no `GENERATED`/`RECOVERY_REQUIRED`
  transition occurs.
- Credential recovery (re-obtaining a signed assertion) still requires
  the real, approved server check-in/activation flow — the client
  never assumes a cached assertion remains valid past its real
  `expires_at`/offline-grace window
  (`offline-license-lease-contract-audit.md`).

## B. Identity lost (app data and secure storage removed)

- The client has no real way to prove it is the same physical
  Installation Owner already knows about — its previous Ed25519 key
  pair no longer exists locally.
- **The client must not impersonate the old Installation.** No shared
  contract type or code path in M8 attempts to resubmit the old
  `installation_public_id` or old device-key fingerprint — a lost
  identity always presents as a genuinely new local
  `InstallationIdentity` (`InstallationIdentityStatus.GENERATED`,
  fresh `LocalInstallationSeed`).
- A new slot may be required — server-side, this looks exactly like
  scenario #19 in `multi-device-scenario-contract.md` (reinstall
  without preserved identity): `DEVICE_SLOT_AVAILABLE` or
  `DEVICE_LIMIT_REACHED`, indistinguishable from a brand-new device,
  because the server genuinely cannot tell the difference either.
- A replacement/deactivation flow may be required if the old
  Installation is still occupying a slot and the user has no more
  slots to spare — `DeviceSlotOutcome.REPLACEMENT_REQUIRES_
  DEACTIVATION`, per `device-replacement-transfer-contract.md`. This
  requires real staff or server-authorized action; the client cannot
  self-resolve it.

## C. Device restored from a backup (OS-level backup/restore, not app data)

- The client must **not** assume secure credentials restore
  identically on Android and iOS — platform backup semantics for
  Keystore-protected/Keychain-protected material differ by design on
  each OS (Android Keystore material is generally NOT restored across
  devices; iOS Keychain material MAY be restored depending on
  configuration) — this asymmetry is a real platform fact, not
  something this shared contract can paper over, so
  `InstallationIdentity`'s own contract makes no restoration guarantee
  either way.
- **Duplicated identities across two physical devices must be
  rejected.** If a restored backup causes two physical devices to
  simultaneously present the same device-key fingerprint, Owner's own
  real uniqueness constraint on `DevicePublicKey.fingerprint`
  (globally unique, `installation-authority-contract.md`) means only
  one of them can ever be the recognized, trusted Installation at a
  time server-side — the second device's activation/check-in attempt
  is rejected by the server's own real constraint, not by any local
  client logic. The client does not attempt to detect or resolve this
  locally; it simply surfaces whatever real server rejection results
  (`DEVICE_KEY_MISMATCH`/`INSTALLATION_REVOKED`, per
  `licensing-error-contract.md`).
- Server remains authoritative in all three restore sub-cases.

## No automatic identity-transfer mechanism

None of the three cases above introduces a "transfer my identity to
this new device" feature — that would require real customer-facing
authentication to authorize the transfer safely, which M7 already
proved does not exist (`customer-authentication-gap-analysis.md`).
Any such feature is real, future, `OWNER_SERVER`-dependent work,
correctly out of scope for M8.
