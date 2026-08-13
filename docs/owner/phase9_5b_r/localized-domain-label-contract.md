# Phase 9.5B-R Milestone 11 — Localized Domain Label Contract

`owner/app/i18n_labels.py` — the one centralized module (Non-Negotiable Principle 1/4). Every function
takes a stable internal code and returns a localized display string; internal code is never mutated,
compared case-insensitively, or otherwise treated as anything but an opaque key.

| Function | Internal codes handled | Example (en) | Example (ar) |
|---|---|---|---|
| `employment_status_label()` | PENDING/ACTIVE/SUSPENDED/TERMINATED/ARCHIVED | Active | نشط |
| `presence_label()` | ONLINE/RECENTLY_ACTIVE/OFFLINE | Recently Active | نشط مؤخرًا |
| `role_label()` | SUPER_ADMIN/SALES/SUPPORT/FINANCE/VIEWER | Super Administrator | مدير النظام الأعلى |
| `account_status_label()` | (is_active, disabled) tuple | Active / Disabled | نشط / معطّل |
| `session_status_label()` | revoked boolean | Active / Revoked | نشط / مُلغاة |
| `mfa_status_label()` | (enrolled, required) tuple | Enrolled / Required / Not enrolled | مُفعّلة / مطلوبة / غير مُفعّلة |
| `invitation_status_label()` | expired boolean | Open / Expired | سارية / منتهية الصلاحية |
| `audit_action_label()` | 20 real `AuditLog.action_code` values | Employee suspended | تم إيقاف الموظف مؤقتًا |

## Safe fallback (real, tested)

Every function's internal dict uses `.get(code, code)` — an unrecognized/future code renders as the raw
code itself, never raises, never renders blank. `test_phase9_5b_r_domain_labels.py::
test_unknown_code_falls_back_safely` asserts this directly for `employment_status_label`/`presence_label`/
`role_label`/`audit_action_label` with a deliberately fake code.

## Stored values never touched

None of these functions write to the database, and none of `EmployeeProfile.employment_status`/
`Role.code`/`AuditLog.action_code`/`presence_state()`'s return value are ever replaced with a translated
string anywhere in the codebase — confirmed by grep: every write site for these columns/return values
(`owner/app/employees/services.py`, `owner/app/employees/presence.py`) uses only the fixed English
constant, unchanged from Phase 9.5A/9.5B.
