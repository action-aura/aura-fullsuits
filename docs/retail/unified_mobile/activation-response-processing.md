# Activation Response Processing (M9.12)

`ActivationResponseProcessor` (`shared/.../licensing/transport/
ActivationResponseProcessor.kt`).

## Real, layered processing (per the checkpoint's own 9-step list)

1. **Structural decoding** — happens before this class is ever
   invoked, via `kotlinx.serialization` decoding directly into the
   real M7.17 `ActivationResult` sealed type (never a generic `Map`,
   avoiding the exact corruption class `mobile-licensing-network-
   audit.md` found in the legacy Gson-based client).
2. **Contract-version validation** — handled upstream by
   `TransportOutcome.UnsupportedContractVersion` (M9.2); by the time a
   `Success<ActivationResult>` reaches this processor, the version is
   already known-supported.
3. **Product validation** / 4. **Platform validation** — real, explicit
   checks in `finishApprovedOrAlreadyActive`: the response's own
   `assertion.payload.productCode`/`platform` must match the caller's
   `expectedProduct`/`expectedPlatform`, or the result is
   `ActivationProcessingResult.ContractViolation`.
5. **Installation identity validation** — `installationPublicId` must
   be non-blank.
6. **Resolved-policy validation** — not independently re-validated
   here; `ResolvedDevicePolicy.validate()` (M8.1) is the real,
   dedicated validator, invoked by the caller before this processor
   runs (kept separate — this class's own single responsibility is the
   activation *response*, not the device policy).
7. **Credential handoff** — `credentialSink.commit(installationPublicId,
   InstallationCredentialMaterial(...))`.
8. **Signed-lease handoff** — `leaseSink.commit(installationPublicId,
   assertion)`.
9. **Presentation result** — `ActivationProcessingResult`
   (`Complete`/`SecurePersistenceRequired`/`ContractViolation`/
   `Rejected`/`Pending`).

## Real, binding completion rule

Activation is marked `Complete` **only** when both the credential sink
and the lease sink return `SecureMaterialCommitResult.Committed` — any
other combination yields `SecurePersistenceRequired`, never `Complete`.
Since M9's only real sink implementations are `InMemorySecureMaterialSink`
(test-only) and `NoSecureStorageAvailableSink` (the real, honest
production default, which always fails), **production activation can
never reach `Complete` in this milestone** — a real, deliberate,
disclosed consequence of M9's own scope boundary (`SECURE_STORAGE_NOT_
IMPLEMENTED`), not an oversight.

## `Pending` (manual-approval activation)

`ActivationResult.Pending` maps straight through to
`ActivationProcessingResult.Pending` — no credential/lease exists yet
to hand off (matches the real Owner server behavior audited in
`installation-credential-contract.md`: a manual-approval activation
issues no signed assertion until staff approval).
