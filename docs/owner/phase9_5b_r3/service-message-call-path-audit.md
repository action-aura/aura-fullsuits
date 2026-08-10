# Phase 9.5B-R3 — Milestone 3: Service Message Call-Path Audit

## Starting point

The governing spec names "the three service exception messages" carried
over from Phase 9.5B-R2. Tracing those to source: 3 raise sites across 2
exception classes in `owner/app/commercial_ops/pilot_lifecycle.py` and
`renewal_requests.py` (`PilotLifecycleError`/`InvalidPilotTransitionError`,
`InvalidRenewalTransitionError`).

## Real, expanded finding

Auditing the full call path of every exception raised from
`app/commercial_ops/*.py` and rendered into a Jinja template via
`error=str(exc)` in `ui_routes.py` found **11 raw-English raise sites**
across those same 2 files (not 3), plus **5 additional exception classes**
with the identical architectural defect that no prior wave's
documentation had named:

| Exception class | File | Raw-English raise sites |
|---|---|---|
| `PilotLifecycleError` / `InvalidPilotTransitionError` | `pilot_lifecycle.py` | 11 (combined) |
| `InvalidRenewalTransitionError` | `renewal_requests.py` | 4 |
| `DeviceSlotError` | `device_slot_ops.py` | 8 |
| `EmergencyExtensionError` | `emergency_extensions.py` | 6 |
| `PendingActivationError` | `activation_policy.py` | 6 |
| `ActivationPolicyError` | `activation_policy.py` | 1 |
| `NotificationError` | `commercial_policy.py` | 2 |

Every one of these was reachable from a real Flask route in
`commercial_ops/ui_routes.py` via `error=str(exc)` in a rendered
template — i.e. every one is a real, current, user-facing English-only
message, matching the governing spec's own PASS-bar language ("zero
unresolved current user-facing English-only message"), not just the
literal "three" named in the milestone title.

## Two classes deliberately left untouched, with real justification

- **`RenewalApplicationError`** (`renewal_requests.py`) — its message
  text (e.g. the literal string `"SELF_APPROVAL_NOT_ALLOWED"`) is
  pattern-matched via `"SELF_APPROVAL" in str(exc)` inside
  `commercial_ops/routes.py`'s JSON API error-code mapping (grep-confirmed:
  `owner/app/commercial_ops/routes.py`). Changing its string format risks
  breaking that real, already-shipped code path. Left as-is; its stable
  code *is* its message, already machine-safe.
- **`CommercialPolicyError`** (`commercial_policy.py`) — grep-confirmed
  that **no Flask route anywhere catches it**; it is raised only inside
  `create_commercial_policy()`'s config-validation guard, reachable only
  from CLI/seed/admin scripts, never from a staff member's browser.
  Reclassified in-place via a class docstring: `"""OPERATOR/MACHINE ONLY
  -- NOT TRANSLATABLE"""`. No behavior change; a real, verified
  unreachability, not an assumption.

## Verification method

- `grep -rn "error=str(exc)"` in `commercial_ops/ui_routes.py`: 0 matches
  remaining after the fix (was 19 before).
- `grep -rn "SELF_APPROVAL"` in `commercial_ops/routes.py`: confirms the
  one real dependency on `RenewalApplicationError`'s string format that
  justified leaving it untouched.
- Direct Python import of every touched module: confirmed clean (no
  syntax/import errors introduced by the refactor).
