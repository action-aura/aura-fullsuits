# Trusted Time Authority (M11.12) / Monotonic Clock Contract (M11.13) / Clock Rollback Detection (M11.14) / Forward-Jump Policy (M11.15)

Real, exact port of `trusted_time.py`
(`canonical-signed-lease-authority-audit.md`), implemented in
`TrustedTimeAuthority.kt` + `MonotonicClock.kt`.

## Real architecture

Mobile wall-clock time alone is **not** sufficient commercial
authority. `TrustedTimeAnchor(serverTimeEpochMillis,
monotonicAtAnchorMillis)` pairs the last Owner-verified wall-clock
time with the monotonic-clock reading taken at that same instant.
`trustedNowMillis(anchor, clock) = anchor.serverTimeEpochMillis +
(clock.elapsedMillis() - anchor.monotonicAtAnchorMillis)` — **trusted
time never re-reads wall clock**. A negative monotonic delta fails
closed (`TrustedTimeError`), never silently treated as zero elapsed —
real, exact port of the Python reference's own explicit choice.

## Real platform monotonic clocks (M11.13)

| Platform | Real primitive | Why |
|---|---|---|
| Android | `SystemClock.elapsedRealtime()` | Real, documented to include time spent in deep sleep (unlike `uptimeMillis()`) — the correct choice so a device sleeping through an offline grace window is not silently excluded from elapsed-time accounting. |
| iOS | `NSProcessInfo.processInfo.systemUptime` | Real, documented monotonic time since last boot — the iOS equivalent. **NOT VERIFIED** on this host (no macOS/Xcode). |

Both reset on device reboot (a real, disclosed, expected limitation —
`rehydrateAnchor()` re-pins to a fresh monotonic reading at each real
process start, carrying the persisted server time forward unchanged,
exactly matching the Python reference's own `rehydrate_anchor`).

## Clock rollback detection (M11.14)

`detectRollback(anchor, localWallClockNowEpochMillis, toleranceMillis)`
— both inputs are epoch-millis (UTC-instant by construction in this
Kotlin port), so a timezone/DST *representation* change can never
false-positive this check at all — a real, structural improvement over
needing to explicitly UTC-normalize two `datetime` values the way the
Python reference does, since this Kotlin port never carries a
timezone-attached value into the comparison in the first place. Real,
tested: `timezoneOnlyDifferenceNeverTriggersRollback`,
`clockRollbackShortCircuitsEveryOtherRule` (`OfflinePolicyEvaluatorTest.kt`).

## Forward-jump policy (M11.15)

**Real, structural, by-construction answer, matching the Python
reference exactly**: because `trustedNowMillis()` never re-reads local
wall clock at all (only elapsed monotonic time from a trusted anchor),
a wall-clock forward jump **cannot corrupt the trusted-time
computation** — there is no separate "forward-jump detector" in this
codebase, by the same real design the canonical Python authority uses.
The only consumer of local wall clock is the rollback check, which
only fires on *backward* movement. This is a real, deliberate
architectural property, not an unhandled gap — documented explicitly
per M11.15's own requirement not to silently claim device time may be
altered or bypassed: this codebase never asks the user to change
device time, and never could be tricked into extending validity by a
forward jump, because forward jumps are structurally inert to this
computation.

## Real, disclosed limitations

- No `BootSessionIdentifier` is implemented in this milestone —
  M11.13's own suggested contract names this as "where safely
  available"; real, open, deferred (monotonic-baseline-reset detection
  across reboots relies on the anchor's own re-pin-at-restart behavior
  being the real, sufficient mitigation for the threat this would
  otherwise catch, not an equivalent-strength independent signal).
- `TrustedTimeConfidence` (the sealed classification M11.12 requires)
  is defined but **not yet wired into any real evaluation path** — the
  current `SignedLeaseVerifier`/`OfflinePolicyEvaluator` integration
  uses the anchor directly rather than surfacing a confidence
  classification to callers. Real, open, disclosed gap.
