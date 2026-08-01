# Phase 9.5B-R3 — Milestone 3: Service Error Architecture Result

## Chosen resolution: Branch A (user-facing -> stable code + presentation-boundary translation)

For all 7 exception classes named in `service-message-call-path-audit.md`
(except the 2 deliberately-untouched ones), applied Branch A: convert the
service layer to raise a stable, request-context-free code, and localize
only at the Flask route boundary.

## Architecture

`owner/app/commercial_ops/errors.py` (new file) — shared base:

```python
class StableCodeError(ValueError):
    _MESSAGES: dict[str, str] = {}
    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        template = self._MESSAGES.get(code, code)
        super().__init__(template.format(**params) if params else template)
```

- Every raise site now does `raise XError("SOME_CODE", field=value, ...)`
  instead of an f-string sentence.
- `str(exc)` still produces a real, readable English diagnostic (for
  logs, CLI output, and the JSON API's `detail` field in
  `commercial_ops/routes.py`) — constructed with **zero** request/Flask/
  session dependency, satisfying Non-Negotiable Rule 3.
- A separate `localize_*_error(code, **params)` function per exception
  class lives in `app/i18n_labels.py`, using Flask-Babel's `_()` — called
  **only** from route handlers, never from service code.
- `commercial_ops/ui_routes.py` gained one dispatcher,
  `_localized_error_text(exc)`, which `isinstance()`-checks across all 6
  stable-coded types (`InvalidRenewalTransitionError`,
  `InvalidPilotTransitionError`, `PilotLifecycleError`, `DeviceSlotError`,
  `EmergencyExtensionError`, `PendingActivationError`,
  `ActivationPolicyError`, `NotificationError` — 8 classes, since
  `PendingActivationError`/`ActivationPolicyError` are both handled) and
  falls through to `str(exc)` unchanged for anything else
  (`RenewalApplicationError`, `StaleDataError`, generic `ValueError`/
  `KeyError`).

## Per-file changes

| File | Classes converted | Codes |
|---|---|---|
| `pilot_lifecycle.py` | `PilotLifecycleError`, `InvalidPilotTransitionError` | 11 codes (e.g. `SUBSCRIPTION_NOT_PILOT_STATUS`, `MAX_EXTENSIONS_REACHED`, `RENEWAL_SUBSCRIPTION_MISMATCH`) |
| `renewal_requests.py` | `InvalidRenewalTransitionError` | 4 codes (`INVALID_RENEWAL_TRANSITION`, `USE_APPROVE_FUNCTION`, `INVALID_RENEWAL_APPROVE_STATUS`, `INVALID_RENEWAL_APPLY_STATUS`) |
| `device_slot_ops.py` | `DeviceSlotError` (imports shared `StableCodeError`) | 8 codes |
| `emergency_extensions.py` | `EmergencyExtensionError` (shared base) | 6 codes |
| `activation_policy.py` | `ActivationPolicyError`, `PendingActivationError` (shared base) | 7 codes |
| `commercial_policy.py` | `NotificationError` (shared base); `CommercialPolicyError` reclassified operator-only | 2 codes |

`pilot_lifecycle.py` and `renewal_requests.py` predate `errors.py` and
still define their own local `_StableCodeError` — functionally identical
to the shared base (same `code`/`params`/`_MESSAGES` contract), left
as-is since a mechanical rename to import the shared class would touch
working code with zero behavioral benefit (both implementations are
verified byte-identical in contract).

## Non-Negotiable Rule 3 compliance

Verified by direct code inspection: no `gettext()`, `_()`, `flask.g`,
`flask.session`, or `current_app` reference exists anywhere inside any of
the 7 exception `__init__`/raise sites. Every one can be constructed and
`str()`-converted from a bare Python REPL with no Flask app context —
confirmed via a direct interactive import/raise/`str()` smoke test for a
representative sample (`DeviceSlotError`, `PilotLifecycleError`) outside
any `app.app_context()` block.
