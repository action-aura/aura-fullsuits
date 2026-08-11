# Phase 9 Milestone 13 — Staging Product Physical Validation

## Status: NOT VERIFIED this session

Blocked entirely on `staging-product-build-report.md`'s precondition (no rc.6 artifacts exist, correctly
not built without a real HTTPS staging URL). No physical Android device or Windows installation was
activated against staging this session, because there is no staging-connected build to activate with.

## What would be required (real deployment, for whoever performs it next)

Once a real HTTPS staging URL exists and rc.6 artifacts are built and signed
(`release-workstation-runbook.md`): repeat the exact real physical validation methodology already
proven twice in this project's history (Phase 8V-P7, Phase 8V-P9) — real physical Android device (the
same Infinix X6528 used throughout this project), real Windows installation, real
activation/check-in/renewal/restriction/reactivation/stale-assertion cycle, real Logcat review, real
traffic data-boundary check. That methodology is proven and reusable; only the target URL changes.

## Real work this phase did NOT touch that remains valid

The physical stale-assertion guard, Scenario 7 device-facing mechanics, and every other Phase 8V-P9
physical finding remain valid and unchanged — see `docs/owner/phase8vp9/` (not re-verified this phase
per `phase8-evidence-reuse-decision.md`, no technical reason to repeat unchanged, already-proven
behavior).
