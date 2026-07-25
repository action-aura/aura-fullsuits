# Phase 7V-A — Residual Risks

## 1. Corrected: what looked like a device-level `adb reverse` limitation was a build-command mistake

**Earlier in this round**, Parts L (suspension/reactivation) and M (deactivation) appeared to be
blocked by a reproducible `adb reverse` connectivity failure — every check-in attempt reported
"Could not reach the licensing service" despite the device's tunnel being correctly registered and
the host-side Owner process confirmed responsive.

**Root cause, found via a targeted logging diagnostic:** the validation session's own
`-PownerLicensingBaseUrl` gradle property was missing Owner's `/api/licensing/v1` blueprint path
prefix. Every rebuild this round (including the Part I/J/K rounds) was pointed at
`http://127.0.0.1:19101` instead of `http://127.0.0.1:19101/api/licensing/v1`, so every check-in
request hit a bare 404 from Owner, which Android's `OwnerClient` correctly surfaced as a generic
connectivity failure (a 404 isn't a retryable status, so the response body — an HTML 404 page —
gets returned as "success" and then fails Gson parsing as `MalformedResponseError`, which is
itself an `OwnerClientError` subtype, producing the exact same UI message a genuine network
failure would). **This was a mistake in this session's own build commands, not a bug in the
product, and not a limitation of the physical test device.**

**How this was found and fixed:** added a temporary `Log.e` diagnostic to `OwnerClient.kt`'s
exception handling (never committed — removed before the final build), which revealed the exact
malformed URL and the literal 404 HTML body being misparsed. Corrected the gradle property to
include the full path, confirmed the fix with a temporary LAN-based retest (see below), then
rebuilt the genuinely final artifacts with the corrected URL and reconfirmed reachability over
`adb reverse`+loopback too.

**Physical evidence obtained after the fix (Retail):**

```
ACTIVE_ONLINE (real check-in succeeds)
  → suspend license on Owner → RESTRICTED (real rejected check-ins, via the Part I reevaluate pipeline)
  → reactivate license on Owner → real check-in succeeds → ACTIVE_ONLINE
  → tap Deactivate This Device → confirm → DEVICE_DEACTIVATED (real deactivation success path)
```

All four transitions were driven by genuine network round-trips (first over a temporary LAN path,
then reconfirmed over the shipped loopback+`adb reverse` path), not simulated or Owner-side-only.

**Why the temporary LAN retest was needed at all, and how it was scoped:** at the time this was
being chased down, the failure looked identical across USB and wireless `adb reverse`, which
pointed toward a device/OEM-level block rather than a cable issue — a reasonable hypothesis at the
time, given it matched a similar-sounding dead end noted earlier in this same session
(`bugs-found-and-fixed.md`'s residual note). Confirming or ruling that out required reaching Owner
over a transport that didn't depend on `adb reverse` at all, which meant a temporary LAN-bound
Owner test instance and a throwaway APK variant with `network_security_config.xml` widened to
permit cleartext to one specific LAN IP. This was done only with explicit user sign-off given the
network-exposure implications, using:

- A temporary Owner instance bound to `0.0.0.0` on an unused port, reusing the same DB and signing
  key as the loopback instance — never a second, divergent source of truth.
- A throwaway, never-committed APK variant (`network_security_config.xml` reverted via `git
  checkout` before any further real build; confirmed via `git status` showing a clean tree
  afterward).
- A firewall rule scoped to that one port on the Private profile only, removed after use.

None of this touched the shipped artifact's actual `network_security_config.xml`, which was
confirmed byte-identical to the committed version before the final rebuild (see
`final-artifact-reconfirmation.md`).

**Conclusion:** `adb reverse` works correctly on this physical device once the URL itself is
correct — reconfirmed directly against the original loopback Owner instance after the fix. There
is no remaining device-level connectivity concern to carry forward from this round.

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

## 3. Minor: a suspended license's rejection doesn't itself update the device's stored `license_status` field

Observed directly this round: when a check-in is rejected because the license is suspended
(`checkin.py` raises before building an assertion), the device's locally-stored assertion evidence
— including its `license_status` field — is left unchanged from the last successful assertion,
since no new assertion is ever issued on a rejection. The device correctly still moves toward
`RESTRICTED` via the Part I reevaluate pipeline (confirmed physically this round), so the
practical effect (commercial features cut off) is correct; only the specific status label
surfaced in the local UI can lag behind the true server-side reason until a later successful
sync. Not a defect in scope for this round to fix — noted for anyone extending the local status
presenter to show a more specific "suspended" message rather than the generic offline/restricted
banner.
