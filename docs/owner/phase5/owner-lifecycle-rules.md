# Phase 5 -- Owner Lifecycle Rules (Part L/N/P)

Every stateful entity uses an explicit status field plus a matching `*_status_history` table, and every transition is checked against a fixed allowed-transitions map before being applied. Invalid transitions raise a typed exception and are never silently applied.

## Subscription lifecycle (`app/subscriptions/services.py::VALID_TRANSITIONS`)
```
DRAFT -> {PILOT, ACTIVE, CANCELLED}
PILOT -> {ACTIVE, COMPLETED, CANCELLED}
ACTIVE -> {PAST_DUE, SUSPENDED, EXPIRED, CANCELLED}
PAST_DUE -> {ACTIVE, SUSPENDED, EXPIRED, CANCELLED}
SUSPENDED -> {ACTIVE, CANCELLED, EXPIRED}
EXPIRED, CANCELLED, COMPLETED -> {}  (terminal)
```
Tested: `owner/tests/test_subscriptions.py` (5/5) -- valid transition succeeds, invalid transition rejected, status history recorded with reason, cancellation from a terminal state rejected, payment status validated.

## License lifecycle (`app/licensing/services.py::VALID_TRANSITIONS`)
```
DRAFT -> {ISSUED}
ISSUED -> {ACTIVE, SUSPENDED, REVOKED}
ACTIVE -> {SUSPENDED, EXPIRED, REVOKED}
SUSPENDED -> {ACTIVE, REVOKED, EXPIRED}
EXPIRED -> {REPLACED}
REVOKED, REPLACED -> {}  (terminal)
```
Tested: `owner/tests/test_licensing.py::test_invalid_status_transition_rejected` (DRAFT -> REVOKED correctly rejected; only DRAFT -> ISSUED is valid).

## Installation lifecycle (`app/installations/services.py::VALID_TRANSITIONS`)
```
REGISTERED -> {PENDING_ACTIVATION, ACTIVE, SUSPENDED, DEACTIVATED}
PENDING_ACTIVATION -> {ACTIVE, DEACTIVATED}
ACTIVE -> {SUSPENDED, DEACTIVATED, REPLACED}
SUSPENDED -> {ACTIVE, DEACTIVATED}
DEACTIVATED, REPLACED -> {}  (terminal)
```
Tested: `owner/tests/test_installations.py::test_valid_and_invalid_transitions` (REGISTERED -> ACTIVE succeeds; ACTIVE -> REGISTERED correctly rejected as a backwards move).

## Staff/session lifecycle
Not a status-field state machine but an equivalent set of hard rules enforced in `app/auth/session.py` and `app/staff/services.py`: a role change or account disablement immediately increments `session_version` and revokes every existing session for that staff member (`revoke_all_sessions_for_staff`) -- there is no window where an old session continues operating under a superseded role set. Verified: `owner/tests/test_auth.py::test_password_change_revokes_other_sessions`, `test_security.py::test_privilege_escalation_role_change_requires_recent_auth`.
