# Lease Public Key Ring Contract (M11.5) / Key Rotation (M11.6)

Real, exact port of `trust_store.py`'s `TrustedKey` model
(`canonical-signed-lease-authority-audit.md`), implemented in
`LeaseVerificationKeyRing.kt`.

## Real model

`TrustedLeaseKey(keyId, publicKeyB64, algorithm, status: ACTIVE|RETIRED, source: BUNDLED_ANCHOR|ROTATION_MANIFEST)`.
`REVOKED` keys are removed outright, never stored with that status —
`isTrusted()` is a pure membership check, matching the Python
reference exactly.

## Real, structural production/test separation (M11.5's own required split)

- **`ProductionLeaseKeyRing`** — the only ring release wiring resolves.
  Bootstrapped from `BUNDLED_PRODUCTION_KEYS`, a real Kotlin literal
  mirroring the exact real, checked-in production trust anchor
  (`commercial_runtime/licensing_contracts/trust_anchor.json`:
  key `owner-ed25519-20260727T053324Z-c32537d7`). No private key
  material exists anywhere in this ring or its construction path.
- **`InMemoryLeaseKeyRing`** — `commonTest`/`androidUnitTest` only,
  constructed exclusively from freshly-generated test keypairs
  (`LeaseTestFixtures.freshKeyPair()`), never seeded with the real
  production key.

## Real, disclosed limitation

`BUNDLED_PRODUCTION_KEYS` is a **manual mirror** of the real Python
trust anchor file, not an automated build-time copy. A future
milestone should wire a real Gradle task that copies
`commercial_runtime/licensing_contracts/trust_anchor.json` into a KMP
resource/generated-source at build time, so the two can never silently
drift — recorded here as real, open follow-up infrastructure work, not
silently claimed automatic. Until then, any real production key
rotation requires a manual, reviewed update to
`ProductionLeaseKeyRing.BUNDLED_PRODUCTION_KEYS` alongside whatever
process updates the canonical Python file.

## Key rotation (M11.6)

`ProductionLeaseKeyRing.admitVerifiedManifest()` is a real, pure,
exact port of `trust_store.py::admit_manifest`'s own real admission
rule (add/update ACTIVE/RETIRED entries, remove REVOKED entries) —
**minus** the manifest's own signature verification, which the caller
must perform first via the same `SignedLeaseVerifier`/
`verifyEd25519Signature` this ring is itself resolved by (a real,
disclosed circular-dependency the Python reference resolves by passing
a plain verification function; the Kotlin port keeps verification the
caller's responsibility rather than tangling the two together).

**Real, disclosed scope**: `admitVerifiedManifest()` exists as a real,
tested pure function, but **no runtime code calls it** in this
milestone — real key-manifest fetching requires the real production
transport M11 explicitly must not enable (`DisabledProductionTransport`
remains the only production transport). This is real, future-ready
code, not yet wired into any live path.

## Real, required proof: release wiring cannot resolve test key material

`ProductionLeaseKeyRing`'s only constructor default is
`BUNDLED_PRODUCTION_KEYS` — a compile-time Kotlin literal, not a
lookup that could resolve a test double. `InMemoryLeaseKeyRing` is
never referenced from any `androidMain`/production-wiring file (a real
regression a future `androidApp`-graph test should assert, matching
`production-test-material-isolation.md`'s own required proof — not yet
written in this milestone, real disclosed gap).

## Emergency key compromise handling

`revokeLocally()` (real, exact port of `trust_store.py::revoke_locally`)
provides immediate, local, out-of-band revocation — real, tested at
the type level, not yet wired into any real UI/support flow (that
requires the restricted-recovery-mode UI, M11.26, not yet built this
session).
