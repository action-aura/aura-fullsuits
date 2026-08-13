# Phase 9.5A — Commission RBAC Rules

| Action | Permission | Granted to (default seed) |
|---|---|---|
| View own commission ledger | `commissions.view_own` | SALES |
| View all employees' commissions | `commissions.view_all` | FINANCE, SUPER_ADMIN |
| Trigger eligibility evaluation (system-invoked, not a direct user action normally) | `commissions.calculate` | FINANCE, SUPER_ADMIN (for manual re-run only — normal flow is automatic on payment confirmation) |
| Approve a commission entry | `commissions.approve` | FINANCE, SUPER_ADMIN — **never** SALES |
| Approve a payout batch | `commissions.pay` | SUPER_ADMIN only (money actually leaving) |
| Reverse a commission entry | `commissions.reverse` | FINANCE, SUPER_ADMIN |
| Change an employee's commission plan | `employees.manage_commission_plan` | SUPER_ADMIN only |

## Self-approval block (code-level, not just permission-level)

Even a `SUPER_ADMIN` account (Bahaa or Awab) cannot approve a commission ledger entry whose
`employee_profile_id` resolves to their own `StaffUser` — this is checked in the service layer
regardless of role, since a permission grant alone cannot express "you may approve anyone's commission
except your own." (Real scenario this guards: if a management account is ever also assigned as a
SALES-style employee profile for their own direct deals — not the current plan, but the schema doesn't
prevent it, so the check is defensive regardless.)
