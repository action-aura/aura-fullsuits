# Offline Commercial Decision Model (M11.17) / Signed Offline Policy (M11.18) / Refresh-Deadline vs Expiry (M11.19) / Revocation Limitations (M11.20)

Real, exact port of `state_machine.py`'s `LicenseState`/`ACTIVE_FAMILY`/
`DATA_PRESERVED_FAMILY` and `policy_evaluator.py::evaluate`
(`canonical-signed-lease-authority-audit.md`), implemented in
`OfflineCommercialDecision.kt`.

## Two real layers

1. **`OfflineLicenseState`** — reached only once signature verification
   and context binding have both already succeeded. Real values:
   `ACTIVE_ONLINE`, `ACTIVE_OFFLINE`, `WARNING`, `GRACE_PERIOD`,
   `RESTRICTED`, `SUSPENDED`, `REVOKED`, `EXPIRED`,
   `CLOCK_REVIEW_REQUIRED`.
2. **`CommercialAccessDecision`** — the full pipeline outcome M11.17's
   own checklist requires, wrapping every real failure mode that can
   occur *before* offline policy is ever evaluated (decode/crypto/
   context/clock failures) alongside the mapped offline state. Every
   case carries `permitsCommercialOperation`,
   `permitsDataPreservedAccess`, `onlineRefreshCanRecover`,
   `reactivationRequired` — never a bare boolean.

## Real evaluation order (`OfflinePolicyEvaluator.evaluate`, exact port)

1. Reject an unrecognized `hardExpiryBehavior` outright (fail closed, never guess a safe default).
2. Clock rollback **short-circuits every other rule** → `CLOCK_REVIEW_REQUIRED`.
3. `installationStatus == SUSPENDED|DEACTIVATED` → `SUSPENDED`/`REVOKED`; `licenseStatus in (EXPIRED, REVOKED)` → `EXPIRED`; `licenseStatus == SUSPENDED` → `SUSPENDED` (explicit signed decisions override every timer, and a security-driven suspension is **never** masked by an emergency extension — real, tested: `emergencyExtensionNeverMasksASecurityDrivenLicenseSuspension`).
4. Subscription state (`EXPIRED|CANCELLED|SUSPENDED` → `RESTRICTED`; `PAST_DUE` past its own signed `commercialGraceEnd` → `RESTRICTED`) — unless an emergency extension is currently effective.
5. `effectiveGraceSeconds = min(signed offlineGraceSeconds, localSafetyCeilingSeconds)` — a local build may only **shorten**, never silently extend (M11.18's own binding rule; real, tested: `localSafetyCeilingCanOnlyShortenNeverExtendSignedGrace`).
6. Recently online → `ACTIVE_ONLINE`. Within grace → `WARNING` (near boundary) or `ACTIVE_OFFLINE`. Within grace+retry → `GRACE_PERIOD`. Exhausted: `WARN_ONLY` → still `GRACE_PERIOD` (never blocks commercial mutation by time alone); otherwise → `RESTRICTED`.

## Signed offline policy only (M11.18)

Every grace/retry/warning/rollback-tolerance value is read **exclusively**
from the verified lease's own `offline_policy` claim. No client-defined
grace exists anywhere in this codebase. `localSafetyCeilingSeconds`
(an optional caller-supplied parameter) can only shorten the effective
window — real, structural, not a convention that could be violated by
a future call site, since `effectiveGraceSeconds` is always
`min(signed, ceiling)`, never `max`.

## Refresh deadline vs. hard expiry (M11.19)

**Real, disclosed gap**: the canonical Python `OfflinePolicy` does not
carry a distinct "refresh deadline" field separate from
`checkInIntervalSeconds`/`offlineGraceSeconds`/`retryIntervalSeconds` —
the real three-tier real Python model *is* check-in-interval →
grace → grace+retry, which this Kotlin port matches exactly. M11.19's
own suggested A/B/C/D refresh-deadline-vs-hard-expiry framing maps onto
this real three-tier model (recently-online / within-grace /
grace-exhausted) rather than being a distinct fourth concept — this
document records that mapping explicitly rather than inventing an
additional unsigned client-side "refresh deadline" concept the
canonical authority does not itself define.

## Revocation limitations (M11.20)

**Explicit, honest limitation**: an offline application cannot learn
about a new server-side revocation until it successfully contacts the
licensing authority. This milestone enforces revocation/suspension
**already signed into the last accepted lease** (`installationStatus`/
`licenseStatus` checks above) and lease expiry/refresh requirements —
it does **not** and cannot claim instant offline revocation. The real
mitigation is bounded lease lifetime (`expiresAt`) and the
`checkInIntervalSeconds`/grace/retry windows above, exactly as the
canonical Python authority already relies on.

## Real, executed test coverage (`OfflinePolicyEvaluatorTest.kt`, 16 tests)

Recently-checked-in → `ACTIVE_ONLINE`; within-grace → `ACTIVE_OFFLINE`;
near-boundary → `WARNING`; past-grace-within-retry → `GRACE_PERIOD`;
exhausted+`RESTRICT_*` → `RESTRICTED`; exhausted+`WARN_ONLY` →
`GRACE_PERIOD`; installation-suspended/license-revoked overrides;
`PAST_DUE` before/after its own grace end; effective emergency
extension overriding `PAST_DUE`; emergency extension never masking a
license-level suspension; clock rollback short-circuit; timezone-only
difference never triggers rollback (structural, epoch-millis-only
design); local safety ceiling shortening effective grace; unknown
`hardExpiryBehavior` fails closed.

## Real, disclosed gaps

Entitlement validation (M11.11), app-version policy (M11.21), and
lease-sequence replay/rollback protection beyond the signature's own
time-window check (M11.16) are not yet wired into this decision model
— real, open work.
