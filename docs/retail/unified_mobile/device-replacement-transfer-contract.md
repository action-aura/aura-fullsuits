# Device Replacement and Transfer Contract (M8.6)

Shared request/result models for future device-management actions —
contract shape only, no HTTP calls, per M8's own scope. Every
operation below requires real server authorization; the client never
marks another Installation deactivated on its own authority.

## Real operations modeled

`shared/.../licensing/DeviceManagementContracts.kt`:

- `DeactivateInstallationRequest(installationId, reason: String?)` —
  mirrors the real `DeactivationRequest` (M7.17) shape at the
  transport level but is the *device-management* intent type used by
  presentation code before a transport-level request is built.
- `RequestDeviceReplacementRequest(oldInstallationId, reason: String)`
- `ReplaceLostDeviceRequest(oldInstallationId, reason: String)`
- `ReplaceRevokedDeviceRequest(oldInstallationId, reason: String)` —
  modeled distinctly from lost-device replacement because Owner's own
  real device-key revocation is permanent and non-retroactive
  (`device_identity.py:112-121`, cited in
  `installation-credential-contract.md`) — a revoked-device replacement
  is real staff/administrative territory, not a self-service retry.
- `CancelReplacementRequest(replacementRequestId)`
- `ListInstallationsRequest(licensePublicId)`
- `RenameDeviceLabelRequest(installationId, newLabel: String)` — the
  only one of these that touches a real, already-modeled, non-security
  field (`InstallationDescriptor.deviceLabel`, M7.17).

## Real, stable outcome set

`DeviceManagementOutcome`:

```
DEACTIVATED
REPLACEMENT_APPROVED
REPLACEMENT_DENIED
DEVICE_NOT_FOUND
DEVICE_ALREADY_INACTIVE
DEVICE_REVOKED
REAUTHENTICATION_REQUIRED
OWNER_SUPPORT_REQUIRED
```

`REAUTHENTICATION_REQUIRED` reflects Owner's own real `require_recent_
auth` pattern (`customer-authentication-gap-analysis.md`'s staff-side
finding) — conceptually mirrored here for a future customer-facing
equivalent once M8.10's prerequisite spec is satisfied; it is not
claimed to exist as a real customer-facing mechanism today.
`OWNER_SUPPORT_REQUIRED` is the honest fallback for scenarios this
contract cannot self-resolve (e.g. a genuinely ambiguous lost/stolen
device with no available replacement policy) — matching M7's own
discipline of surfacing a gap rather than inventing a resolution.

## Kept distinct from ordinary activation

`RequestDeviceReplacementRequest`/`ReplaceLostDeviceRequest`/
`ReplaceRevokedDeviceRequest` are separate types from `ActivationRequest`
(M7.17) even though a successful replacement will eventually still
involve a real activation call — the *intent* (replacing a specific,
named old Installation) is a distinct, audited operation server-side
(`replace_device_slot()`/`replace_device_key()`, staff route `POST
/licensing-admin/installations/<id>/replace-device`,
`installation-credential-contract.md`), and conflating the two request
shapes would blur that real distinction for presentation code and any
future audit trail.

## No self-authorized deactivation of another Installation

Every request type above carries only the *target* Installation's
identifier — no request type grants the calling device authority over
itself to deactivate a *different* Installation without server-side
authorization; the shared model enforces no more than intent
expression, and the outcome enum's own `REAUTHENTICATION_REQUIRED`/
`OWNER_SUPPORT_REQUIRED` cases exist specifically so the client never
assumes success.
