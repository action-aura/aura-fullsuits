# Phase 9.5B-R Milestone 19 — Local Bilingual Functional Validation

Real end-to-end test (`owner/tests/test_phase9_5b_r_bilingual_e2e.py::
test_full_arabic_employee_lifecycle_scenario`), real HTTP requests through the actual Flask test client,
synthetic Arabic employee-name data (`سارة أحمد الزهراني`), following the governing spec's own 15-step
Arabic flow list.

## What the test actually proves, step by step

1. `/auth/login` in Arabic — `lang="ar" dir="rtl"` confirmed directly on the response.
2. Real MFA login for a Super Admin.
3. Employee dashboard renders (`لوحة تحكم الموظفين`).
4. Employee list renders (empty-state message correctly localized).
5. Real employee creation with an Arabic full name, Arabic invitation-created confirmation text.
6. Real invitation acceptance in Arabic (`إعداد حساب Aura Owner الخاص بك`).
7. Real first login forced into Arabic MFA enrollment (`تفعيل المصادقة متعددة العوامل`), real TOTP secret
   extracted from the Arabic-labeled page and used to generate a real valid code.
8. Self-profile renders with the real Arabic name; the employee number stays unmangled/LTR.
9. Real presence heartbeat via the real API, returns raw `"ONLINE"` (never translated) even under the
   Arabic session.
10. Management (switched to Arabic) sees the same employee, the real localized `نشط` (Active) badge.
11. Real Arabic `confirm()` dialog text present in the rendered `data-confirm` attribute; real suspension
    call; real re-login attempt correctly rejected (`401`) regardless of locale.
12. Real audit timeline shows the localized `تم إيقاف الموظف مؤقتًا` (Employee suspended) label; the
    underlying stored `AuditLog.action_code` is directly asserted to still be the real English
    `"EMPLOYEE_SUSPENDED"` identifier.
13. The employee number renders wrapped in the real `<bdi dir="ltr">` bidi-safety markup.
14. Switch back to English — confirmed `lang="en" dir="ltr"` on the next anonymous page load.
15. Preference persistence confirmed directly against the database (`StaffUser.locale == "en"`).

## Confirmed unaffected by language switching (directly asserted in the test)

Employee `employment_status` (stays `"ACTIVE"`/`"TERMINATED"` etc. in the database and in every API
response), the `AuditLog.action_code` value, the API's `presence` field, and the account's real identity/
session validity (a follow-up authenticated request succeeds after the switch).

## Real bug found via this exact test during development

An earlier draft of this test failed twice on a session-state assumption (the shared test client still
held the admin's authenticated session when the test expected to see the anonymous login form) — both
instances fixed by adding explicit logout steps at the right points, matching the same lesson already
documented in Phase 9.5B's own `test_full_employee_lifecycle_scenario`. Recorded honestly as a real
debugging step, not hidden.
