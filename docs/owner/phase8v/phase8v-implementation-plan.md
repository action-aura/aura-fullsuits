# Phase 8V — Implementation Plan (this session)

Execution order, each step ending in a green test run and a commit:

1. UI scaffolding: `app/commercial_ops/ui_routes.py` blueprint (`/commercial-ops/ui`), registered
   alongside the existing JSON blueprint (kept, unmodified). Base list/detail templates directory
   `app/templates/commercial_ops/`.
2. Renewals UI (highest-value, most complex — sets the pattern every later workflow reuses).
3. Pilots + emergency extensions UI.
4. Pending-activation review + device-slot operations UI.
5. Notification center + role-based queue view UI.
6. Reconciliation UI + commercial timeline + dashboard link closure.
7. Security pass: grep-audit every new template/route against `phase8-ui-security-boundary.md`.
8. Automated tests for every route (auth/RBAC/CSRF/object-level/optimistic-lock/domain-error cases).
9. Scenario validation suite (`phase8-validation-scenario-plan.md`'s 7 scenarios, HTTP-level).
10. Data-boundary + traffic-content verification for the new surfaces.
11. Migration reconfirmation against a fresh synthetic Phase-7-head database.
12. Final regression (Owner, commercial_runtime, Android Kotlin builds).
13. Final documentation set + honest release-gate decision (no physical Android evidence ->
    CONDITIONAL, per the governing brief's own tag-blocking rules).

No new tag until the decision doc says so.
