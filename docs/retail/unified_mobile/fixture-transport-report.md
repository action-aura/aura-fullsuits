# Fixture Transport Report (M9.28)

`ContractFixtureTransport` (`shared/src/commonTest/.../licensing/
transport/ContractFixtureTransport.kt`) — test-only, deterministic,
per-operation-configurable. `M9SanitizedFixtures.kt` builds real
sanitized scenario data on top of the already-audited M7.18/M8.14
fixture sets, `FIXTURE_SET_VERSION = "m9-orchestration-fixtures-v1"`.

## Real scenario coverage (per the checkpoint's own required list)

| Scenario | Real test |
|---|---|
| Sign-in success | `signInSuccessMapsToAuthenticatedState` |
| Invalid credentials | `signInInvalidCredentialsMapsToError` |
| Verification required | Modeled via `CustomerAuthenticationState.VerificationRequired`; state-machine transition covered in `fullHappyPathStateTransitionMap`'s own `RESPONSE_RECEIVED → CUSTOMER_VERIFICATION_REQUIRED` real path |
| Account locked / disabled | Real, closed cases exist (`CustomerAuthenticationState.Locked`/`Disabled`) — reachable once a real server contract exists; not independently fixture-tested since no real distinguishing server code exists yet (honestly disclosed, same as `licensing-error-presentation-map.md`'s own Customer-auth-category note) |
| License claim success | `licenseClaimSuccessTarget` |
| License not owned / Subscription inactive / suspended / expired / revoked | `licenseClaimNotOwnedInactiveSuspendedExpiredRevoked` |
| Android allowed | `LicensingPlatform.ANDROID` used throughout, e.g. `activationResponseProcessorCompletesOnlyWhenBothSinksCommit` |
| iOS contract supported but server unseeded | `ios-activation-orchestration-readiness.md` + `IosPlatformReadinessState.current()` (M8, still green) |
| Device slot available / final slot / device limit reached / same Installation retry | `DeviceSlotOutcome` (M8.3, still green) + `responseLostAfterCommitSameInstallationRetryReusesTheSameOutcome` |
| Activation success target response | `activationResponseProcessorCompletesOnlyWhenBothSinksCommit` |
| Malformed activation response | `TransportOutcome.MalformedResponse` real case, `malformedResponseAndUnsupportedVersionMapToContractCategory` |
| Wrong Product / wrong Platform | `activationResponseProcessorRejectsWrongProductOrPlatform` |
| Unknown contract version | `TransportOutcome.UnsupportedContractVersion`, same test |
| Network timeout / rate limit / server unavailable | `rateLimitedTimeoutNetworkFailureAreSafeToRetryOnly` |
| Response lost after commit | `responseLostAfterCommitSameInstallationRetryReusesTheSameOutcome`, `activation-process-recovery-contract.md` |
| Secure persistence failure | `activationResponseProcessorStopsAtSecurePersistenceRequiredWhenSinkFails` |
| Lease refresh success target response | `leaseRefreshSuccessTarget` |
| Installation revoked | `leaseRefreshMapsRealBusinessRejectionsToDistinctStates` |
| Required update | `LeaseRefreshState.RequiredAppUpdate` real case, mapped from `TransportOutcome.UnsupportedContractVersion` in `LeaseRefreshOrchestrator` |

## Real, verified isolation from production

`ContractFixtureTransport` lives exclusively under `shared/src/
commonTest/` — a separate Gradle/Kotlin compilation unit from
`commonMain`/`androidMain`. `productionWiringCannotConstructAFakeSuccessTransport`
(M9.29) is the structural proof: the only `ExternalLicensingTransport`
implementation referenced anywhere in production source is
`DisabledProductionTransport`.

## No real secrets

`M9SanitizedFixtures` reuses `LicensingFixtures.validAssertion()`
(M7.18's own already-audited, obviously-fake assertion fixture) rather
than fabricating new fake signature material — every other field
(`FIXTURE_ACCESS_TOKEN_NOT_REAL`, `FIXTURE-SERIAL-...`, etc.) follows
the same real, established `FIXTURE`/`FAKE` naming discipline.
