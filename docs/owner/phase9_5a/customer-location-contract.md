# Phase 9.5A Milestone 8 — Customer Location Contract

## New: `customer_locations` (`owner/app/models/leads.py`, shared table for Lead and Customer)

```
id (UUID PK),
lead_id (FK, nullable), customer_id (FK, nullable),
  CHECK (lead_id IS NOT NULL) != (customer_id IS NOT NULL)  -- exactly one, never both/neither
latitude (Numeric(9,6)), longitude (Numeric(9,6)), accuracy_meters (Numeric(8,2), nullable),
captured_at (timestamptz), captured_by_employee_profile_id (FK, not nullable),
source (GPS | NETWORK | MANUAL | IMPORTED),
manual_address (text, nullable), reverse_geocoded_address (text, nullable — populated by a later phase
  when a geocoding integration exists; column reserved now so the API contract is stable),
verified (boolean, default false), verification_method (nullable string),
updated_at, version (optimistic lock), archived_at (nullable)
```

## Requirements (all satisfied by design)

- Explicit user action only — the API contract has no "background sync" endpoint, only
  `POST .../locations` triggered by an explicit employee action in a future mobile client.
- While-in-use permission only — a mobile-architecture note (not enforceable server-side, an OS-level
  permission model concern), recorded here so the future mobile app's permission request matches this
  contract: request `ACCESS_FINE_LOCATION`/`CoreLocation` "while using the app," never "always."
- No background tracking field exists on the model at all — there is nothing to disable, because
  nothing continuous was ever built.
- Accuracy stored — `accuracy_meters`, always populated when `source=GPS`/`NETWORK`.
- Manual entry always available — `source=MANUAL` with `manual_address` set, no coordinates required
  (nullable lat/long when source is MANUAL and no coordinates were ever resolved).
- Every change audited — `CUSTOMER_LOCATION_CAPTURED`/`CUSTOMER_LOCATION_CORRECTED` (Milestone 23).
- Employee cannot alter another employee's location capture without permission —
  `customers.verify_location` (management/support only) is required to edit a location once
  `captured_by_employee_profile_id` differs from the actor; the original capturing employee can always
  edit their own within a bounded window (design choice, mirrors typical "edit your own note" patterns
  elsewhere in this codebase) — exact window left to Milestone 22's service implementation, not fixed
  in this design doc.
- Historical captures preserved — no location row is ever hard-deleted; `archived_at` supersedes
  instead, keeping a full history where business policy requires it (matches `Customer.archived_at`'s
  own precedent).
- No location in licensing API traffic — the `/api/licensing/v1` prefix is untouched by this phase;
  `customer_locations` has no relationship to `Installation`/`License`/assertion payloads at all.
- No continuous location history requirement — this table supports as many discrete captures as real
  business need dictates (e.g. once at lead qualification, once at contract signing), never a periodic
  feed.

## Honesty requirement (explicit, spec's own wording)

A location is never labeled "GPS verified" merely because the OS reported `source=GPS` — `verified`
is a distinct, separately-set boolean requiring an explicit `verification_method` (e.g. a support
staff member cross-checking the address), never implied by the capture source alone. An approximate
`NETWORK`-sourced position with a large `accuracy_meters` value is displayed with its real accuracy,
never silently rounded into false precision.
