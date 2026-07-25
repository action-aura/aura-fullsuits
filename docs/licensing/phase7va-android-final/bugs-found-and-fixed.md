# Phase 7V-A — Real Defects Found and Fixed via Physical Device Testing

All six defects below were found only because a real device was used — none were caught by the
existing 800+ unit/integration tests, since each depends on real hardware, real elapsed wall-clock
time, or a real Android process lifecycle. Each was diagnosed via the same methodology: add a
targeted print/log diagnostic, rebuild, reproduce on-device, read the exact evidence, fix, remove
the diagnostic, add a regression test. Every fix is minimal and directly related to the failure it
addresses; none touch financial logic, signing identities, package IDs, or add release-build
bypasses.

## 1. StrongBox fallback never actually caught the exception it was written for

**File:** `android/aura-{clinic,retail}/app/.../licensing/DeviceIdentity.kt`

The wrapping-key generator wrapped only the `setIsStrongBoxBacked(true)` builder call in
try/catch — a call that never throws. The real `StrongBoxUnavailableException` throws at
`generator.generateKey()`, outside the guard, so a device without StrongBox hardware (this test
device) crashed instead of falling back to a non-StrongBox key. Fixed by moving `generateKey()`
inside the try block and catching broadly (`Throwable`, not the named exception class, since that
class requires API 28 and `minSdk` is 26 — catching it by name fails `lintRelease` as NewApi).

## 2. Retry loop reused the same signed body — including the nonce — on every attempt

**File:** `android/aura-{clinic,retail}/app/.../licensing/OwnerClient.kt`

`request()`'s retry loop built the signed request body once and resent the identical bytes
(nonce included) on every retry. Owner's replay protection correctly rejects a reused nonce, so
any request that needed even one retry over the device's real, unreliable transport failed
permanently with `NONCE_REUSED`. Fixed by making `activate`/`checkIn`/`deactivate` take a
`bodyProvider` lambda invoked fresh on every attempt (including retries) — `idempotency_key` stays
stable across retries (matching Owner's own idempotency-conflict detection, which deliberately
excludes nonce/timestamp/request_id), only the nonce/timestamp/signature rotate.

## 3. Owner crashed on a same-device-key, different-installation-id retry

**File:** `owner/app/licensing_service/activation.py`, `device_identity.py`

The client-generated `installation_id` is intentionally fresh on every activation attempt (by
protocol design). Owner's own retry-dedup logic only recognized a retry with the *same*
installation_id; a retry with the same device key but a *new* installation_id fell through to a
raw `register_device_key()` INSERT and crashed with a `UniqueViolation` on the device key's unique
fingerprint. Fixed by adding a fallback lookup by device-key fingerprint before falling through to
registration: if an ACTIVE device key already exists for this fingerprint under the same license,
reuse its installation instead of crashing.

## 4. Gson's numeric coercion silently corrupted the exact bytes a signature was computed over

**Files:** `OwnerClient.kt`, `LicensingCoordinator.kt` (both products)

`Gson().fromJson(..., Map<String, Any?>)` coerces every JSON number to `Double`. The Kotlin layer
parsed Owner's signed response into a `Map`, then **re-serialized** that map with
`gson.toJson(...)` before handing it to the embedded Python backend for independent verification —
producing bytes that were no longer byte-identical to what Owner actually signed (canonicalization
depends on exact value representation). Verification correctly failed with
`ASSERTION_VERIFICATION_FAILED`, even though the signature was cryptographically valid (confirmed
independently, outside the app, before this was found). Fixed by having `requestRaw()` return
Owner's response body as a raw `String`, parsed only once to validate it's well-formed JSON (the
parsed copy is discarded), and forwarding that exact string verbatim through to the
`/_internal/sync-*` calls — no re-serialization anywhere on the path a signature was computed over.

## 5. No clock-skew tolerance on assertion validity — a ~1s real device/host clock offset rejected every assertion

**File:** `commercial_runtime/licensing_contracts/assertion_verifier.py`

`not_before`/`expires_at` were checked with zero tolerance against the trusted-time value. This
device's clock ran about one second behind the host issuing assertions, which was enough to make
every fresh assertion fail with `ASSERTION_NOT_YET_VALID` before its own `not_before` timestamp
had technically arrived from the device's point of view. Added a
`CLOCK_SKEW_TOLERANCE_SECONDS = 60` tolerance band on both edges — generous enough to absorb real
device clock drift, narrow enough that it does not meaningfully weaken the freshness guarantee.

## 6. Android's failed check-in never re-evaluated offline policy at all (found in this window, Part I)

**Files:** `commercial_runtime/licensing_contracts/routes.py`, `checkin_scheduler.py`,
`android/aura-{clinic,retail}/.../licensing/LicensingCoordinator.kt`

Windows' `LicenseCheckInScheduler.run_once()` calls `reevaluate_only(checkin_ok=False)` on every
failed check-in attempt, which re-runs `evaluate()` against elapsed trusted time even though no
fresh assertion arrived — this is how Windows notices a WARNING/GRACE_PERIOD/RESTRICTED boundary
has been crossed purely by time passing. Android's Kotlin layer makes its own signed HTTP call
directly (never routes through `run_once()`); on failure its `checkIn()` simply returned the
last-persisted status unchanged. This meant Android's offline state machine could never advance
past `ACTIVE_OFFLINE` no matter how much real time elapsed with Owner unreachable — every failed
check-in attempt was a complete no-op as far as state evaluation was concerned, discovered only
by physically waiting real minutes with the device offline and observing `current_state` never
move. Fixed by adding a new `/_internal/reevaluate` route (no Owner call — purely re-runs
`evaluate()` against the already-stored assertion and elapsed trusted time, exactly like
`reevaluate_only()`), and wiring Android's `checkIn()` failure path to call it instead of just
re-reading stale status. Physically confirmed on-device: `ACTIVE_ONLINE` → `ACTIVE_OFFLINE` →
`WARNING` → `RESTRICTED` all reached via real elapsed wall-clock time on both Clinic and Retail
(see `final-decision.md` for the full physical evidence log).

## Residual/dead-end investigation (not a bug, logged for completeness)

An early hypothesis blamed `adb reverse` tunnel unreliability for the original activation
failures and prompted a LAN-IP detour (a companion Owner instance bound to `0.0.0.0`, reached via
the device's real LAN IP instead of loopback). This was abandoned once bug #1 (StrongBox) was
found to be the actual root cause — the detour would have required weakening
`network_security_config.xml`'s cleartext allowlist beyond `127.0.0.1`/`localhost`, which was
correctly avoided. Final builds use loopback-only, matching the shipped design. The same class of
device-level `adb reverse` flakiness resurfaced later in this session (see `residual-risks.md`)
and is a testing-environment characteristic of this specific device/OEM, not a product defect.
