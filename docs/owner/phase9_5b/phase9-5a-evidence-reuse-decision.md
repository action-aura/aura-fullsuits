# Phase 9.5B — Phase 9.5A Evidence Reuse Decision

## The single biggest reuse decision this phase makes

`owner/app/staff/services.py`'s `StaffInvitation`-based flow (`create_invitation` →
`accept_invitation_form`/`accept_invitation_submit` → `create_staff_from_invitation`) is **already** a
complete, real, tested, secure one-time-setup mechanism: cryptographically random token
(`generate_token()`), hashed at rest (`hash_token`, unique `token_hash` column), explicit `expires_at`
(3-day TTL), single-use (`accepted_at` checked in `_find_valid_invitation`), revocable
(`revoked_at`), never logged/never in an audit payload (`create_invitation`'s `audit_record()` call only
records `email`/`role_codes`, never the raw token), displayed once (`invitation_created.html`, rendered
directly from the `link` variable, never persisted anywhere else).

This satisfies essentially all of Milestone 3's "SECURE SETUP FLOW" requirements verbatim, already. Phase
9.5B does **not** build a second setup-token mechanism. Instead:

1. `StaffInvitation` gets one additive column, `employee_profile_draft` (nullable `Text`, JSON-encoded) —
   carries the employee-profile fields (employee number, full name, phone, job title, department, start
   date, manager, commission plan, MFA-required flag) captured at invitation time, materialized into a
   real `EmployeeProfile` row only on successful acceptance.
2. `create_staff_from_invitation()` is extended (not replaced) to also create the `EmployeeProfile` in the
   same transaction when a draft is present — using the exact same `EmployeeProfile` model and
   `create_employee_profile()`-shaped logic already real from Phase 9.5A Milestone 22
   (`owner/app/employees/services.py`).
3. `accept_invitation_submit()` gains the MFA-enrollment gate: when the created employee's profile or role
   requires MFA, the user is redirected into the **existing** `auth.mfa_enroll_form` flow (already real,
   already tested) before a full session exists — no new MFA mechanism.

## Why not build a parallel `/employees/new` → custom token flow

Would duplicate `StaffInvitation`, `hash_token`/`generate_token`, and the accept-invitation template/route
pair — directly against Non-Negotiable Principle 1 ("reuse the existing staff authentication authority")
and the session's own established discipline (Phase 9.5A's `duplication-risk-report.md` precedent).

## Where Phase 9.5A explicitly deferred and 9.5B now completes it

`EmployeeProfileService` (`suspend_employee`, `terminate_employee`, `touch_presence`) was built and tested
in Phase 9.5A Milestone 22, but with **no route, no UI, no lifecycle completeness** (no `reactivate`, no
`activate`, no `archive`) — Phase 9.5A's own explicit boundary was "service layer only, no routes yet".
Phase 9.5B adds the missing lifecycle functions to the *same* `owner/app/employees/services.py` module
(not a new one) and wires real routes/UI on top.
