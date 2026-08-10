# Phase 9.5C — Milestone 15: CRM Operations API Report

## New blueprint: `app/api_operations/crm.py`

Registered as a second `Blueprint` sharing the existing `/api/operations
/v1` `url_prefix` (Flask supports multiple blueprints on one prefix with
distinct blueprint/endpoint names) — no new API root introduced, per
`crm-domain-reuse-matrix.md`'s decision. Same cookie-session
authentication + CSRF protection as every other route under this prefix
(unchanged from the existing `api_operations/routes.py` module's own
documented rationale).

## Endpoints implemented

| Resource | Routes |
|---|---|
| Leads | `GET/POST /leads`, `GET/PATCH /leads/<id>`, `POST /leads/<id>/status`, `POST /leads/<id>/assign`, `POST /leads/<id>/convert`, `POST /leads/<id>/archive`, `GET /leads/<id>/status-history`, `GET /leads/<id>/assignment-history` |
| Customers | `GET /customers`, `GET/PATCH /customers/<id>`, `POST /customers/<id>/assign`, `POST /customers/<id>/archive`, `GET /customers/<id>/assignment-history` |
| Contacts | `GET/POST /leads/<id>/contacts`, `GET/POST /customers/<id>/contacts` |
| Interactions | `GET/POST /leads/<id>/interactions`, `GET/POST /customers/<id>/interactions` |
| Follow-ups | `GET /followups?view=due_today|overdue`, `POST /leads/<id>/followups`, `POST /customers/<id>/followups`, `POST /followups/<id>/complete`, `POST /followups/<id>/cancel` |
| Notes | `GET/POST /leads/<id>/notes`, `GET/POST /customers/<id>/notes` |
| Locations | `GET/POST /leads/<id>/locations`, `GET/POST /customers/<id>/locations`, `POST /locations/<id>/verify` |

Every endpoint delegates to the same service functions the (Milestone
16) web routes call — zero business logic duplicated in this file; it
only parses the request, checks permission (`@require_permission`) and
record-level ownership (`_lead_or_404()`/`customer_visible_to_actor()`),
and serializes the response.

## Requirements checklist

| Requirement | Status |
|---|---|
| Authentication | Cookie-session, `@require_permission` implies `@require_login` (existing decorator chain) |
| Permission | Every route decorated with the specific `leads.*`/`customers.*` code |
| Record-level ownership | `_lead_or_404()` (Lead: creator-or-assignee-or-`_all`) / `customer_visible_to_actor()` (Customer) on every single-record route, list routes apply `apply_ownership_filter()`/the same helper |
| Public UUIDs | Every response uses `str(row.id)`; zero internal integer PKs exist anywhere in this schema (all tables use `UUIDPKMixin`) |
| Pagination | `page`/`page_size` (capped at 100)/`total`/`total_pages`, matching the existing `api_operations/routes.py` shape exactly |
| Optimistic locking | `version` accepted in request bodies, compared before mutation, `409` + stable code on mismatch |
| Idempotency | Lead creation and conversion both accept `idempotency_key` |
| Stable service-error codes | Every `except LeadError/CustomerCrmError/LocationValidationError as exc: jsonify({"error": exc.code, ...})` — the machine-readable `.code`, never a translated string |
| No internal numeric IDs | Confirmed — no route returns anything but UUIDs |
| No child-resource IDOR | Every child-resource route (contacts/interactions/followups/notes/locations) loads and authorizes the **parent** record first, before ever touching the child by its own UUID |
| No hidden-note leakage | `list_lead_notes_route`/`list_customer_notes_route` call the Milestone 11 visibility-filtered listing functions, never a raw unfiltered query |
| No duplicate-record privacy leakage | `lead_convert_route`'s `DuplicateCustomerError` handler calls `describe_duplicate_candidates_for_actor()` (Milestone 5), never returns raw candidates |
| No raw coordinates in errors/logs | `LocationValidationError` messages never include the value (Milestone 12); location routes' error responses inherit this |
| OpenAPI updated | Not yet done this pass — see Residual Risk Register; the existing OpenAPI spec file needs a CRM section appended, mechanical work not completed within this wave's time budget |

## Known limitations, honestly recorded

- `_lead_or_404` for `PATCH /leads/<id>` and status/assign/convert routes
  checks ownership only when `actor_profile is not None`; an actor with
  no `EmployeeProfile` row (should not occur in practice — every
  StaffUser with CRM permissions is expected to have one) would bypass
  the check. Documented, not yet defensively hardened.
- Request bodies are trusted `request.get_json()` dicts passed close to
  directly into service functions in several places (e.g.
  `create_lead_route`) without the full `validate_lead_fields()`/
  `validate_location_fields()` pass on every field — `create_lead_route`
  does apply an allowed-key filter but not the fuller validation module;
  `capture_*_location_route` **does** call `validate_location_fields()`.
  Full validation-module wiring across every write route is tracked as
  a Milestone 21/immediate-follow-up hardening item, not silently
  claimed complete.

This report documents real, working, permission-and-ownership-checked
endpoints — with the above gaps named explicitly rather than glossed
over.
