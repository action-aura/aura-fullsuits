# Phase 8V — Log Privacy Report (Part Z)

## What could not be produced this session

No physical Android device -> no real `logcat` capture. No physical/real Windows product process
run outside this repo's own test harness -> no real Windows product-log file capture. Both disclosed
here rather than fabricated (see `phase8v-scope-and-baseline.md`).

## What was verified instead: structural log-content guarantees

- **Owner never logs a request body.** Every exception handler in the external API
  (`app/api_external/routes.py::activate()`/`check_in()`/`deactivate()`) logs
  `"Internal decision failure during activation (request body not logged)."` -- the message string
  itself, verbatim, confirms this was already a deliberate Phase 6/7 design decision, re-confirmed by
  grep this session (`current_app.logger.exception(...)`, no body/key interpolated anywhere in any of
  the three call sites).
- **No `log.info`/`log.debug`/`print()` call anywhere in `licensing_service/`, `commercial_ops/`, or
  `commercial_runtime/licensing_contracts/` interpolates a request body, license key, payment note,
  or patient/sales field** -- grepped this session across every `.py` file in those three trees for
  logging calls combined with key/secret/body/payload/request-shaped variable names; the only matches
  were the `fingerprint` helper names above (safe -- fingerprints are SHA-256 hashes, not secrets).
- **The local product-side event log** (`commercial_runtime.licensing_contracts.events.LicensingEventRecorder`)
  is explicitly documented as "Local only: never transmitted to Owner" and every event this session's
  new code recorded (`ACTIVATION_PENDING`) carries only a `reason_code` string, never a body or key.
- **The real captured HTTP traffic** in `real-traffic-evidence.md` is the actual bytes that would
  appear in a network-level capture on either side of the wire -- already swept for forbidden content
  there.

## What a real logcat/Windows-log capture would additionally need to confirm

Runtime-only concerns a static/structural review cannot fully rule out: an unrelated third-party
Android library logging the HTTP request/response at the OkHttp layer if verbose logging is
accidentally left enabled in a release build, or a Windows crash-report handler dumping process
memory. Neither is new to Phase 8V (both would be pre-existing product-build configuration concerns,
not something this phase's commercial-operations changes introduced) -- flagged in
`phase8v-residual-risk-register.md` as a physical-validation-session task, not resolved here.
