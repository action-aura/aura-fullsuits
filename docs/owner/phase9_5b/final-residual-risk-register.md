# Phase 9.5B — Final Residual Risk Register

## Real, deliberate deferrals (not gaps discovered late)

1. **Arabic/English/RTL localization.** Owner has zero pre-existing i18n infrastructure across every
   existing screen (Phase 5-9.5A). Deferred with a documented reason (`phase9-5b-scope-and-boundaries.md`)
   rather than a shallow, single-corner translation layer. **Risk if never addressed**: low for an internal
   tool with a known English-fluent staff base today; becomes real if Arabic-speaking staff are onboarded
   before a future phase adds real i18n.

2. **Mobile auth routes (`/api/operations/v1/auth/mobile/*`).** Explicitly out of both Phase 9.5A's and
   9.5B's own scope (no mobile client exists to consume them yet). The schema (`refresh_token_hash`/
   `refresh_token_family_id`) is ready; the routes are not built. **Risk**: none today (nothing depends on
   them); becomes relevant only when a mobile client project actually starts.

3. **Employee number auto-generation / setup-token reissue for an already-accepted account.** Management
   must type the employee number manually (by design, per the spec's own "assign employee number" wording).
   A lost/expired *unaccepted* invitation has a real reissue path (`reissue_employee_invitation()`); an
   *accepted* PENDING employee who can't complete MFA has no dedicated "resend" route this phase — the
   existing `staff.reset_mfa` action is the closest real tool, not a perfect match. **Risk**: low
   (an admin can always walk the employee through `/auth/mfa-enroll` again if their existing session or a
   quick support interaction is available); a real gap for a fully unattended flow.

4. **Commission-plan reassignment UI.** The field is set at creation time; changing it later has no route
   yet (`employees.manage_commission_plan` permission is seeded and ready). **Risk**: none — no commission
   *calculation* exists yet either (Phase 9.5A's own honest scope), so this is not yet load-bearing.

5. **Heartbeat rate limiting.** The spec asked for "max 1 accepted update per 30s ... excess return 200
   idempotently." The real implementation instead upserts the same row on every call (`touch_presence()`),
   which has the same practical effect (no unbounded row growth, no write amplification beyond a single
   `UPDATE` per call) but doesn't literally throttle request *rate* — a client sending heartbeats every
   second would generate that many `UPDATE` statements, just cheap ones. **Risk**: low at real internal-app
   scale (a handful of employees, one browser tab each); would need a real rate limiter if a misbehaving
   client ever polled aggressively.

## Real gaps found and fixed during this phase (residual risk: none — closed)

- Suspension/termination not blocking new login (only revoking existing sessions) — **fixed**.
- No last-usable-SUPER_ADMIN protection anywhere in the codebase — **fixed**.
- `EmployeePresenceSession` rows not revoked on suspension/termination (separate table from `StaffSession`)
  — **fixed**.
- Duplicate `employee_number` reservable by two concurrent open invitations — **fixed**.
- Preflight false-failure on a fresh, staff-less database — **fixed**.

## Carried forward from Phase 9.5A (unchanged, still real)

Commission/expense/management-note/report service layers remain schema-only (no service/route). Device
policy resolution remains unwired from the live licensing enforcement path (deliberate boundary, unchanged).
