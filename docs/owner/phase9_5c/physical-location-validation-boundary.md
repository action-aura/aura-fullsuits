# Phase 9.5C — Milestone 12: Physical Location Validation Boundary

## Status: NOT VERIFIED (explicit, by design)

No physical Android or iPhone device was used to validate real GPS
capture in this phase. All location-capture testing is:

- Server-side unit tests against `validate_location_fields()`/
  `capture_location()`/`verify_location()` with synthetic coordinate
  values (`tests/test_phase9_5c_location.py`).
- Browser geolocation **simulation** via Playwright's geolocation
  override API, planned for Milestone 23 (real Chromium browser, fake
  coordinates injected by the test harness — not a real device's GPS
  chip).

This matches the governing spec's own explicit instruction: "Physical
Android/iPhone GPS validation is not required for Phase 9.5C and must be
reported as NOT VERIFIED, reserved for the later mobile/physical
validation phases." No claim of real-device GPS accuracy, real-world
positioning correctness, or physical field validation is made anywhere
in this phase's documentation, code comments, or audit trail.
