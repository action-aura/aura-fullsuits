# Phase 8V-P5 — Baseline

## Hard entry gate: PASSED

```
$ adb devices -l
1122070476060894  device product:X6528-OP model:Infinix_X6528
```

Same Infinix X6528. 5-minute stability check: 6/6 iterations `state=device`, zero disconnects.

## Git baseline

```
HEAD: 504e8d2f1bd5599b9514b02735e0e31e433dbc31
Conditional tag: aura-owner-commercial-ops-phase8-conditional-complete -> 7150564
Final tag: does not exist
Working tree: clean
```

## Real, long-duration persistence confirmed (bonus finding, stronger than a force-stop test)

Both products' installations survived the multi-hour gap between the Phase 8V-P4 and 8V-P5
sessions untouched, with no app relaunch or interaction in between:

```
Clinic: installation_id de10cfb1-... unchanged, ACTIVE_ONLINE, same assertion
Retail: installation_id e77bd448-... unchanged, ACTIVE_ONLINE, same assertion
```

## Owner environment this session

Started with a **real wire-capture middleware** wrapping the actual Owner WSGI app (not a proxy --
observes the real app's own request/response handling at its WSGI boundary), logging every real
`/api/licensing/v1/*` exchange to a local, redacted JSONL file. See `raw-wire-capture-plan.md`.

`flask commercial preflight` -> `ok: true`. `adb reverse tcp:5551 tcp:5551` re-established and
confirmed via `adb reverse --list`.

## Critical structural finding this session (read source before assuming a bug)

Deep-read of `owner/app/licensing_service/checkin.py`,
`owner/app/commercial_ops/state_resolution.py`,
`commercial_runtime/licensing_contracts/policy_evaluator.py`, and
`commercial_runtime/licensing_contracts/capability_guard.py` before attempting Scenario 2/3/5 again,
specifically to understand why three consecutive prior sessions could never get the product to show
a local `RESTRICTED` state from a subscription-level commercial lapse. Confirmed by source, not
guessed:

- `process_checkin()` gates only on `License.status` (`SUSPENDED`/`REVOKED`/`EXPIRED`) and
  `Installation.status` -- it never reads `Subscription.status` at all. A subscription going
  `PAST_DUE` or `EXPIRED` does not, by itself, change what a check-in returns.
- `commercial_ops/expiry_scan.py`'s own docstring confirms this is deliberate: it "only ever calls
  the existing `transition_subscription()`... never License."
- The local `evaluate()` policy evaluator (`policy_evaluator.py`) likewise only branches on
  `license_status`/`installation_status` plus *offline elapsed time* against the assertion's own
  embedded offline policy -- never on `subscription_status`.
- The seeded default `OfflinePolicy` (`hard_expiry_behavior="WARN_ONLY"`) can **never** produce
  `RESTRICTED` at all, by design -- grace-exhausted under `WARN_ONLY` returns `GRACE_PERIOD`
  forever, not `RESTRICTED`.

**Conclusion**: this is intentional design ("past-due must NOT automatically mean revoked/blocked"
per the code's own comment), not a defect. The two real, product-provided mechanisms that *do*
produce a locally-enforced restricted state are: (a) explicit staff `License.status = SUSPENDED`
(coarse, immediate, staff-driven, fully audited -- the real hard-block mechanism this system
actually ships), and (b) a license assigned a **non-default** `OfflinePolicy` with
`hard_expiry_behavior="RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA"`, reached only after real
elapsed offline time exceeds that policy's own `offline_grace_seconds + retry_interval_seconds`.
Both are exercised for real this session -- see `scenario2-retail-late-renewal-final.md` (path a)
and `scenario5-emergency-extension-and-expiry-final.md` (path b).
