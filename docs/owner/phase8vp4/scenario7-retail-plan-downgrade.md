# Phase 8V-P4 — Scenario 7: Plan Downgrade and Device Overage — **NOT independently re-verified physically this session**

The Owner-side defect this scenario is centered on (`Subscription.device_allowance` never
propagating to `License.device_limit`) was found and fixed for real in Phase 8V-P2
(`owner/app/commercial_ops/renewal_requests.py`), with 8 real Postgres-backed regression tests and
a live-database re-verification, reconfirmed green in this session's own 394-test Owner suite run.

This session's real, physical Retail installation (`e77bd448-...`) is on a license with
`device_limit=2` and only 1 active slot consumed (itself) -- reaching a genuine overage condition
physically would require either a second real device identity (same constraint as Scenario 6) or
manufacturing the overage by direct database edit, which this phase's own rules explicitly forbid
("do not edit product licensing databases to manufacture lifecycle states").

## What is already real and proven, carried forward unchanged

The full remediation mechanism (no silent deactivation, new-activation blocking, reconciliation
finding, Support queue item, temporary exception with explicit expiry) was proven for real via
wire-level traffic and a real over-limit-scan CLI run in Phase 8V-P
(`docs/owner/phase8vp/scenario-7-plan-downgrade-evidence.md`) and the sync fix itself in Phase
8V-P2 (`docs/owner/phase8vp2/scenario7-final-evidence.md`), including a live-database re-run this
same repository has already executed twice.

## What this session did NOT add

No new physical Android evidence for Scenario 7 specifically -- the same real, disclosed gap as
Scenario 6, for the same root cause (only one physical device available).

## Result: **NOT VERIFIED physically this session** (Owner-side mechanics remain proven and
current, reconfirmed via the green regression suite; no source change affecting this path this
session).
