# Phase 7V-A — Residual Risks

## 1. Device-specific `adb reverse` connectivity limitation (new this round)

**What:** on the physical test device (Infinix HOT 40i), `adb reverse tcp:19101 tcp:19101` —
used to let the device's embedded app reach the loopback-only Owner test instance running on the
host — worked reliably during the Part G/H activation testing earlier in this session, but became
unreliable later in the session: repeated, carefully-controlled attempts (clean app restarts,
fresh tunnel re-registration, full `adb server` restart, waits well in excess of the client's own
worst-case retry budget) consistently resulted in the app reporting "Could not reach the licensing
service" for check-in attempts, even though the host-side Owner process was confirmed reachable
and responsive on every attempt.

**Why this is believed to be a device/OEM characteristic, not a product defect:**
- `network_security_config.xml` was independently re-reviewed and is unchanged and correct
  (cleartext permitted only to `127.0.0.1`/`localhost`).
- The identical mechanism (Kotlin `OwnerClient` → embedded Python via `/_internal/sync-*`) was
  used successfully for the original Part G/H activations earlier in this exact session, on this
  exact device, over this exact tunnel.
- An earlier investigation this same session (see `bugs-found-and-fixed.md`'s residual note)
  already flagged `adb reverse` reliability issues on this specific device/OEM as a dead end
  unrelated to the real bugs found.
- Genuine hardware-level USB disconnects (Windows PnP `Status=Unknown`) were also observed
  multiple times this session, consistent with a generally flaky USB/adb transport on this
  particular device rather than an application-level defect.

**Impact:** Part L (suspension/reactivation) and Part M (deactivation) could not be carried to a
full physical end-to-end confirmation in this round — both require the device to successfully
reach Owner. See `final-decision.md` for exactly what evidence substitutes for the missing
physical confirmation on each, and why that evidence is considered strong despite the gap.

**Recommended follow-up:** re-attempt Parts L/M's physical confirmation on a future session with
either a different physical device, a fresh USB cable/port, or (if `adb reverse` continues to be
unreliable on this device class) the LAN-IP approach explicitly re-evaluated with its
`network_security_config.xml` implications properly scoped and reviewed — not as a silent
workaround, but as a deliberate, documented decision.

## 2. Carried forward from `phase7v-final/final-residual-risk-register.md`

- **Trusted-time anchor reset after a process restart with no fresh sync yet.** Confirmed and
  directly exercised multiple times this round (every `adb install -r` / `am force-stop` +
  restart resets the in-memory anchor cache, causing `elapsed_offline` to appear near-zero
  immediately after a restart until the next successful sync or `reevaluate` call re-establishes
  real elapsed time). This is the documented, accepted design gap from Phase 7V-F, not a new
  finding — but this round's testing produced unusually direct, repeated confirmation of exactly
  how it manifests in practice, which is recorded here for anyone debugging a similar "state
  seems stuck right after a rebuild" symptom in the future.
- **AAB rebuild status:** both final AABs in this round (see `final-artifact-reconfirmation.md`)
  were rebuilt via `bundleRelease` alongside the final `assembleRelease`, resolving the
  `phase7v-final` round's carried-forward "AAB not rebuilt this round" item for Android — the
  checksums in this document's AAB rows are current, not stale.
