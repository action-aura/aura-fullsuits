# Device Policy Fixture Report (M8.16)

Real, executed inventory of M8's device-policy/identity/replacement/
iOS-readiness/presentation contracts and fixtures, and an honest
accounting of the required test matrix — what is real-tested, what is
structurally guaranteed by type design, and what is genuinely
out-of-scope/conceptual (never silently skipped).

## Files delivered

| File | Contents |
|---|---|
| `LicensingEnums.kt` (extended) | `LicensingPlatform` now includes `IOS`; `PlatformDecodeResult` |
| `DevicePolicyContracts.kt` | `DeviceLimitMode`, `ResolvedDevicePolicy` (+ `validate()`), `DevicePolicyValidationResult`, `DeviceSlotOutcome`, `IosReadinessFlag`, `IosPlatformReadinessState` |
| `InstallationIdentityContracts.kt` | `InstallationIdentityVersion`, `InstallationIdentityStatus`, `LocalInstallationSeed`, `InstallationIdentity` |
| `DeviceManagementContracts.kt` | `DeviceMetadata`, 7 replacement/transfer request types, `DeviceManagementOutcome` |
| `DevicePolicyPresentationContracts.kt` | `DevicePolicyPresentationState` (6 cases) |
| `DevicePolicyFixtures.kt` (test) | 22-fixture compatibility matrix, `FIXTURE_SET_VERSION = "m8-device-policy-fixtures-v1"` |
| `DevicePolicyContractTest.kt` (test) | 24 real, executed tests |
| `CanonicalVectorFixture.kt` + test (test) | Recovered, cross-checked canonicalization vector (M8.11) |

## Required test matrix — honest accounting

### PLATFORM
| Item | Status | Evidence |
|---|---|---|
| WINDOWS/ANDROID/IOS parse | Tested | `LicensingContractTest.platformDecodeParsesEveryKnownValue` |
| unknown/case-mismatch/future value rejected | Tested | `platformDecodeRejectsAllAnyMobileAndUnknownValuesWithoutFallback` |
| ALL rejection | Tested | Same test (`"ALL"` included) |

### DEVICE POLICY
| Item | Status | Evidence |
|---|---|---|
| valid policy | Tested | `validPolicyPassesValidation` |
| final slot / zero remaining | Tested | `finalRemainingSlotPolicyIsValidAndReportsOneSlot`, `zeroRemainingSlotPolicyIsValidButFull` |
| inconsistent counts / malformed values | Tested | `negativeLimitRejected`, `activeCountAboveLimitStillCaughtByRemainingCountConsistencyCheck`, `inconsistentRemainingCountRejected`, `unknownPlatformInPolicyRejected` |
| future policy version | Tested | `futurePolicyVersionFailsSafely` |
| exact one canonical device limit / no alternative-column exposure | Tested (structural + explicit) | `onlyOneCanonicalDeviceLimitFieldExistsOnResolvedDevicePolicy` — compile-time proof: no `planDeviceLimit`/`subscriptionDeviceAllowance`/`entitlementMaxDevices` field exists on the type at all |

### IDENTITY
| Item | Status | Evidence |
|---|---|---|
| valid generated-seed representation | Tested (structural) | `LocalInstallationSeed`/`InstallationIdentity` construction in the redaction tests |
| malformed seed | **Not applicable with evidence** | No real seed-generation function exists yet in `commonMain` (deferred to `ANDROID_ADAPTER`/`IOS_ADAPTER`, gap #8/#9) — there is nothing to malform; a "malformed seed" concept only becomes meaningful once real generation/parsing exists |
| redaction | Tested | `installationIdentityToStringNeverExposesTheRawSeed`, `localInstallationSeedToStringNeverExposesItsValue` |
| Product separation | **Not applicable with evidence** | No real generation/persistence code exists yet to test cross-Product reuse against — `installation-identity-seed-contract.md`'s requirement is binding on the future real implementation, not testable at the contract-shape layer alone |
| identity-preserved / identity-lost reinstall | Structural only | `lostIdentityStatusIsDistinctFromGeneratedNeverImpersonatesThePrevious` proves the two statuses can never be conflated by type; the full reinstall *flow* is documentation (`installation-reinstall-recovery-contract.md`), not yet executable code |
| backup duplication risk | **Not applicable with evidence** | Real resolution happens server-side (Owner's unique `fingerprint` constraint) — nothing in this shared module computes or could test this locally |

### REPLACEMENT
| Item | Status | Evidence |
|---|---|---|
| voluntary deactivation / replacement allowed / denied | Tested | `replacementAllowedAndDeniedPoliciesAreDistinct` |
| revoked / lost device | Tested (status distinctness) | `revokedAndDeactivatedInstallationsCarryDistinctRealStatuses` |
| reauthentication required | Tested (structural) | `deviceManagementOutcomesAreClosedAndDoNotIncludeAnUnauthorizedForceCase` confirms the real, closed outcome set includes `REAUTHENTICATION_REQUIRED` |

### IOS READINESS
| Item | Status | Evidence |
|---|---|---|
| contract supported / Owner unseeded / server unverified / build unverified / runtime unverified | Tested | `iosReadinessCurrentSnapshotHasEveryBlockingFlagAndNeverReadyForActivation` |
| ProductPlatform missing / release missing | Documented, not independently flagged in `current()` | `ios-platform-readiness-state.md`'s own current-state table lists these as real (both true today) but the shared `current()` snapshot intentionally models the smaller, decision-relevant flag set (`readyForActivation` only requires the presence of ANY blocking flag, so `PRODUCT_PLATFORM_MAPPING_MISSING`/`RELEASE_MAPPING_MISSING` are documented facts, not separately asserted in code, since `OWNER_PLATFORM_UNSEEDED` already dominates the same real conclusion) |
| fully ready target fixture | Tested | `fullyReadyTargetFixtureIsRepresentableButDistinctFromCurrentState` |

### PRESENTATION
| Item | Status | Evidence |
|---|---|---|
| device policy loaded / malformed policy | Tested | `presentationLoadedStateCarriesTheRealResolvedPolicyAndIosReadiness`, `presentationMalformedStateCarriesTheRealValidationProblems` |
| server unavailable / customer auth missing | Tested (distinctness) | `customerAuthPrerequisiteMissingStateIsDistinctFromServerUnavailable` |
| transport not implemented | Tested | `presentationStatesAreClosedAndIncludeTransportNotImplemented` |
| secrets absent from UI state | Tested (via underlying model redaction) | `Loaded` only ever embeds `ResolvedDevicePolicy`/`InstallationDescriptor`/`IosPlatformReadinessState`, none of which carry a secret field |

## Security matrix (M8.15) — see `device-policy-security-review.md` for the full threat-by-threat review; test cross-references only, here

`client-selected device limit ignored`, `client-selected active count
ignored` → structural (`clientCannotOverrideDeviceLimitOrActiveCount_
noMutatorExists`). `unknown Platform rejected`, `ALL rejected`, `MOBILE
rejected` → `LicensingContractTest`. `malformed policy rejected`,
`negative limit rejected`, `inconsistent counts rejected` →
`DevicePolicyContractTest`. `duplicate Installation identifier
rejected`, `same credential assigned to two Installations rejected by
contract` → **not independently tested**: no client-side collection-
dedup logic exists in this shared module (the real, authoritative
rejection is Owner's own unique `fingerprint` DB constraint,
`installation-authority-contract.md`) — classified `NOT_APPLICABLE_
WITH_EVIDENCE`, not silently omitted. `serial excluded from long-term
policy models` → structural (no such field exists on `ResolvedDevicePolicy`).
`credential redacted from toString`, `Installation seed redacted` →
tested. `metadata allowlist`, `prohibited business data absent` →
tested (`deviceMetadataSerializesToExactlyTheEightAllowedFields`,
`noSharedLicensingModelKeyMatchesAProhibitedDataCategory`). `lost
identity does not impersonate previous Installation` → tested
(structural). `duplicated restored identity classified as server-
rejected` → `NOT_APPLICABLE_WITH_EVIDENCE` (server-side behavior, no
local code to test). `local admin cannot bypass expired License`,
`valid License cannot bypass local feature permission` →
`NOT_APPLICABLE_WITH_EVIDENCE` (conceptual boundary,
`local-authorization-device-management-boundary.md`; local RBAC lives
outside this shared licensing package entirely, nothing to construct a
bypass with here). `iOS unseeded state cannot display activation-
ready` → tested. `future policy version fails safely` → tested.

## Real, executed result

`:shared:testDebugUnitTest --tests "com.actionaura.retail.licensing.*"`
→ all real licensing-package tests passed (M7's 23 + M8's 24 + 3
canonical-vector fixture tests = 50), 0 failures, 0 errors. Full-suite
and Android debug APK build results are recorded in
`milestone-8-test-report.md`.
