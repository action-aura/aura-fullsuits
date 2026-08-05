# Multi-Device Scenario Contract (M8.3)

Shared client-side scenario contracts — interpretations of real server
results (per `multi-device-license-policy-audit.md`, M7.7), never
locally invented commercial decisions. No scenario below computes a
decision the server hasn't made; each maps a real server outcome (or a
real, honest "not yet supported on this platform" state) to one of 8
closed client-side result cases.

## Real result cases

`DeviceSlotOutcome` (`shared/.../licensing/DevicePolicyContracts.kt`):

```
DEVICE_SLOT_AVAILABLE
DEVICE_LIMIT_REACHED
SAME_INSTALLATION_RETRY
PLATFORM_NOT_ALLOWED
REPLACEMENT_ALLOWED
REPLACEMENT_REQUIRES_DEACTIVATION
INSTALLATION_REVOKED
SERVER_POLICY_UNAVAILABLE
```

## Scenario → real outcome mapping

| # | Scenario | Real outcome | Real basis |
|---|---|---|---|
| 1 | One Windows Installation, slot available | `DEVICE_SLOT_AVAILABLE` | `resolve_effective_device_limit()` > active count |
| 2 | One Android Installation, slot available | `DEVICE_SLOT_AVAILABLE` | Same mechanism — platform-agnostic count (M7.7 #1) |
| 3 | One iOS Installation | `PLATFORM_NOT_ALLOWED` today | `platform-authority-audit.md` — no `IOS` `owner_platforms` row; `ios-platform-readiness-state.md` governs this explicitly. Not `DEVICE_SLOT_AVAILABLE` under any circumstance until Owner seeds the platform. |
| 4 | Windows + Android, both within limit | `DEVICE_SLOT_AVAILABLE` (for the second) | M7.7 scenario 1 — count is not per-platform |
| 5 | Windows + iOS | `PLATFORM_NOT_ALLOWED` for the iOS attempt | Same as #3 |
| 6 | Android + iOS | `PLATFORM_NOT_ALLOWED` for the iOS attempt | Same as #3 |
| 7 | Windows + Android + iOS | `PLATFORM_NOT_ALLOWED` for the iOS attempt only; the other two follow #4 | Same as #3/#4 |
| 8 | Two Android devices, `device_limit=2` | `DEVICE_SLOT_AVAILABLE` for both | M7.7 scenario 1 |
| 9 | Two iOS devices | `PLATFORM_NOT_ALLOWED` for both | Same as #3 |
| 10 | Final available slot | `DEVICE_SLOT_AVAILABLE`, then next attempt `DEVICE_LIMIT_REACHED` | M7.7 scenario 4, real row-locked concurrency test |
| 11 | Attempt above limit | `DEVICE_LIMIT_REACHED` | `activation-idempotency-contract.md` |
| 12 | Same physical Installation retry (device-key fingerprint match, fresh local `installation_id`) | `SAME_INSTALLATION_RETRY` | M7.7 scenario 8 — reuses existing Installation, no new slot |
| 13 | Voluntary deactivation | Not a slot-check outcome — see `device-replacement-transfer-contract.md`'s own `DEACTIVATED` result | `deactivation.py` idempotent behavior, M7.6/M7.9 |
| 14 | Administrative revocation | `INSTALLATION_REVOKED` on any subsequent attempt by that device | M7.7 scenario 11 |
| 15 | Replacement after deactivation | `REPLACEMENT_ALLOWED` if the policy's `replacementAllowed` is true and a slot is free or the old Installation's slot was released; `REPLACEMENT_REQUIRES_DEACTIVATION` if the old Installation is still occupying a slot | `replace_device_slot()`/`replace_device_key()`, M7.7 scenario 10 |
| 16 | Lost device | `REPLACEMENT_REQUIRES_DEACTIVATION` until staff/administrative deactivation of the lost Installation completes | Real design implication of M7.7 scenario 10 — no automatic "lost" state exists server-side, so the client must not claim `REPLACEMENT_ALLOWED` until the old slot is actually freed |
| 17 | Stolen device | Same as #16, `REPLACEMENT_REQUIRES_DEACTIVATION` — plus the real device-key revocation path (`device_identity.py:112-121`) applies once staff acts | M7.6, M7.7 scenario 11 |
| 18 | Reinstall with preserved local identity | `SAME_INSTALLATION_RETRY` | Same as #12 — `installation-reinstall-recovery-contract.md` case A |
| 19 | Reinstall without preserved local identity | `DEVICE_SLOT_AVAILABLE` or `DEVICE_LIMIT_REACHED`, exactly like a brand-new device, since the server cannot recognize it | `installation-reinstall-recovery-contract.md` case B |

## `SERVER_POLICY_UNAVAILABLE`

Real, honest fallback when the client cannot obtain or trust a
`ResolvedDevicePolicy` (network failure, malformed response —
`DevicePolicyValidationResult.Malformed`, or the licensing transport
not yet implemented — `LICENSING_TRANSPORT_NOT_IMPLEMENTED` per
`device-policy-presentation-contract.md`). The client must never
substitute a locally-guessed outcome in this case — no scenario above
is evaluated at all until a real, valid policy is available.

## Explicitly not locally decided

None of the 8 outcome cases represent a client-computed commercial
decision — `DEVICE_LIMIT_REACHED` is only ever produced by relaying a
real server `DEVICE_LIMIT_REACHED` reason code
(`licensing-error-contract.md`) or by comparing the server's own
`remainingInstallationSlots` field (already server-computed) to zero
before attempting a call — the client never independently computes
whether a slot exists from first principles.
