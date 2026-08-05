# Installation Identity Seed Contract (M8.4)

Privacy-safe shared Installation identity contract, extending M7's own
`installation-identity-privacy-contract.md` finding that Owner's real
device-identity model is cryptographic (Ed25519 proof-of-possession),
never a hardware fingerprint.

## Real requirements (restated as binding client-side rules)

The future mobile implementation must use an application-generated
cryptographically random seed — never derived from IMEI, MAC address,
phone number, contacts, location, advertising identifier, Android ID
alone, or a hardware serial; not predictable; not reused across
unrelated Products (Retail vs. Clinic); not logged; not stored in
ordinary Retail business SQLite. These are the same real constraints
M7.11 already established from reading Owner's actual schema and
`Installation.fingerprint_hash`'s own disclaiming comment — M8 does
not re-derive them, it encodes them as real, closed types.

## Shared types (contract shape only — M8 scope, no real generation yet)

`shared/.../licensing/InstallationIdentityContracts.kt`:

- `InstallationIdentityVersion` — `enum class { V1 }`. Versioned so a
  future stronger seed algorithm can be introduced without silently
  reinterpreting an old one.
- `InstallationIdentityStatus` — `enum class { GENERATED,
  PERSISTED_SECURELY, RECOVERY_REQUIRED, LOST }`. `PERSISTED_SECURELY`
  is only reachable once real platform secure storage exists (a later
  milestone, per `licensing-gap-ownership-matrix.md` gap #8) — M8
  models the state, does not claim to reach it.
- `LocalInstallationSeed` — `data class(value: String, version:
  InstallationIdentityVersion)`. `value` is modeled as an opaque,
  already-encoded string (e.g. base64 of real random bytes) — this
  class never itself calls a random-number generator or platform
  keystore API (that is real platform code, out of `commonMain`,
  out of M8's own scope: "M8 may implement deterministic derivation
  functions only when they do not require real platform secure
  storage" — no such function is needed for the contract shape
  itself, so M8 defines none).
- `InstallationIdentity` — `data class(seed: LocalInstallationSeed,
  status: InstallationIdentityStatus, generatedAt: String)`. Its
  `toString()` redacts `seed.value` — matching the same discipline
  already established for `licenseKey`/`signature` in M7.17.

## Explicitly deferred (not implemented in M8)

Real random-byte generation, real platform secure storage (Android
Keystore / iOS Keychain), and real persistence are all `ANDROID_
ADAPTER`/`IOS_ADAPTER` work per `licensing-gap-ownership-matrix.md`
gaps #8/#9 — unchanged classification, M8 adds no new gap here, it
only adds the shared contract shape those future adapters will
implement against.

## Real test coverage requirement (M8.15/M8.16)

`InstallationIdentity.toString()` never exposes `seed.value` — a
fixture-backed test proves this, mirroring
`signedAssertionEnvelopeToStringNeverExposesSignature` (M7.18).
