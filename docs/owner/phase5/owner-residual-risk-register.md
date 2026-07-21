# Phase 5 -- Owner Residual Risk Register

| # | Item | Severity | Disposition |
|---|---|---|---|
| 1 | Single-process, DB-backed login rate limiter -- no distributed/multi-instance rate limiting | Low (acceptable at internal-staff scale, single-process deployment) | Deferred to a future phase if Owner is ever deployed multi-instance |
| 2 | Audit hash chain is application-level tamper-evidence, not WORM-storage-backed -- a DB administrator with raw SQL access could edit a row and recompute the chain forward | Low-Medium | Documented limitation (`owner-audit-design.md`); acceptable for an internal tool where DB access is already a high-trust boundary |
| 3 | No automated dependency vulnerability scanning configured for this app yet | Low | Every dependency is version-pinned; scanning is a CI-pipeline concern, no CI exists for Owner yet |
| 4 | MFA is optional (not enforced) for non-Super-Admin roles by default | Medium | Matches spec's own instruction ("configurable requirement for other sensitive roles"); any role can be flipped to MFA-required later without a schema change (`StaffUser.mfa_required` already exists per-user) |
| 5 | No email delivery for staff invitations -- links are copy-pasted manually by a Super Admin | Low | Explicit spec instruction this phase ("Do not integrate an email provider yet") |
| 6 | Pagination helper (`app/services/pagination.py`) exists but is not wired into every list route yet -- large tables (e.g. thousands of customers) would render unpaginated | Low (no realistic data volume yet) | Should be wired before any real pilot customer data volume is expected |
| 7 | No formal disaster-recovery rehearsal program (a single manual restore-recovers-data test was performed, not a scheduled drill) | Low | Consistent with a Phase-5 foundation; a real DR drill program belongs in a later operational-maturity phase |
| 8 | `owner_control_center/` (pre-existing empty scaffold, 0 files) left in place, unused, alongside the new `owner/` tree | Cosmetic | Harmless (git doesn't even track empty directories); noted in `owner-foundation-scope.md` for anyone who finds it confusing later |
| 9 | One transient, non-reproduced test-suite flake was observed once during development (a schema-drift assertion failed on a single full-suite run, then passed cleanly on 2 subsequent full re-runs with the exact same code) | Low | Root cause not conclusively identified; re-verified passing twice after the fact; flagged here rather than silently ignored in case it recurs |
| 10 | No CI pipeline runs `owner/tests/` automatically on every change | Medium | Manual `pytest` invocation only this phase; wiring CI is straightforward future work, not attempted here to stay within Phase 5's scope |

## Explicitly NOT risks (by design, verified)
Owner has zero connection to Retail/Clinic data (structurally proven, not just claimed -- `test_data_boundary.py`). No plaintext secret of any kind (password, MFA secret, license key, session/invitation token) is stored anywhere. No external API route is reachable by default. No fake/demo data exists in the seeded dev database.
