# Phase 9.5B Milestone 4 — First Login and MFA Flow (real code)

## Reused, not rebuilt

`owner/app/auth/routes.py`'s login (`login_submit`), MFA verify (`mfa_verify_submit`), MFA enroll
(`mfa_enroll_form`/`mfa_enroll_submit`) are **entirely unchanged** — real, pre-existing (Phase 4/6), and
already implement every one of Milestone 4's numbered requirements: unexpired/unused/eligible-account
checks (`_find_valid_invitation`, `authenticate()`'s `is_active`/`disabled_at` gate), password policy
(`hash_password`/`PasswordPolicyError`), MFA enrollment + recovery codes
(`generate_recovery_codes`/`hash_recovery_code`, shown once via `session["_just_generated_recovery_codes"]`,
never logged), replay prevention (single-use invitation token, single-use recovery codes), generic error
messages that never reveal whether an account exists (`login_submit`'s comment: "Deliberately generic").

## The one real integration point this phase adds

`owner/app/auth/session.py::create_session()` — the single function every login-completion path already
calls (plain login, MFA verify, first-time forced MFA enrollment, post-password-change re-login) — now
also calls `_activate_pending_employee_profile()`: if the logging-in `StaffUser` has an `EmployeeProfile`
still in `PENDING`, it transitions to `ACTIVE` via the real `activate_employee()` service function
(Milestone 2). This is why `EmployeeProfile.employment_status` stays `PENDING` through account-setup
(password creation) and only becomes `ACTIVE` at the moment of the employee's first genuine authenticated
session — exactly matching the spec's own step ordering (setup completes → MFA enrolls if required →
*then* activate).

## Real, tested proof

- `test_first_login_activates_pending_profile`: profile is `PENDING` immediately after account setup,
  `ACTIVE` only after the first successful login (no-MFA path).
- `test_mfa_required_employee_forced_through_enrollment_before_full_access`: an `mfa_required=True`
  employee's first login redirects to `/auth/mfa-enroll` (never reaches `create_session()` yet) and the
  profile remains `PENDING` until enrollment actually completes — proving MFA is a real gate, not
  cosmetic.

## What this milestone deliberately does not touch

Recovery-code display/regeneration, reauth (`/auth/reauth`), password-change — all pre-existing, all
unchanged, all still covered by the pre-existing test suite (28/28 `test_security.py` + `test_auth.py`
passing after this phase's changes, confirmed).
