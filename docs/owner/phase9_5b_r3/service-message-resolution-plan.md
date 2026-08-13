# Phase 9.5B-R3 — Service-Message Resolution Plan

Three messages to resolve:

1. `app/commercial_ops/pilot_lifecycle.py` — max-extensions-exceeded
   `PilotLifecycleError`.
2. `app/commercial_ops/renewal_requests.py` — invalid-transition
   `InvalidRenewalTransitionError` (transition_renewal_request).
3. `app/commercial_ops/renewal_requests.py` — wrong-status-for-apply
   `InvalidRenewalTransitionError` (apply_renewal_request).

## Method

For each: grep every call site (route, test, CLI, scheduler), trace
whether the raised exception's `str(exc)` value ever reaches
`render_template(..., error=...)`, a `jsonify()` response, a flash message,
or only a test assertion / log line. Classify Branch A (reaches the UI —
needs stable code + presentation-boundary translation) or Branch B
(operator/test-only — reclassify, prove unreachable, regression-test the
proof). Real source inspection already done in Phase 9.5B-R2 shows these
ARE caught in `commercial_ops/ui_routes.py` and shown via
`error=str(exc)` in real templates — meaning Branch A is the expected real
classification, not Branch B, pending final confirmation in
`service-message-call-path-audit.md`.
