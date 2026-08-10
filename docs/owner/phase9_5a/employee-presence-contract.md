# Phase 9.5A Milestone 5 — Employee Presence Contract

## New: `EmployeePresenceSession` (`owner/app/models/employees.py`)

```
id (UUID PK), employee_profile_id (FK), staff_session_id (FK -> owner_staff_sessions — ties presence
  to a real authenticated session, never a standalone unauthenticated concept),
app_instance_id (client-generated opaque string, bounded length),
platform (WEB | ANDROID | IOS), app_version (nullable string),
last_seen_at (timestamptz), last_activity_at (timestamptz),
device_label (bounded-length string, e.g. "Bahaa's Pixel" — never a raw hardware identifier),
created_at, revoked_at (nullable)
```

Presence *state* (`ONLINE`/`RECENTLY_ACTIVE`/`OFFLINE`) is **derived at query time** from
`last_seen_at`, never stored — the spec's own thresholds (< 2min / 2-15min / > 15min) are a pure
function of `now() - last_seen_at`, computed identically wherever presence is displayed (dashboard,
employee list), never duplicated as a stored enum that could drift out of sync.

## Heartbeat endpoint (design; contract only, real route in a later phase per Milestone 22's scope)

`POST /api/operations/v1/presence/heartbeat` — authenticated, rate-limited (e.g. max 1 accepted update
per 30s per session — excess requests return `200` idempotently without writing a new row, not an
error), session-bound (the `staff_session_id` must match the caller's real active session — a heartbeat
can never be forged for another session), safe after logout (`revoked_at` set on logout invalidates
future heartbeats for that session with `AUTHENTICATION_REQUIRED`, matching the existing session
lifecycle), and does not itself keep an expired `StaffSession` alive — presence tracking reads session
validity from the existing `StaffSession` table, never grants it.

## Explicitly not stored (Non-Negotiable Principle 7 / this milestone's own list)

Continuous GPS, screen contents, customer data, keystrokes, or any behavioral telemetry beyond
"this session pinged at this time, from this platform." `EmployeePresenceSession` has no location
field at all — location capture (Milestone 8) is a wholly separate, explicit, lead/customer-scoped
concept, never derived from presence heartbeats.

## Not attendance, not audit

Presence is a real-time operational signal for dashboards only — it is never treated as an HR
attendance record and never substitutes for the real audit log (a `PRESENCE_HEARTBEAT` audit event is
deliberately NOT added to the Milestone 23 catalog — that volume of audit writes for a purely
operational, low-stakes signal would dilute the audit log's real security/financial signal value).
