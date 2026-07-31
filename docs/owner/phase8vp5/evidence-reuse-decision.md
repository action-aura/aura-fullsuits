# Phase 8V-P5 — Evidence Reuse Decision

## Accepted without repetition (no source change since Phase 8V-P4, no regression found)

- Scenario 1 (Clinic early renewal): retained as-is from `docs/owner/phase8vp4/scenario1-clinic-early-renewal.md`.
- Scenario 4 (Clinic pilot conversion): retained as-is from `docs/owner/phase8vp4/scenario4-clinic-pilot-conversion.md`.
- Device readiness, signed upgrade, certificate continuity, initial activation: retained; smoke-
  reconfirmed only (device model/serial, versionName/versionCode, installation IDs unchanged).

## Smoke-checked this session (not fully re-executed)

- Both products' installed identity (versionCode/versionName) -- reconfirmed via `dumpsys package`.
- Both products' installation continuity -- reconfirmed via real `/api/licensing/status` calls,
  which additionally proved real multi-hour persistence across the session gap (stronger evidence
  than a mere force-stop test).
- Owner preflight -- re-run for real, `ok: true`.

## Requiring full execution this session (named incomplete or not verified in Phase 8V-P4)

Scenario 2 (final), Scenario 3 (final), Scenario 5 (final, including expiry), Scenario 6, Scenario
7, stale-assertion rejection, backup/restore/export, Retail financial/return integrity, Clinic
invoice/payment integrity, raw wire capture, full product backend regression, local-deactivation UX
classification.

## Evidence invalidated by this session's own findings

None from source changes (no source changed). One **interpretive** correction: Phase 8V-P4's
scenario docs described the RESTRICTED-state gap as a timing/observation limitation ("would need
more session time"). This session's source investigation clarifies it more precisely: it was not
merely a matter of more time, but of using the *correct* real mechanism (License-level `SUSPENDED`
or a non-default `OfflinePolicy`) rather than only changing `Subscription.status`, which structurally
cannot produce local restriction on its own. Phase 8V-P4's own evidence stands as accurate for what
it tested; this is an additive clarification, not a retraction.
