# Phase 8V-P5 — Scenario 7 (Plan Downgrade / Overage) — Final

## Result: NOT VERIFIED this session

## Why

Same real time-budget constraint as `scenario6-device-replacement-final.md` -- this scenario depends on
the same two real installations (physical Android + real Windows Retail install) already being active
under a shared license, which Scenario 6 did not reach this session. The underlying source-level defect
this scenario exists to re-verify (`Subscription.device_allowance` -> `License.device_limit`
propagation) was already fixed and unit-tested in a prior session (per the carried-forward project
record); this session did not re-derive or re-confirm that fix, and did not attempt the physical
downgrade/overage/exception/expiry walk.

## What is NOT claimed

No plan-downgrade or overage evidence, physical or otherwise, is claimed for this session.

## Follow-up required (next session)

Once Scenario 6 establishes both real installations, follow the governing spec's Part J sequence in
full: record current allowance/active-installation counts, create a next-term downgrade via the Owner
UI, confirm current-term protection before the effective date, apply the new term, confirm
`License.device_limit` receives the reduced value, confirm no silent deactivation of existing
installations, attempt a new activation and confirm it is safely blocked, run the real device-limit
reconciliation scan and confirm an overage finding plus Support-queue item, create and later let expire
a real temporary exception, and capture the real wire traffic for the check-ins throughout (via the
existing capture middleware).
