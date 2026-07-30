# Phase 8V-P4 — Scenario 3: Past Due to Restricted (Physical) — **CONDITIONAL, real Owner-side finding**

## What was proven for real this session

Setting a real subscription's status to `EXPIRED` and checking in from the physical Retail device
immediately afterward produced `subscription_status: "EXPIRED"` (correct, real, from Owner) while
`current_state` remained `ACTIVE_ONLINE` on-device. Root-caused for real, not assumed:

- `commercial_ops/state_resolution.py::resolve_commercial_state()` correctly computes
  `EXPIRED` / `may_issue_assertion=False` the moment the subscription's own status/end_date says so
  -- confirmed via a direct real call against the live Postgres row.
- The device's *already-issued* signed assertion (from real Ed25519 activation minutes earlier)
  remains valid on its own signed terms until it naturally expires. Owner correctly refuses to
  *reissue* a fresh assertion once the subscription is bad, but does not (and structurally cannot)
  retroactively invalidate one it already signed -- the local state machine's own
  `ACTIVE_ONLINE + "checkin_succeeded" -> ACTIVE_ONLINE` transition (`state_machine.py`) confirms a
  successful check-in call keeps the device online regardless of the *reason* behind the call,
  exactly matching this project's own "technical grace stays separate from commercial grace"
  principle (documented since Phase 6).

This is real, correct, disclosed system behavior -- not a defect, and not something this session
attempted to route around by manufacturing a fake state.

## What was not observed this session

The full `PAST_DUE -> notification -> grace -> RESTRICTED` sequence physically on-device. Reaching
it for real requires either (a) the device's own already-issued assertion to naturally expire (its
signed validity window was ~24h from this session's real activation, i.e. into the next calendar
day, past this session's own working window), or (b) starting from a subscription that was already
bad *before* the device's very first activation -- which would violate this scenario's own
precondition ("Clinic/Retail Android ACTIVE_ONLINE" as the starting state).

The real expiry-scan CLI (`flask commercial expiry-scan`), the real Sales/Finance/Support queue
behavior, and real notification deduplication were all already proven for real (not simulated) in
Phase 8V-P (`docs/owner/phase8vp/scenario-3-past-due-evidence.md`) — unchanged since, reconfirmed
this session only indirectly via the 394-test Owner suite passing green (this scenario's own tests
are part of that suite).

## Backend enforcement in an EXPIRED/blocked commercial state

Independently confirmed this session via a *different*, real mechanism: attempting a real product
mutation (`POST /api/sub/clinic/patients`) before Clinic's device was ever activated returned
`{"reason_code": "LICENSE_INACTIVE", "status": "error"}` -- a safe, bounded reason code, not a raw
500 or stack trace, both via direct API call and via the real app UI ("Couldn't reach the server" --
see `android-backend-enforcement.md` for the note on that specific message's imprecision). This is
the same class of guarantee Scenario 3 exists to prove (protected mutations denied with a safe
message, not a crash), demonstrated via a different, real trigger than the one originally planned.

## Result: **CONDITIONAL** — Owner-side commercial-state resolution proven real and correct;
backend-mutation denial proven real via a different real trigger; the specific physical
`RESTRICTED` visual end-state was not reached this session for the structural, disclosed reasons
above.
