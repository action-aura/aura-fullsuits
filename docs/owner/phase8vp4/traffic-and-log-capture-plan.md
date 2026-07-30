# Phase 8V-P4 — Traffic and Log Capture Plan (as actually executed)

Written retroactively to record the method actually used this session (a formal proxy/
instrumentation harness was not stood up given time constraints -- see `real-android-traffic-
evidence.md` for what this means for wire-level capture specifically).

## What was used

1. **Local licensing-status API** (`GET /api/licensing/status` on each product's own embedded
   backend, reached via `adb forward`): queried after every real Owner-side action this session, a
   real and direct reflection of what the Kotlin licensing layer's last real Owner exchange
   produced.
2. **Logcat**, filtered per-app-PID: `adb logcat -c` before a batch of operations,
   `adb logcat -d --pid=<pid>` afterward, greeped for license-key strings and exception/error
   markers.

## Redaction

No redaction was needed in practice -- the license key never appeared in any captured log or status
response outside the one real activation call per product, so there was nothing to redact from the
evidence actually gathered.

## Not done this session

A TLS-terminating local proxy or Kotlin-side request logging to capture the actual raw
Owner<->device HTTP bytes in flight -- disclosed as a real gap in `real-android-traffic-
evidence.md`.
