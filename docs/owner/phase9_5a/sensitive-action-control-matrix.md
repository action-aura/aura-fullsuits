# Phase 9.5A — Sensitive Action Control Matrix

| Action | Permission (necessary) | Additional control (necessary, not sufficient by permission alone) |
|---|---|---|
| Approve own commission | `commissions.approve` | Code-level self-approval block regardless of role (`commission-rbac-rules.md`) |
| Change device policy | `device_policy.manage` | SUPER_ADMIN only (not granted below); `DEVICE_POLICY_OVERRIDE_CREATED` audit |
| Override a catalog price | `pricing.override` | SUPER_ADMIN only; both catalog and overridden price recorded in the audit payload |
| Terminate an employee | `employees.terminate` | SUPER_ADMIN only; triggers real session revocation (existing mechanism) |
| Assign employee roles | `employees.assign_role` | SUPER_ADMIN only, same as the existing `staff.assign_roles` precedent |
| Approve a commission payout batch | `commissions.pay` | SUPER_ADMIN only — real money leaving |
| Revoke another employee's session | `security_sessions.revoke` | SUPER_ADMIN only |
| Regenerate a daily snapshot | `reports.regenerate_daily` | Bounded to a 48h window (`daily-reporting-contract.md`) — not an unbounded historical rewrite tool |
| Manage shared management notes | `management_notes.manage` | SUPER_ADMIN only (matches "management notes" being inherently a management-only concept) |

## Recent-auth / MFA requirement carried forward, not reinvented

Every action in this table inherits the existing `RECENT_AUTH_WINDOW_SECONDS` (10 min) and
`mfa_required` enforcement already real for `SUPER_ADMIN` accounts (Phase 4/6/8) — no new
recent-auth/MFA mechanism is built this phase; sensitive Phase 9.5A actions simply sit behind
permissions that, for every role capable of holding them, already require MFA at login.
