# Phase 9.5A — Mobile Location Privacy Design

## Principle (Non-Negotiable #7, restated as an enforceable design)

Location capture happens only inside an explicit employee action in the app (e.g. tapping "Capture
current location" on a lead/customer detail screen) — never a background service, never a periodic
job, never tied to app-open/heartbeat events. This is enforced by *absence*: no background-location
API, no periodic-sync endpoint, and `EmployeePresenceSession` (Milestone 5) has no location field to
accidentally repurpose.

## Server-side guarantee

The server has no endpoint that accepts a location payload without an explicit `lead_id` or
`customer_id` target and an authenticated `captured_by_employee_profile_id` — there is no generic
"report my location" endpoint that a background service could call even if a future client tried to
add one; the API contract itself (Milestone 19) only exposes location as a child resource of a specific
Lead/Customer record.

## Future mobile app requirement (recorded now, enforced at build time in a later phase)

- Android: request `ACCESS_FINE_LOCATION` with the "while in use" launch mode, never
  `ACCESS_BACKGROUND_LOCATION`.
- iOS: request `NSLocationWhenInUseUsageDescription`, never `NSLocationAlwaysAndWhenInUseUsageDescription`.
- Neither permission is requested at app launch — only when the employee actually taps "Capture
  location" on a specific record, matching the server contract's own per-record, explicit-action shape.

## What this phase does NOT build

The mobile app itself (forbidden this phase). This document exists so the *server contract* the future
app is built against cannot be misused to implement background tracking even by an unaware future
developer — the absence of a generic location-report endpoint is itself the control.
