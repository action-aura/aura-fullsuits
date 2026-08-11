# Phase 9.5C — Milestone 26: CRM Preflight

## New `_check_crm_domain_integrity()` in `app/commercial_ops/preflight.py`

Same defense-in-depth spirit as the existing `_check_employee_domain_
integrity()` (Phase 9.5B) — every condition checked here should already
be structurally impossible via a real service-layer invariant; these
checks exist to catch a bypass (a direct DB write, a future migration
bug) before a real user hits it, not to replace the invariant itself.

| Check | What it verifies |
|---|---|
| `no_invalid_lead_status` | Every `Lead.status` is one of the 9 canonical `LEAD_STATUSES` values |
| `no_duplicate_primary_lead_contact` / `no_duplicate_primary_customer_contact` | No parent record has more than one non-archived contact marked `is_primary` (the Milestone 8 demotion-transaction invariant) |
| `no_invalid_lead_note_visibility` / `no_invalid_customer_note_visibility` | Every note's `visibility` is one of the 3 canonical `NOTE_VISIBILITIES` values |
| `no_orphan_locations` | Every `CustomerLocation` row references exactly one parent (the exact-one-owner CHECK constraint's own invariant, double-checked at the application layer) |

Wired into `run_preflight()` as a blocking check (`blocking_ok &=
_check_crm_domain_integrity(checks)`), alongside every other blocking
check — a CRM data-integrity violation now fails `flask commercial
preflight` and `/health/ready` exactly like a signing-key or i18n-catalog
problem would.

## Real gap this milestone's investigation found and fixed (not merely checked)

While building this check, re-auditing every CRM route for the exact
"parent-ownership check present?" question this preflight check's
philosophy implies surfaced a **real, live IDOR** that had shipped in
the Milestones 15-17 commits: the `crm_shared` **web** routes
(`/followups/<id>/complete`, `/followups/<id>/cancel`,
`/locations/<id>/verify`) had **no parent-ownership check at all** —
only the API blueprint's equivalents had been hardened
(`_resolve_followup_with_parent_check()`, added during Milestone 15).
Any employee holding `leads.update_own` could complete or cancel
**any** other employee's follow-up by UUID through the web route; any
holder of `customers.verify_location` could verify **any** location by
UUID regardless of ownership (currently only reachable via the
`SUPER_ADMIN` wildcard in practice, since the permission isn't seeded to
any other role — but architecturally wrong regardless).

Fixed in both the web (`app/leads/routes.py`) and API
(`app/api_operations/crm.py`) blueprints, with new tests
(`test_employee_a_cannot_complete_employee_b_followup_via_web_route`,
`test_holder_of_verify_location_without_view_all_cannot_verify_unowned_
location` — the latter constructs a genuine non-global synthetic role
holding only `customers.verify_location` to prove the ownership check
is real, not just a permission gate with nothing behind it).

## Existing preflight checks re-confirmed unaffected

`flask commercial preflight` re-run against `aura_owner_dev` after all
Phase 9.5C changes: all pre-existing checks (`all_permissions_seeded`,
`role_permissions_synced`, `i18n_catalog_complete_en`/`ar`, `no_
duplicate_employee_numbers`, `at_least_one_usable_super_admin`, etc.)
remain `OK`, plus the 6 new `_check_crm_domain_integrity` checks.
