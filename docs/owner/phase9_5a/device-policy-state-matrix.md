# Phase 9.5A — Device Policy State Matrix

| Scenario | `resolve_device_policy()` result | Real enforcement this phase |
|---|---|---|
| No profile for plan, no global default | `max_total_devices=None` (unlimited beyond `License.device_limit`), no platform rules | `License.device_limit` alone, unchanged Phase 8 behavior |
| Plan profile exists, no subscription override | Plan profile's values | Report-only (see enforcement-wiring boundary) |
| Plan profile + active subscription override | Override's non-null fields win, others fall back to plan profile | Report-only |
| Subscription override with `effective_until` in the past | Override ignored, plan profile applies | Report-only |
| 1 Windows + 1 Android on a `WINDOWS:1, MOBILE:1` profile | Within policy | N/A (not yet wired to block) |
| 1 Windows + 1 Android + 1 more Android attempt on the same profile | Policy violation (`MOBILE` rule exceeded) — computable and testable | N/A (not yet wired to block) — real finding surfaced only via the resolution function's own test suite this phase, not a live activation rejection |
| `approval_required_after_device_number=1` and this is the 2nd device | Policy says "requires approval" | N/A — `ActivationPolicy.mode` (existing, live) remains the actual live gate this phase |

## Why "N/A (not yet wired)" is the correct, honest answer for several rows

Per `multi-device-policy-design.md`'s explicit boundary: this phase proves the policy resolves
correctly (real, tested), without touching the live Phase 8 activation enforcement path. A later phase
wires `resolve_device_policy()`'s result into `licensing_service/activation.py` alongside
`resolve_effective_device_limit()`, re-running the full Phase 8V-P9 physical regression before that
wiring is trusted.
