# Phase 9.5C — Milestone 12: Location Privacy and Threat Model

## Threats considered

| Threat | Mitigation | Evidence |
|---|---|---|
| Continuous/background tracking | No polling loop anywhere in the codebase; `capture_location()` is a single insert per call, triggered only by an explicit route call (Milestone 16) which itself is triggered only by an explicit browser button click (Milestone 17) | `app/leads/services.py:capture_location`, `app/leads/location.py` — grep confirms zero `setInterval`/`watchPosition`-equivalent server or client code exists |
| False "verified GPS" claim | `verified` defaults `False`; only `verify_location()` (management-only, reason-required, audited) can flip it | `test_verify_location_requires_reason_and_records_actor_not_coordinates` |
| Coordinate leakage via exceptions/logs | Every `LocationValidationError` message references field name + bound, never the value | `app/leads/errors.py` `LocationValidationError._MESSAGES` |
| Coordinate leakage via audit payload | `capture_location()`/`verify_location()` audit calls record only `source`/`verified`/location UUID | `app/leads/services.py`, `app/leads/location.py` |
| Coordinate leakage via URL | Not applicable yet (no route exists) — tracked as a Milestone 16 requirement: location submission must be a POST body, never a query string | — |
| Spoofed `captured_by`/actor | `captured_by_employee_profile_id` is always the caller-supplied `actor_employee_profile_id` parameter, never taken from request body | `app/leads/services.py:capture_location` signature |
| Unauthorized parent access | Enforced at the route layer (Milestone 16) via the same ownership checks as every other Lead/Customer child resource | — |
| Employee-movement-history reconstruction | No query exists (or is planned) that lists locations by `captured_by_employee_profile_id` across multiple Leads/Customers — only per-record listing | Confirmed by grep: `captured_by_employee_profile_id` appears only as a write target, never in a `WHERE`/`ORDER BY` clause anywhere in `app/leads/` |
| Third-party geocoding/map provider data exposure | `reverse_geocoded_address` column exists (Phase 9.5A) but no provider integration was added this phase — column remains unused/NULL unless a future phase adds one | Confirmed: zero HTTP client calls to any mapping/geocoding service anywhere in `app/leads/` |

## What this phase does NOT claim

- Not GPS-verified by default, ever.
- Not anti-spoofing-proof (a browser can report false coordinates; this
  phase stores what the browser reports with clear source/accuracy
  metadata, and lets a human — management — apply real-world judgment
  through the verification action, exactly as the governing spec
  requires).
- Not physically validated on real Android/iPhone hardware — see
  `physical-location-validation-boundary.md`.
