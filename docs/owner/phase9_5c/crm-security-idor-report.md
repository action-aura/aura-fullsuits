# Phase 9.5C — Milestone 21: Security and IDOR Completion Report

## New HTTP-layer tests (`tests/test_phase9_5c_api_idor.py`)

Unlike the earlier service-layer tests (which call `apply_ownership_
filter()`/`customer_visible_to_actor()` directly), these tests go
through the real Flask test client — proving the ownership checks are
actually *wired into* the routes, not merely correct as standalone
functions:

| Test | Proves |
|---|---|
| `test_employee_a_cannot_read_employee_b_lead_via_api` | `GET /leads/<id>` returns 404 (not 403 — no existence confirmation) for an unauthorized Lead |
| `test_employee_a_cannot_read_employee_b_lead_contacts_via_api` | Child-resource IDOR: knowing the real parent Lead UUID still doesn't unlock its contacts |
| `test_employee_a_cannot_complete_employee_b_followup_by_uuid` | The Milestone 15 follow-up parent-check fix actually blocks the HTTP request, **and** the database row is confirmed genuinely unmutated after the rejected request (not merely a rejected response with a silent side effect) |
| `test_employee_a_cannot_verify_employee_b_customer_location` | Permission-layer rejection (403) confirmed present as the first line of defense before ownership even matters |
| `test_management_view_all_can_read_any_lead` | The `_all` bypass genuinely works end-to-end over HTTP, not just in the service layer |
| `test_lead_list_api_does_not_leak_other_employees_leads_in_total_count` | `total` in the paginated response reflects only the actor's visible set — real proof against count-based enumeration |
| `test_idempotency_key_reuse_across_different_leads_is_rejected_via_api` | Milestone 13's conflict detection works through the real HTTP conversion endpoint, returning the documented `IDEMPOTENCY_CONFLICT` code at `409` |
| `test_xss_payload_in_lead_note_is_escaped_on_render` | Extends `test_security.py`'s existing Customer-name XSS proof to the new `LeadNote.body` field via a real rendered-page assertion (not template-source inspection) |

## Coverage against the governing spec's Milestone 21 checklist

| Area | Status |
|---|---|
| Employee isolation (Lead/Customer, child-resource UUID, no count leakage) | Covered — combination of `test_phase9_5a_ownership_idor.py` (service-layer) + `test_phase9_5c_api_idor.py` (HTTP-layer) |
| Lead lifecycle (valid/invalid transitions, stale version, converted-Lead protection) | Covered — `test_phase9_5a_lead_services.py`, `test_phase9_5c_conversion_hardening.py` |
| Assignments (authorization, inactive-destination rejection, history, creator preservation) | Covered — same files + `crm-record-ownership-contract.md`'s test references |
| Duplicates (exact match, inaccessible-record masking, override) | Covered — `test_phase9_5c_duplicate_detection.py` |
| Notes (all 3 visibility modes, hidden-count non-leakage) | Covered — `test_phase9_5c_note_visibility.py` |
| Locations (coordinate bounds, NaN/Infinity, negative accuracy, verification reason) | Covered — `test_phase9_5c_location.py` |
| Conversion (eligible/ineligible, idempotency, rollback, no orphan Customer, no commercial documents) | Covered — `test_phase9_5c_conversion_hardening.py` |
| API (public UUIDs, stable codes, pagination, optimistic locking, idempotency) | Covered — `test_phase9_5c_api_idor.py` + inspection of `api_operations/crm.py` |

## Real, named gaps not covered by an automated test this wave

- **Status/assignment-history route-level access** (`GET /leads/<id>/
  status-history`/`assignment-history`) is gated by the same `_lead_or_
  404` check as the parent detail route (verified by code inspection),
  but no dedicated HTTP test exercises an unauthorized actor hitting
  these two specific endpoints.
- **Concurrent completion of the same follow-up** — the idempotent-
  completion design (`test_followup_lifecycle_open_complete_idempotent`)
  proves sequential double-completion is safe, but no test simulates
  genuinely concurrent requests (two threads/processes racing).

These are named, not hidden, per the same discipline established in
Phase 9.5B-R3.
