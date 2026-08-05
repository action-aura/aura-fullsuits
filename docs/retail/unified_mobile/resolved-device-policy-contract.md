# Resolved Device Policy Contract (M8.1)

One immutable shared model representing a server-resolved device
policy — the client consumes exactly one resolved value, never the
four raw Owner columns identified in `subscription-entitlement-
license-map.md` (M7.4)/`licensing-gap-ownership-matrix.md` gap #1.

## Real fields (adjusted to real evidence, not invented)

`ResolvedDevicePolicy` (`shared/.../licensing/DevicePolicyContracts.kt`):

- `policyVersion: Int` — versioned per M8's own forward-compatibility
  requirement; only `1` is currently supported (see
  `current-device-limit-semantics.md`, `DeviceLimitMode.TOTAL_ACTIVE_
  INSTALLATIONS`).
- `licensePublicId: String` — matches `AssertionPayload.
  licensePublicId` (M7.17), never the raw internal `License.id`.
- `productCode: LicensingProductCode`
- `allowedPlatforms: List<LicensingPlatform>` — decoded via
  `PlatformDecodeResult`, never a raw string list.
- `totalActiveInstallationLimit: Int` — the one real, resolved value;
  mirrors `License.device_limit` + any active `DeviceSlotException`
  extras, i.e. `resolve_effective_device_limit()`
  (`multi-device-license-policy-audit.md`, M7.7) — never `Plan.
  max_device_count`, `Subscription.device_allowance`, or the generic
  `max_devices` `PlanEntitlement`.
- `currentActiveInstallationCount: Int`
- `remainingInstallationSlots: Int`
- `voluntaryDeactivationAllowed: Boolean`
- `replacementAllowed: Boolean`
- `sameInstallationRetryConsumesSlot: Boolean` — real, `false` per
  `activation-idempotency-contract.md` (M7.10)'s own finding that a
  device-key-fingerprint match on a fresh `installation_id` reuses the
  existing installation rather than consuming a new slot.
- `releaseChannel: String?`
- `policyEffectiveAt: String` — ISO-8601 timestamp.

## Real internal-consistency validation (client-side, before use)

`ResolvedDevicePolicy.validate(): DevicePolicyValidationResult`:

- `totalActiveInstallationLimit >= 0`
- `currentActiveInstallationCount >= 0`
- `remainingInstallationSlots >= 0`
- when all three are present (always, since none are nullable):
  `remainingInstallationSlots == totalActiveInstallationLimit -
  currentActiveInstallationCount` — any disagreement is malformed.
- `allowedPlatforms` decodes entirely to `PlatformDecodeResult.Known`
  values — any `UnsupportedPlatform` entry in a *policy the client is
  about to trust* is malformed (this is stricter than parsing an
  activation error, where an unsupported platform is an expected,
  handleable outcome, not a policy-integrity failure).
- `policyVersion` is a real, currently-supported version (`1`).

Any violation produces `MALFORMED_DEVICE_POLICY` — a real,
non-recoverable-locally outcome. **The client may display the policy;
the client may never override it.** No code path in this milestone
computes, guesses, or substitutes a device cap when the server's
policy is malformed or unavailable — it surfaces the failure instead
(`SERVER_POLICY_UNAVAILABLE`, see `multi-device-scenario-contract.md`).

## Real citation discipline

Every field above traces to a real Owner concept already audited in
M7 (cited inline) or to a real M7.17 model (`AssertionPayload`,
`PlatformDecodeResult`) — no field is invented without a citation.
