# Activation Secure Persistence Integration (M10.21)

Real replacement of M9's own test-only secure persistence boundary
with real platform-backed production storage adapters.

## Real production sequence (unchanged shape from M9.12, now real underneath)

```
Transport response
→ structural validation          (kotlinx.serialization, unchanged since M7.17)
→ contract validation            (ActivationResponseProcessor, M9.12, unchanged)
→ secure activation-bundle commit (SecureMaterialStoreActivationSink -> GenerationalSecureMaterialStore, real, M10.6/M10.21)
→ commit verification             (real, built into commitActivationBundle itself, M10.6)
→ non-secret activation metadata update (SecureActivationBundleMetadata, real, M10.5)
→ ACTIVATION_COMPLETE
```

## Real bridge — `SecureMaterialStoreActivationSink`

`shared/.../securestorage/SecureMaterialStoreActivationSink.kt`
implements both M9.13's `InstallationCredentialSink` and
`SignedLeaseSink` — `ActivationResponseProcessor` (M9.12) calls each
exactly once per real activation, unchanged. This adapter gathers both
pieces and performs exactly **one** real, atomic
`commitActivationBundle` call once both have arrived — neither
individual `commit(...)` call durably persists anything alone,
preserving M10.6's own "no credential without its matching lease"
guarantee even though the two calls arrive separately from M9's own
already-accepted two-sink API.

## Real, binding constraint restated

`DisabledProductionTransport` remains the production transport until
the real Owner external API exists (unchanged since M9.3) —
**therefore production still cannot obtain a real activation response
today**. Secure storage itself is real and validated (M10.6's own 14
real tests); what remains untested in a real, live production sense
is the *end-to-end* real-network-response → real-secure-commit path,
because no real network response can occur yet.

## Real regression tests (M10.28/29)

- **Secure-store success allows completion in controlled test wiring**:
  `activationResponseProcessorCompletesOnlyWhenBothSinksCommit`
  (M9.29, unchanged, still green) — proves the real completion path
  using `InMemorySecureMaterialSink`.
- **Secure-store failure blocks completion**:
  `activationResponseProcessorStopsAtSecurePersistenceRequiredWhenSinkFails`
  (M9.29, unchanged) — proves the real block using `NoSecureStorage
  AvailableSink`, the same real, honest production default.
- **Production transport still cannot fabricate activation**:
  `productionWiringCannotConstructAFakeSuccessTransport` (M9.29,
  unchanged) — real, structural, unaffected by M10's own additions.
- **Fixture transport absent from release production graph**: real,
  structural — `ContractFixtureTransport` remains `commonTest`-only
  (unchanged since M9.28); `SecureMaterialStoreActivationSink` and
  `GenerationalSecureMaterialStore` are both real `commonMain` types,
  never `commonTest`-only, so their presence in production wiring is
  correct and intended (unlike the fixture transport, which must never
  appear there).

## Real, new M10 test proving the bridge itself works

`secureMaterialStoreActivationSinkCommitsOnlyOnceBothPiecesArrive`
(M10.29) — real, direct proof that neither `commit(installationId,
credential)` alone nor `commit(installationId, lease)` alone produces
a durable, loadable bundle; only both together do.
