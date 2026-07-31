# Phase 8V-P6 — Scenario 7 (Plan Downgrade / Device Overage) — Final

## Result: NOT VERIFIED this session

## Why

Same time-budget reason as `scenario6-device-replacement-final.md` -- depends on the same two real
installations Scenario 6 would establish, which was not reached. The underlying Owner-side fix this
scenario exists to re-verify (`Subscription.device_allowance -> License.device_limit` propagation,
`docs/owner/phase8vp2/scenario7-resolution-report.md`) was not touched or affected by this session's
changes (confirmed: this session's diffs are entirely in `licensing_service`/`commercial_ops`'s
check-in/activation/offline-policy/emergency-extension paths, none of which overlap
`renewal_requests.py::apply_renewal_request()`), so no regression risk is introduced, but no new
physical evidence is claimed either.

## What is NOT claimed

No plan-downgrade or overage evidence, physical or otherwise, is claimed for this session.

## Follow-up required (next session)

Unchanged from Phase 8V-P5: once Scenario 6 establishes both real installations, follow the governing
spec's Part R sequence in full.
