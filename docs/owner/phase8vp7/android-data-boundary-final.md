# Phase 8V-P7 — Android Data Boundary — Final

## Result: CLEAN for the 10 exchanges captured this session; same scope limitation as prior sessions

All fields observed match the previously-documented allowed list exactly (Phase 8V-P5/P6's own
`final-android-data-boundary.md`/`android-data-boundary-final.md`). No new field category was
introduced by this session's changes (the only behavior change is client-side interpretation of
already-allowed fields; the wire schema itself is unchanged, see `assertion-contract-change-decision.md`
carried from Phase 8V-P6, still accurate). No Clinic or Retail domain data appeared in any exchange.
`license_key` appeared, redacted, only in the six real Windows activation attempts (the only protocol
step where the full key legitimately transmits) -- never in any check-in. This audit does not cover the
scenario types not exercised from the physical device this session (late renewal confirmation,
downgrade confirmation, exception confirmation, stale-assertion) -- see `raw-wire-final.md`'s scope
disclosure.
