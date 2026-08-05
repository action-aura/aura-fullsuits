# Licensing Contract Fixture Report (M7.22 / M7.23)

Real, executed inventory of the M7.17 shared contract models and
M7.18 fixture suite, `shared/src/commonMain/kotlin/com/actionaura/
retail/licensing/` and `shared/src/commonTest/kotlin/com/actionaura/
retail/licensing/`.

## Files delivered

| File | Contents |
|---|---|
| `LicensingEnums.kt` | `LicensingPlatform`, `LicensingProductCode`, `LicenseStatus`, `InstallationStatus`, `SubscriptionStatus` — real, audited values only |
| `LicensingError.kt` | `ServerReasonCode` (31), `LocalReasonCode` (18), `RetryGuidance`, `LicensingError` sealed interface with unknown-code handling |
| `AssertionContracts.kt` | `OfflinePolicy`, `AssertionPayload`, `SignedAssertionEnvelope` |
| `ActivationContracts.kt` | `ActivationRequest`, `ActivationResult` |
| `CheckInContracts.kt` | `CheckInRequest`, `CheckInResult`, `DeactivationRequest`, `DeactivationResult` |
| `InstallationContracts.kt` | `InstallationDescriptor` |
| `ReleaseContracts.kt` | `ReleaseCheckRequest`, `ReleaseCheckResult` |
| `LicensingFixtures.kt` (test) | `FIXTURE_SET_VERSION = "m7-fixtures-v1"`, all required scenario fixtures |
| `LicensingContractTest.kt` (test) | 23 real, executed tests |

## Required scenario coverage (offline-license-lease-contract-audit.md's own list)

| Scenario | Fixture | Test |
|---|---|---|
| valid | `validAssertion()` | `validAssertionHasActiveLicenseStatus` |
| grace | `graceAssertion()` | `graceFixtureIsPastExpiryButCarriesACommercialGraceEnd` |
| expired | `expiredAssertion()` | `expiredSuspendedRevokedFixturesCarryTheirRealDistinctLicenseStatus` |
| suspended | `suspendedAssertion()` | same |
| revoked | `revokedAssertion()` | same |
| wrong-product | `wrongProductAssertion()` | `wrongProductFixtureDiffersFromRequestedProduct` |
| wrong-platform | `wrongPlatformAssertion()` | `wrongPlatformFixtureDiffersFromRequestedPlatform` |
| unknown-key | `unknownKeyAssertion()` | `unknownKeyFixtureUsesADifferentSigningKeyIdThanTheValidFixture` |
| malformed-signature | `malformedSignatureAssertion()` | `malformedSignatureFixtureIsNotValidBase64Shape` |
| future-version | `futureContractVersionAssertion()` | `futureContractVersionFixtureUsesAVersionThisClientDoesNotRecognizeAsV1` |
| min-version-failure | `minVersionFailureError()` | `minVersionFailureFixtureMapsToRealVersionUnsupportedNotAnInventedCode` (documented `NOT_APPLICABLE_WITH_EVIDENCE` for a true app-semver gate; exercises the real `VERSION_UNSUPPORTED` code instead, per `mobile-release-version-contract.md`) |

## No real private keys / no real secrets — verified, not merely asserted

`fixturesAreObviouslyFakeNeverResemblingRealKeyMaterial` asserts every
fixture's `licenseKey`/`devicePublicKey`/`signature` string literally
contains `FIXTURE`/`FAKE` — a real, executable guard against a future
edit accidentally substituting real-looking material into this file.

## Test matrix (M7.22)

| Category | Real test(s) |
|---|---|
| Domain contract shape | `licenseStatusHasExactlyTheSevenRealAuditedValues`, `installationStatusHasExactlyTheSixRealAuditedValues`, `platformHasExactlyTheTwoRealSeededValuesNoIos` |
| Serialization round-trip | `activationRequestRoundTripsThroughJson`, `assertionPayloadRoundTripsThroughJson`, `signedAssertionEnvelopeRoundTripsThroughJson` |
| Multi-device / lease scenarios | the 11 fixture-backed tests listed above |
| Data minimization | `activationRequestSerializesToExactlyTheRealOwnerAllowlist`, `activationRequestToStringNeverExposesLicenseKeyOrSignature`, `deactivationRequestToStringNeverExposesSignature`, `signedAssertionEnvelopeToStringNeverExposesSignature` |
| Authorization composition | Not a shared-model test — `local-authorization-vs-license-entitlement.md`'s composition rule has no runtime implementation yet (M7.17 stops at contract models); no test claims otherwise. |
| Error-contract fixtures | `everyRealServerReasonCodeParsesToItself`, `unrecognizedServerCodeSurfacesAsUnknownNeverCoercedToAnExistingCase`, `rateLimitedAndServiceUnavailableAreTheOnlyServerCodesSafeToRetry` |

## Real, executed result

`:shared:testDebugUnitTest --tests "com.actionaura.retail.licensing.*"` →
23/23 passed, 0 failures, 0 errors
(`shared/build/test-results/testDebugUnitTest/TEST-com.actionaura.
retail.licensing.LicensingContractTest.xml`).
Full-suite run (`:shared:testDebugUnitTest`, no filter) → 564 total
tests across the whole `shared` module, 0 failures, 0 errors — see
`milestone-7-test-report.md` for the progression from the M6 baseline.
