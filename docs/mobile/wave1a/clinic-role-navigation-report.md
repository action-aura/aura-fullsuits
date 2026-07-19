# Clinic — Role-Aware Navigation (Wave 1A, Part K)

## Authoritative source read first
Per instruction, `docs/security/clinic-rbac-matrix.md` was read before any implementation. Its real, verified (not assumed) binary RBAC model: Admin bypasses everything; `clinic_role=="doctor"` gates exactly 5 write actions (update visit diagnosis/treatment, add clinical note, add/delete follow-up, create prescription); everything else is open to any authenticated clinic staff. Admin-only user management (`/api/admin/*`) exists in the backend.

## Finding: none of the gated actions have any Android UI at all
A direct grep across the Android source confirmed: the 5 doctor-only write actions have zero Android UI (`PrescriptionsScreen.kt` is read-only/list-only, for example), and admin user-management has no Android screen either.

## Decision
Rather than fabricate UI gates for actions that don't exist in the app (which would misrepresent what this wave actually changed), built correct, tested role-derivation **infrastructure** for when such actions are eventually added:
- `ClinicRole` enum (ADMIN/DOCTOR/SECRETARY)
- `deriveClinicRole(user: User?): ClinicRole` — pure function; admin role wins; else `clinic_role=="doctor"`; else secretary. Never defaults to Admin on ambiguous/null data.
- `ClinicSession` object with observable `role` state, updated at login and on session restore, reset on logout.

This is explicitly UI-only exposure reduction — it does not replace or weaken backend authorization, which independently re-checks every request.

## Tests
7 unit tests in `ClinicSessionTest.kt`: admin wins regardless of `clinic_role`; doctor/secretary/blank/null role derivation; `reset()` behavior; `canDoDoctorOnlyActions` correctness.

## Device verification
No gateable UI exists to visually verify on-device (matches the finding above); verified instead via the unit test suite and by confirming login correctly populates `ClinicSession` (role infrastructure wired into `LoginScreen.kt` and `AppRoot.kt`'s session-restore path).

## Result
PASS — infrastructure built and tested; no misleading UI gates added for non-existent actions.
