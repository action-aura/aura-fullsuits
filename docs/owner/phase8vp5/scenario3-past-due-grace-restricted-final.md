# Phase 8V-P5 — Scenario 3 (Past Due / Grace / Restricted) — Final

## Result: PARTIAL PASS (Owner-tier mechanics reconfirmed for real; physical past-due-specific UX
not separately re-run this session -- shared restricted-state evidence from Scenario 5 substituted
with disclosure, per the evidence-reuse principle in `evidence-reuse-decision.md`)

## What is proven, and by what evidence

1. **Real expiry-scan mechanics (Owner tier)**: `owner/app/commercial_ops/expiry_scan.py`'s own
   docstring and code confirm the real, current behavior: it evaluates due dates against the real
   clock, transitions `Subscription.status` through `PAST_DUE`, and -- by explicit design --
   **never touches `License.status`**. Re-running the scan against an already-`PAST_DUE`
   subscription is idempotent (same subscription is not re-transitioned, no duplicate notification),
   consistent with Phase 8V-P4's own prior finding for this mechanism.
2. **The structural finding this session adds (see `phase8vp5-baseline.md` for the full source
   trace)**: `Subscription.status == PAST_DUE` alone produces **no local restriction** on an
   already-active installation, because `checkin.py::process_checkin()` never reads
   `Subscription.status` at all -- only `License.status` and `Installation.status`. This is a
   structural fact of the current architecture, not a bug: the two real mechanisms that *do* reach
   the device are (a) an explicit `License.status = SUSPENDED`/`REVOKED` transition (immediate at the
   Owner/check-in-rejection layer; see below) and (b) a non-default `OfflinePolicy` combined with real
   elapsed offline time (Scenario 5's mechanism).
3. **Real, physical proof that disconnecting Owner is not treated as past-due, and that technical
   offline grace and commercial past-due are separate**: the SUSPEND test (Scenario 2 evidence) showed
   a genuinely `License.status = SUSPENDED` license still serving `ACTIVE_OFFLINE` from a cached
   assertion for a real Retail sale (SALE-000002) -- proving Owner rejecting check-ins (400,
   `ACTIVATION_REJECTED`, `LICENSE_SUSPENDED` deliberately normalized as internal-only per
   `reason_codes.py`) does not by itself force any local state change; the device only moves once its
   own cached assertion's offline grace is exhausted. Scenario 5's dedicated short `OfflinePolicy` is
   the real, physical demonstration of exactly that separate technical-grace path reaching genuine
   on-device `RESTRICTED`, including the real product copy confirming records stay readable and
   backup/restore/export stay available in that state.
4. **Backend enforcement during real RESTRICTED**: reused directly from Scenario 5's real,
   physical `Save Patient` denial test (same physical device, same session) -- a UI action and a
   direct local backend request both failed to create a record, with a stable (if imprecisely
   worded -- "Couldn't reach the server", a disclosed minor UX finding, not this scenario's subject)
   error, no partial write.

## Why this is not a full, independent PASS

The governing spec asks Scenario 3 to walk the complete due-date -> signed-warning -> commercial-grace
-> real elapsed-time -> `RESTRICTED` sequence specifically through the past-due path, with its own
distinct warning/grace UX confirmed physically. Given the structural finding above -- that
`Subscription.status` transitions alone never reach the device -- that specific literal path (past-due
subscription causing local warning/grace/restricted UX) **does not exist in the current architecture**
to walk. What was walked and physically confirmed instead is the two mechanisms that are real: License
suspension (Owner-tier immediate, locally delayed) and offline-policy-driven restriction (Scenario 5,
fully physical). This is reported honestly as **PARTIAL PASS with a disclosed structural
substitution**, not a like-for-like completion of the spec's literal past-due UX walk, and is one of the
reasons the final unconditional tag is withheld (see `phase8-final-decision.md`).

## Cross-references

- `phase8vp5-baseline.md` -- full source trace of why `Subscription.status` never reaches the device.
- `scenario5-emergency-extension-and-expiry-final.md` -- the real, physical `RESTRICTED` evidence and
  backend-enforcement evidence this scenario relies on.
- Scenario 2 evidence -- the real SUSPEND/backend-still-serving-sale finding.
