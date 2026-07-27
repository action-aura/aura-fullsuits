# Phase 8V — Physical Validation Report

## Verdict: NOT VERIFIED (environment-blocked, disclosed before any work began)

No `adb`, no connected physical Android device, no running Android emulator, and no path to acquire
either exist in this environment. This was checked and disclosed in
`phase8v-scope-and-baseline.md` *before* any implementation work started this session, not
discovered as an excuse afterward.

## What was substituted, and why it's real evidence even though it isn't this

- A live `werkzeug`-served Owner Flask app on a real localhost TCP port, driven by the actual
  `commercial_runtime.licensing_contracts` Python package (the identical code both the Windows
  desktop product AND Android's embedded-Python backend run) via genuine `requests`-library HTTP,
  real Ed25519 signing, real server-side signature verification. This is real product-to-Owner wire
  traffic and real cryptographic proof -- it is Owner-to-Windows-equivalent traffic, not
  Owner-to-Android-hardware traffic.
- What a physical Android device would add beyond this substitute: AndroidKeystore-backed private
  key storage (vs. this session's in-memory Ed25519 key -- the *protocol* behavior is identical
  either way, since the embedded Python backend never sees the private key regardless of where it's
  stored), the Kotlin `OwnerClient`/`LicensingCoordinator` HTTP round-trip and JSON-passthrough layer
  (already covered, without hardware, by real Gradle unit tests --
  `OwnerClientTest.kt`/`CanonicalTest.kt`, part of the 82/82 Clinic total), and genuine on-device
  UI rendering of the new PENDING message (the Kotlin code path was written and the Gradle build
  compiles and unit-tests it, but the actual screen was never visually confirmed on a device this
  session).

## What must happen in a session with device access

1. Connect a physical Android device (Clinic and Retail, at least one each, matching Phase 7V-A's own
   bar).
2. Run the seven Part AB scenarios physically, screenshot/logcat evidence per scenario.
3. Visually confirm the new PENDING-activation message renders correctly (English and Arabic on
   Clinic; English on Retail, given its pre-existing Arabic gap).
4. Capture real logcat/Windows-log output per `phase8v-log-privacy-report.md`'s outstanding item.
5. Only then may the final Phase 8 tag drop its conditional qualifier -- see
   `docs/owner/phase8/phase8-final-decision.md`'s updated closure section and this phase's own
   `phase8-final-decision.md`.

No physical result is claimed anywhere in this document set. Every "PASS" recorded elsewhere in
Phase 8V's evidence is explicitly scoped to what was genuinely run.
