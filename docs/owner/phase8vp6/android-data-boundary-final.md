# Phase 8V-P6 — Android Data Boundary — Final

## Result: CLEAN for the 7 exchanges captured this session; same scope limitation as Phase 8V-P5

All fields observed across the 4 check-ins, 2 signing-keys, and 1 service-info exchange this session
match the previously-documented allowed list exactly (`docs/owner/phase8vp5/final-android-data-boundary.md`),
plus the one field this session started actively consuming client-side (`commercial_grace_end`), which
was already part of the allowed list since Phase 8 Part W and is itself just an ISO-8601 timestamp --
no new category of data was ever exposed by this session's changes; only *interpretation* of
already-allowed data changed. No Clinic domain data (patient names, invoice details, appointment data)
appeared in any request or response body. `license_key` never appeared (no activation occurred this
session). `signature` was redacted in every entry. This audit does not cover the scenario types not
exercised this session (late renewal, device replacement, plan downgrade, stale assertion) -- see
`raw-wire-final.md`'s scope disclosure.
