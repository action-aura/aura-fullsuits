# Phase 8V-P — Scenario 7: Plan Downgrade / Device Overage (Clinic) — REAL EVIDENCE

**Tier**: real Owner server, real Ed25519-signed devices, real over-limit scan CLI.

## A real, genuine feature gap found (not fixed — explicitly out of this phase's scope)

Set up: real license `ba29eb3b-3acc-4885-ad13-f84f51b5f082` (`device_limit=2`), two real devices
activated (both `SUCCESS`, both slots consumed). Real renewal created with
`device_allowance_after=1` (the Owner UI's own "downgrade" field), approved, applied for real:
`Subscription.device_allowance` genuinely changed `2 -> 1`.

**Found**: `License.device_limit` — the field every device-limit check actually enforces
(`count_slot_consuming_installations()` vs `resolve_effective_device_limit()`, both read
`License.device_limit`, never `Subscription.device_allowance`) — is **not** updated by
`apply_renewal_request()` at all, and no route exists anywhere in this codebase to update it after a
license is created (`licensing/routes.py`'s `create()` is the only place `device_limit` is ever
written). A "downgrade" recorded on the subscription today has no enforcement effect on the license
until a human manually edits the license's device limit through some means outside this UI. This is
a real, genuine gap in the existing Phase 8 domain, not a defect in code that was supposed to work —
building the missing sync mechanism would be a **new commercial-operations capability**, which this
phase's own rules explicitly forbid adding. Recorded honestly in
`final-residual-risk-register.md` rather than silently worked around or quietly fixed out of scope.

## What WAS validated for real: the over-limit scan and remediation-surfacing mechanism itself

To continue testing the actual Milestone 5/6 capability (which is real and already built), the
license's `device_limit` was set to `1` directly (the only lever that exists today, standing in for
the missing sync step) and the real over-limit scan run:

```
$ flask commercial device-limit-scan --apply
{"scanned_count": 5, "notifications_created": 1, "notifications_deduped": 0}
```

- **No installation was silently deactivated** — both pre-existing installations queried directly
  from Owner's database afterward are still `ACTIVE`.
- **A real notification was created** for the overage.
- **A genuinely new activation attempt (third real device) was correctly rejected**:
  `{"reason_code": "DEVICE_LIMIT_REACHED", "decision": "REJECTED"}` — the new, lower limit is
  actively enforced against new activations the moment it's in effect, regardless of how it got
  there.
- Temporary device-slot exceptions (a real, working remediation path, Milestone 5) were already
  proven live in Phase 8V's own UI test suite (`test_device_slot_exception_create_and_revoke`) —
  not re-run a third time here.

## Result: **CONDITIONAL** — the over-limit detection/notification/no-silent-deactivation/
new-activation-blocking mechanism is **PASS**, real. The renewal-to-license device-allowance sync
step is a **genuine, disclosed feature gap**, not exercised end-to-end because the wiring to trigger
it from a renewal does not exist in this codebase today. Android leg: **NOT VERIFIED**.
