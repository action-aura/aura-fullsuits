# Device Policy Presentation Contract (M8.12)

Shared presentation models and ViewModel-consumable state for future
License and device-management screens. No real HTTP wiring — a
production build must expose an explicit "not implemented" state
rather than pretending a policy loaded.

## Real presentation state

`DevicePolicyPresentationState` (`shared/.../licensing/
DevicePolicyPresentationContracts.kt`), a sealed interface:

```
Loading
Loaded(policy: ResolvedDevicePolicy, installations: List<InstallationDescriptor>,
       iosReadiness: IosPlatformReadinessState)
ServerUnavailable
PolicyMalformed(problems: List<String>)
CustomerAuthPrerequisiteMissing
TransportNotImplemented
```

## Real field coverage (per the checkpoint's own required list)

`Loaded`'s own fields, via `ResolvedDevicePolicy` (M8.1) and
`InstallationDescriptor` (M7.17), already cover: current License
status (`AssertionPayload.licenseStatus`, reachable through the same
loaded-policy flow), allowed Platforms, current device count, total
device limit, remaining slots, current Installation, other
Installations (`installations` list, current one identified by
matching `installationId` against the local `InstallationIdentity`),
active/inactive/revoked states (`InstallationDescriptor.status`),
replacement availability (`ResolvedDevicePolicy.replacementAllowed`),
deactivation availability (`voluntaryDeactivationAllowed`), and iOS
readiness (`IosPlatformReadinessState.current()`, embedded directly).

## The four non-happy-path states, each real and distinct

- `ServerUnavailable` — real network/transport failure distinguishable
  from a malformed response (matches `DeviceSlotOutcome.SERVER_POLICY_
  UNAVAILABLE`'s own network-failure branch,
  `multi-device-scenario-contract.md`).
- `PolicyMalformed(problems)` — `ResolvedDevicePolicy.validate()`
  returned `DevicePolicyValidationResult.Malformed` — the real
  problems list is surfaced (developer/support-diagnostic value), the
  policy itself is never rendered as if valid.
- `CustomerAuthPrerequisiteMissing` — real, honest reflection of
  `OWNER-CUSTOMER-AUTH-PREREQUISITE-SPEC.md`'s own blocking
  classification, for any future screen whose data genuinely requires
  a logged-in customer principal (item 14 of that spec) rather than
  pure possession-based activation.
- `TransportNotImplemented` — the real, current, honest state for
  *every* production consumer of this contract today, since M8
  implements no HTTP execution (per its own scope restriction). This
  is not a placeholder bug; it is the correct, current state.

## Production wiring requirement (real, binding)

Any production `AuraAppContainer`/DI wiring (M6's own established
pattern, `presentation-di-scope-report.md`) that surfaces a device-
policy screen in this milestone or any milestone before real HTTP
execution exists **must** wire it to a provider that always emits
`TransportNotImplemented` — never a fabricated `Loaded` state, never a
silently-empty policy that looks like "0 devices, plenty of room." No
hard-coded administrative device limit is ever substituted.

## Test-only wiring

`LICENSING_TRANSPORT_NOT_IMPLEMENTED`-adjacent fake/sanitized
transports are permitted only under `commonTest`, using the M7.18/
M8.14 fixture suites — never reachable from a production entry point.
