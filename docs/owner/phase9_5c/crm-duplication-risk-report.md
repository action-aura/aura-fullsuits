# Phase 9.5C — Milestone 1: Duplication Risk Report

Real risks identified and their avoidance decision, before any new code
was written.

| Risk | Where it could occur | Avoidance decision |
|---|---|---|
| Second Customer table | Building Lead-to-Customer conversion | Reuse `app/models/customers.py:Customer` unchanged; conversion only ever creates/links rows in the existing table. |
| Second Customer-contact authority | Building Lead contacts | `LeadContact` is a genuinely new table (Leads have no existing contact concept), but copies `CustomerContact`'s exact field shape/primary-flag rule rather than inventing new semantics — not a second *authority* for the same concept, since Lead contacts and Customer contacts are different concepts (pre-conversion prospect contact vs. confirmed customer contact) linked by conversion carrying the data across, not two competing systems for the same data. |
| Second Customer-note authority | Building note visibility | Extending the existing `CustomerNote` table additively (new `visibility` column); not creating a parallel "CustomerNoteV2" or similar. |
| Second Customer UUID system | Anywhere records are referenced externally | `Customer.id` (existing `UUIDPKMixin`) remains the only public identifier; no new customer-identifier scheme introduced. |
| Second Customer-audit system | Building CRM audit events | Reuses `app/audit/services.py:record()` unchanged; new action codes follow the existing free-form convention, no new audit table. |
| Second Lead ownership filter | Building interaction/followup/contact/location queries | All new query types resolve their parent Lead/Customer through the existing `apply_ownership_filter()` before applying any child-specific filter — never a second, independently-written ownership check (this is exactly the IDOR-risk pattern the Phase 9.5A docs already warned against). |
| Second duplicate-detection engine | Building duplicate warnings for the Lead-creation flow (Milestone 5) | Extends `find_duplicate_candidates()` (adds real phone matching); does not introduce a separate "lead duplicate checker." |
| Second localization authority | Building CRM-specific labels | Uses the existing Flask-Babel `_()` + `translations/{en,ar}/LC_MESSAGES/messages.po` pipeline; no CRM-specific string table. |
| Second API root | Building CRM endpoints | Registered under the already-existing `/api/operations/v1` blueprint prefix; no new API namespace created. |
| Overlapping status vocabulary | Lead status vs. `Customer.lifecycle_status` | Confirmed distinct and non-overlapping: `Lead.status` values (`NEW`...`ARCHIVED`) never appear as `Customer.lifecycle_status` values (`LEAD`/`ACTIVE`/etc. — a Customer's own, separate lifecycle); conversion explicitly maps `Lead.status="CONFIRMED"` to `Customer.lifecycle_status="ACTIVE"`, not a shared enum. |

## Conclusion

No duplication risk requires deviation from the reuse decisions recorded
in `crm-domain-reuse-matrix.md`. Every new table this phase introduces
(`LeadContact`, and the additive `visibility` columns) is a genuinely new
concept or a non-breaking extension of an existing one, never a second
authority for something that already exists.
