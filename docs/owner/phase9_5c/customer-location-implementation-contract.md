# Phase 9.5C — Milestone 12: Location Capture Implementation Contract

## Server-side validation (new: `app.leads.location.validate_location_fields`)

| Rule | Enforcement |
|---|---|
| Latitude -90..90, finite | `_to_decimal()` rejects non-numeric/NaN/Infinity before the range check; `INVALID_LATITUDE` |
| Longitude -180..180, finite | Same pattern; `INVALID_LONGITUDE` |
| Accuracy >= 0, finite | `INVALID_ACCURACY` |
| Known source only | Existing `LOCATION_SOURCES = ("GPS", "NETWORK", "MANUAL", "IMPORTED")` (Phase 9.5A vocabulary, reused unchanged — not renamed to the spec's suggested `BROWSER_GEOLOCATION`/`MANUAL`/`IMPORTED`/`OTHER` set, per the governing spec's own "do not force existing values to match this prompt" instruction); `INVALID_SOURCE` |
| Bounded client/server clock skew | `client_captured_at` rejected if more than 1 hour from server `now`; `TIMESTAMP_TOO_FAR` |
| Bounded manual address length | 2000 chars; `MANUAL_ADDRESS_TOO_LONG` |

All 7 rules verified by `tests/test_phase9_5c_location.py` (9 tests),
including explicit `math.nan`/`math.inf`/`-math.inf` rejection cases.

## Never claims "verified GPS" by default

`CustomerLocation.verified` already defaulted `False` at the model level
(Phase 9.5A) — unchanged. A browser-reported `source="GPS"` location is
stored as **unverified** until an authorized management action changes
that explicitly.

## Verification — new: `app.leads.location.verify_location`

Real gap closed: the existing `verified`/`verification_method` columns
recorded *that* something was verified but never *who*, *when*, or *why*.
Three additive columns this milestone
(`verified_by_employee_profile_id`, `verified_at`,
`verification_reason` — migration `dd408948bf89`) plus the new function:

- Requires a non-empty `reason` (`REASON_REQUIRED_FOR_VERIFY` otherwise)
  — verified by `test_verify_location_requires_reason_and_records_actor_
  not_coordinates`.
- Sets `verified=True`, `verification_method="MANAGEMENT_REVIEW"`,
  the verifying actor, and a timestamp.
- Audits `LOCATION_VERIFIED` with the location's UUID and `{"verified":
  true}` — **not the coordinates** (same test asserts
  `verified_by_employee_profile_id == profile.id` and
  `verification_reason` equals the real string passed in, never checks
  or logs `latitude`/`longitude`).
- Permission gating (only an authorized management role may call this)
  happens at the route layer, Milestone 16 — this function itself has no
  Flask dependency, consistent with every other service in this phase.

## Correction policy — new capture, not in-place mutation

A location "correction" is implemented as **capturing a new
`CustomerLocation` row** for the same parent, not editing the original
row's coordinates. This is a deliberate, real design choice consistent
with the model's own `CHECK` constraint (a row can never hold both
`lead_id` and `customer_id`, and there is no "supersedes" chain field) —
multiple location rows per parent, ordered by `captured_at`, already
form a complete, tamper-evident correction history with zero schema
change required. The most recent row is "current"; older rows remain
queryable, satisfying "correction creates history rather than silently
replacing evidence" without inventing new versioning machinery.

## Privacy — verified, not just claimed

- Exact coordinates never appear in any raised exception (`errors.py`'s
  `LocationValidationError` messages reference field names and bounds,
  never the actual value).
- `capture_location()`'s audit call (Phase 9.5A, unchanged) records only
  `{"source", "verified"}`.
- `verify_location()`'s audit call records only the location UUID and
  verification outcome.
- No employee location *history* endpoint exists — only per-record
  capture/list, scoped to an authorized Lead/Customer, never an
  employee-centric "where has employee X been" query.

## Browser behavior (Milestone 16/17 wiring, not yet built)

The explicit-action, no-`watchPosition`, no-background-capture,
no-capture-on-page-load requirements are UI/route-layer concerns
(`navigator.geolocation.getCurrentPosition()` called once, from a click
handler) — tracked for Milestone 16/17, validated with Playwright
geolocation simulation in Milestone 23. This milestone delivers the
server-side contract those routes will call into.

## Physical GPS validation boundary

Confirmed, unchanged from the governing spec's own instruction: no
physical Android/iPhone GPS validation is claimed or attempted this
phase — see `physical-location-validation-boundary.md`.
