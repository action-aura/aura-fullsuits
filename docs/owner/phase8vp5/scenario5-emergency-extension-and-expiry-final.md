# Phase 8V-P5 — Scenario 5 (Emergency Extension) — Final

## Result: PARTIAL PASS with one real structural finding (P1, disclosed, not silently worked around)

## What was proven, physically, this session

1. **Local RESTRICTED reached for real, on-device, for the first time across 5 sessions.**
   Root cause of why prior sessions never observed it (see `phase8vp5-baseline.md`): `Subscription.status`
   changes alone never reach the local policy evaluator; only `License.status` (hard states) or a
   non-default `OfflinePolicy.hard_expiry_behavior` do. This session created a dedicated short policy
   (code `phase8vp5-fast-test-2bb386`: `check_in_interval_seconds=20, retry_interval_seconds=20,
   offline_grace_seconds=30, warning_start_seconds=15,
   hard_expiry_behavior=RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA, emergency_extension_allowed=true`),
   assigned it to a fresh Clinic license/subscription/installation (installation id
   `c7150980-d45b-4d14-866b-642fb798dceb`), removed `adb reverse` to force real check-in failure, waited
   real elapsed time, and observed the on-device Licensing screen genuinely render **"Restricted"** with
   the real product copy "Some features are limited in this state. Existing records remain fully
   viewable, and backup/restore/export remain available."
2. **Real backend enforcement during genuine RESTRICTED**: attempted `Save Patient` through the real UI;
   denied (no patient row created); message shown was the generic "Couldn't reach the server" rather than
   a state-specific message — noted below as a minor, real, disclosed UX finding, not corrected in
   production code this session (out of the emergency-extension scope; tracked in the residual risk
   register).
3. **A real P1 bug was found and fixed**: `create_emergency_extension()` defaulted `now` to the naive
   `datetime.utcnow()` instead of the codebase's shared timezone-aware `app.models.base.utcnow()`. On
   this project's real dev Postgres (`Asia/Amman`, UTC+3), a naive value written into a
   `DateTime(timezone=True)` column is silently re-localized to the session's own timezone, shifting the
   real stored `starts_at`/`expires_at` a full 3 hours earlier than intended — a short extension could
   already be expired by the time it was read back. Fixed in
   `owner/app/commercial_ops/emergency_extensions.py` (all three call sites); added regression test
   `test_create_emergency_extension_default_now_is_real_utc_not_shifted`; fixed a pre-existing test that
   was silently masking the same naive/aware mismatch
   (`test_is_emergency_extension_active_reflects_status_and_expiry`). 12/12 passed in
   `test_commercial_ops_emergency_extensions.py` after the fix; no collateral regressions in the two other
   files that call `create_emergency_extension` (16/16 passed).
4. **A real, second, more significant structural finding, found by carefully reading the real captured
   wire evidence rather than assuming success**: after the timezone fix, a new emergency extension
   (id `19c6048e-d35d-4698-a9a1-ccec79b10ff2`) was created for the same subscription, a real device
   check-in was captured, and the resulting signed assertion correctly carried
   `emergency_extension_id: 19c6048e-...` (proving the timezone fix works end-to-end, Owner service through
   to a real signed payload). However `payload.offline_policy.emergency_extension_until` was `null` in
   that same real payload. Source investigation (`owner/app/commercial_ops/assertion_fields.py`,
   `owner/app/licensing_service/offline_policy.py`, `owner/app/models/licensing_service.py:139-140`,
   `commercial_runtime/licensing_contracts/policy_evaluator.py:108-110`) confirms this is not a bug in the
   capture or a timing artifact: **there are two separate, never-wired emergency-extension mechanisms in
   this codebase**:
   - The business/audit layer (Part N): `commercial_ops.emergency_extensions.EmergencyExtension` — real
     MFA-gated creation/revocation, real audit trail, real hard cap/no-stacking invariants, its `id`
     surfaced informationally in the assertion via `assertion_fields.py::_active_emergency_extension_id()`.
   - The technical grace-extension layer (Part O): `OfflinePolicy.emergency_extension_allowed` /
     `OfflinePolicy.emergency_extension_until` — two columns directly on the `OfflinePolicy` row itself.
     `policy_evaluator.py::evaluate()` is the **only** local code path that can extend
     `effective_grace_seconds`, and it reads exclusively `policy.emergency_extension_allowed and
     policy.emergency_extension_until is not None` (line 108) — never the `EmergencyExtension` table, never
     `emergency_extension_id`.
   - `create_emergency_extension()` never writes to `OfflinePolicy.emergency_extension_until`. There is no
     code path in this codebase today that connects the two. **Creating a real, approved, audited
     emergency extension currently has no functional effect on a device's local grace/restriction
     computation** — it is informational/audit-only as delivered to the client. The earlier apparent
     "Restricted → Active" transition observed in this session's first (confounded) attempt was correctly
     recognized in-session as caused by the ordinary successful-check-in grace-timer reset, not by the
     extension — this second, corrected attempt (with real wire evidence inspected line-by-line) confirms
     that recognition was right.

## Verdict and why this is not a full PASS

Per the governing spec, Scenario 5 PASS requires confirming the extension "restores eligibility" and that
its real expiry causes reversion — i.e., a real, isolated, functional effect. That could not be
demonstrated, because on current code there is no functional effect to demonstrate: the two mechanisms are
unwired. This is reported as **PARTIAL PASS**: the extension's business-process half (creation, hard cap,
no-stacking, revocation, audit, propagation into the signed assertion as an informational field, and the
real timezone bug found in that half) is fully proven working. Its technical half (actually extending
local grace) is not implemented/wired, so its "isolated effect" and "real expiry-triggered reversion"
cannot be shown to exist. The expiry sub-test was not run against a mechanism confirmed to have no
observable effect (would prove nothing beyond what source-reading already shows).

## Disposition

Recorded as a real, disclosed P1 in the residual risk register (`docs/owner/phase8vp5/...` final regression
report and `docs/owner/phase8vp4/final-residual-risk-register.md`, updated additively). Not fixed this
session: wiring `EmergencyExtension` creation to also set
`OfflinePolicy.emergency_extension_allowed=True`/`emergency_extension_until=extension.expires_at` (and
clearing it on revoke/expiry) is a real, scoped, correct-looking fix, but the governing spec's explicit
scope is "targeted P0/P1 fixes found during validation" for *this* phase's own gates — implementing and
then re-validating a new cross-module wiring change physically (fresh device cycle, new wait windows) was
assessed against remaining session time and deferred with full disclosure rather than rushed. This
directly affects the final Phase 8 tag decision (see `phase8-final-decision.md`): Scenario 5 does not meet
the spec's PASS bar, so the final unconditional tag is withheld.

## Real IDs for traceability

- OfflinePolicy code: `phase8vp5-fast-test-2bb386`
- Installation id: `c7150980-d45b-4d14-866b-642fb798dceb`
- EmergencyExtension id (post-fix, correctly wire-proven informational field): `19c6048e-d35d-4698-a9a1-ccec79b10ff2`
- Incident reference: `PHASE8VP5-S5-ISOLATED-RETRY`
