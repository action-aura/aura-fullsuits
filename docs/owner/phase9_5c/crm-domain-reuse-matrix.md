# Phase 9.5C — Milestone 1: CRM Domain Reuse Matrix

Decision for every domain concept: reuse as-is, extend additively, or
build new. No replacement models are created anywhere in this matrix.

| Concept | Decision | Rationale |
|---|---|---|
| `Lead` table/model | Reuse as-is | Already correct, already migrated, already tested. |
| `Customer` table/model | Reuse as-is | Pre-existing authoritative Customer system from earlier Owner phases; Phase 9.5A deliberately did not repurpose `lifecycle_status="LEAD"` as a second pipeline — confirmed still true. |
| Lead status vocabulary | Reuse as-is | `NEW/NOT_INTERESTED_NOW/POTENTIAL/FOLLOW_UP/UNDER_OBSERVATION/QUALIFIED/CONFIRMED/LOST/ARCHIVED` already the real stored values (`app/models/leads.py:22-25`) — this phase's spec's suggested vocabulary already matches exactly; nothing to rename. |
| `LeadStatusHistory` | Reuse as-is | Already append-only, already used by `change_lead_status()`. |
| `LeadAssignment` | Reuse as-is | Already the correct close/open time-windowed pattern. |
| `LeadInteraction` / `CustomerInteraction` | Extend: add service layer | Models correct and sufficient; only the service functions are missing. |
| `LeadFollowup` / `CustomerFollowup` | Extend: add service layer + derive `OVERDUE` | Models correct; per the spec's own instruction, `OVERDUE` is derived (`status == OPEN and due_at < now`), not a stored column — no migration needed for that. |
| `LeadProductInterest` | Extend: add service layer | Model correct, unused. |
| `LeadNote` / `CustomerNote` | Extend: additive `visibility` column on both | Both tables lack any visibility semantics today; every existing note is implicitly visible to anyone with record access. Adding a `visibility` column with a default of `ASSIGNED_RECORD_USERS` preserves current real-world behavior for all existing rows (non-breaking), while making `AUTHOR_ONLY`/`MANAGEMENT_ONLY` available going forward. |
| `CustomerLocation` | Extend: add input validation at the service boundary, add a route | Model, exactly-one-owner constraint, and capture service already correct; only bounds-checking and wiring are missing. |
| Lead-to-Customer conversion | Extend: `conversion.py`'s `convert()` | Add the missing `LeadStatusHistory` append and an optimistic-version check; everything else (idempotency, duplicate-check, transaction, lineage) is reused unchanged. |
| Duplicate detection | Extend: `find_duplicate_candidates()` | Add real phone-based matching (currently a dead parameter); keep the existing name/registration/email logic unchanged. |
| Ownership filter | Reuse as-is | `apply_ownership_filter()` is the single authoritative helper; every new query type (interactions, followups, contacts, notes, locations) must resolve its parent Lead/Customer through this same helper — never a second, parallel filter. |
| Lead contacts | Build new, minimal: `LeadContact` | No existing Lead-side contact model. `CustomerContact` cannot be reused directly (different parent FK), but its shape/pattern is copied exactly (same fields, same primary-flag rule) rather than inventing a new contact concept. |
| Permissions | Reuse as-is + extend | Existing `leads.*`/`customers.*` codes reused unchanged; new codes added only for the genuinely new operations (interactions, followups, contacts, location verify). |
| Audit action codes | Reuse the existing free-form pattern | No catalog file to migrate; new codes follow the exact `ENTITY_ACTION` naming already established (`LEAD_CREATED`, `CUSTOMER_LOCATION_CAPTURED`, etc.). |
| i18n | Reuse the existing Flask-Babel catalog | No second localization authority; every new label goes through the same `_()` + `pybabel extract/update/compile` pipeline already used by every prior Owner phase. |
| `/api/operations/v1` | Reuse the existing namespace | New CRM route modules registered under the same existing blueprint prefix, not a new API root. |

## No new duplicate authorities created

Confirmed: this phase adds **zero** new Customer tables, zero new
Customer-contact tables, zero new Customer-note tables, zero new
Customer-UUID systems, and zero new Customer-audit systems — satisfying
Non-Negotiable Domain Rule 2 verbatim.
