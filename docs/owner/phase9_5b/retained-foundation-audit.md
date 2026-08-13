# Phase 9.5B — Retained Foundation Audit (required reading, done)

Real files read in full before writing any code:

- `docs/owner/phase9_5a/phase9-5a-final-decision.md` — Phase 9.5A verdict, real bugs, what's reserved.
- `docs/owner/phase9_5a/phase9-5a-gate-matrix.md` — final gate status.
- `docs/owner/phase9_5a/employee-domain-model.md` — `EmployeeProfile` schema, transitions, hard-delete
  prohibition (`ON DELETE RESTRICT`).
- `docs/owner/phase9_5a/employee-presence-contract.md` — `EmployeePresenceSession` schema, thresholds
  (<2min/2-15min/>15min), heartbeat contract, explicit "not attendance, not audited per-heartbeat".
- `docs/owner/phase9_5a/rbac-permission-matrix.md` — 125 permissions, grant table, SUPER_ADMIN-only set.
- `docs/owner/phase9_5a/sensitive-action-control-matrix.md` — `employees.terminate`/`employees.assign_role`
  are SUPER_ADMIN-only.
- `docs/owner/phase9_5a/mobile-authentication-adr.md` / `mobile-session-contract.md` — mobile
  login/refresh routes explicitly NOT routed in 9.5A; confirmed still out of 9.5B's own endpoint list
  (9.5B's Milestone 12 lists `/me`, `/presence/heartbeat`, `/me/sessions`, employee CRUD/lifecycle,
  roles — no `/auth/mobile/*` routes) — so mobile auth stays deferred, consistent.
- `docs/owner/phase9_5a/internal-api-contract.md` — `/api/operations/v1` namespace decision, standards
  (public UUIDs, pagination envelope, `Idempotency-Key`, `X-Correlation-Id`, Decimal-as-string).
- `docs/owner/phase9_5a/data-model.md` / `data-dictionary.md` — full new-table inventory (36 tables).
- `docs/owner/phase9_5a/audit-event-catalog.md` — 10 real + 18 reserved `action_code` values; none of
  the reserved ones are employee-lifecycle (those were already real from Milestone 22).
- `docs/owner/phase9_5a/migration-validation-report.md` — real migration bugs found/fixed (server_default,
  named constraints, forbidden-term collision, stale test DB).

Additionally read (not in the spec's list, but load-bearing for this phase): `owner/app/models/staff.py`
(full), `owner/app/staff/services.py` (full), `owner/app/staff/routes.py` (full), `owner/app/auth/routes.py`
(full, 424 lines) — the **existing, real, complete** `StaffInvitation`-based onboarding + login + MFA
enroll/verify + password-change + reauth flow. This is the real secure-setup mechanism the governing
spec asks for in Milestone 3/4 — see `phase9-5a-evidence-reuse-decision.md`.

## Real gap found during this audit (not in any prior phase's docs)

**No last-usable-SUPER_ADMIN protection exists anywhere in the codebase.** `owner/app/commercial_ops/
preflight.py` reports super-admin-without-MFA as a `WARNING` (read-only diagnostic), but neither
`disable_staff()` nor `assign_roles()` (`owner/app/staff/services.py`) blocks an action that would leave
zero usable `is_super_admin` accounts. `staff/routes.py::disable()` only checks `staff.id == actor.id`
(cannot disable self) — a second Super Admin could still disable the *only other* one, or a Super Admin
could `assign_roles()` themselves away from `SUPER_ADMIN` leaving none. This is exactly Non-Negotiable
Principle 11's concern, real and previously unenforced. Fixed in Milestone 2 (see
`employee-lifecycle-contract.md`).
