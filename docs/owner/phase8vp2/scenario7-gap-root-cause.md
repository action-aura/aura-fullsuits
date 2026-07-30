# Phase 8V-P2 — Scenario 7 Root Cause

Determined by reading the actual prior evidence (`docs/owner/phase8vp/scenario-7-plan-downgrade-evidence.md`
and `docs/owner/phase8vp/final-physical-validation-matrix.md`), not inferred from a summary.

## Expected behavior

A renewal that lowers a subscription's device allowance should, once applied, be enforced: the new,
lower limit should actively reject a genuinely new device activation attempt, exactly as if a staff
member had manually lowered the license's limit through some other means.

## Actual behavior (before this session's fix)

`apply_renewal_request()` (`owner/app/commercial_ops/renewal_requests.py`) wrote the new value to
`Subscription.device_allowance` and stopped there. `License.device_limit` — the field every real
device-limit check actually reads (`installations/services.py::count_slot_consuming_installations()`
compares against `commercial_ops/device_slot_ops.py::resolve_effective_device_limit()`, which reads
`License.device_limit`, never `Subscription.device_allowance`) — was never touched. No other route
anywhere in the codebase updated `device_limit` after a license's creation. A downgrade recorded on
the subscription had **zero enforcement effect** until a human manually edited the license row
through some means outside this UI.

## Exact failing layer

Owner service defect — a genuine, missing piece of business logic in
`owner/app/commercial_ops/renewal_requests.py::apply_renewal_request()`. Not a UI gap, not an
assertion-contract defect, not a commercial_runtime defect, not a product-enforcement defect, not a
reconciliation/queue defect (those all already worked correctly *given* a correct `device_limit` —
proven by the prior session using a manually-set value to exercise them). Not an environment gap
either.

## Severity

Medium-to-high from a commercial-correctness standpoint: a customer could be sold a downgrade that
Owner staff believe is enforced and it silently was not, until someone thought to check. Not P0/P1 in
the strict sense used elsewhere in this project (no crash, no data corruption, no security bypass —
the *existing* device slots and the over-limit-scan/notification/no-silent-deactivation machinery all
already worked once the number was right); it is a missing wiring step, not a broken one.

## Affected products/platforms

Owner-only. Neither `commercial_runtime` nor any product (Windows or Android) needed to change — a
product's device-limit enforcement has always correctly read whatever `License.device_limit` the
Owner database holds; the product side was never the problem.

## Whether a source correction is required

Yes — see `scenario7-resolution-report.md`. This is squarely "a real implementation defect within
Phase 8 scope" per this phase's own Part B framing: `apply_renewal_request()` already writes several
other subscription-level fields (`plan_id`, `end_date`, `status`) as part of applying a renewal;
completing that same function so it also keeps the one enforcement-relevant license field in sync is
finishing an already-scoped capability, not adding a new one.

## Exact tests required

Owner-domain: downgrade syncs `device_limit`; existing installations untouched; multiple licenses
under one subscription all sync; no audit noise when the value doesn't actually change; an audit
trail entry exists when it does; the synced value is what actually blocks a subsequent new
activation attempt. All seven implemented in
`owner/tests/test_commercial_ops_renewal_requests.py` — see `scenario7-final-evidence.md`.
