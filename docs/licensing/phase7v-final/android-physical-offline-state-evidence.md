# Phase 7V-F — Android Physical Offline-State Evidence (Part I)

## Status: NOT VERIFIED — device disconnected before physical Android offline testing began

## Equivalent evidence that was gathered live this session (Windows, same shared code)

The exact scenario this part requires — real Owner outage, real elapsed time, WARNING → GRACE →
RESTRICTED via a real short signed policy, no product-side fake clock — was executed twice, live,
on Windows (Clinic and Retail), and is the central finding of this session:

1. Activated against the real production-like Owner.
2. Real Owner process killed (`taskkill`/`kill -9`, genuine `ECONNREFUSED`).
3. Waited real wall-clock time (100+ real seconds, `sleep` inside a Monitor task, not simulated).
4. Single real check-in call → **`RESTRICTED`**, confirmed via the actual HTTP response.

This uncovered and led to fixing a real P0 defect in the trusted-time anchor mechanism (see
`checkin_scheduler.py`/`activation.py`/`trusted_time.py` — full root-cause writeup in
`final-regression-report.md` and the git commit history). Both Clinic and Retail Windows
confirmed reaching `RESTRICTED` correctly after the fix, with:

- No revocation/suspension/crash/data-loss on the first failed check-in.
- `ACTIVE_OFFLINE` correctly held during the safe window.
- Real database integrity confirmed (`PRAGMA integrity_check` → `ok`) throughout.
- No customer data transmitted in any of the offline retry traffic.
- Backups remained available throughout (proven earlier in the session for the upgrade sequence).

## Why this does not substitute for physical Android proof

Android's embedded Python runs the identical `commercial_runtime/licensing_contracts/` package, so
the *policy evaluation and trusted-time* logic is proven correct wherever it runs. What is NOT
proven by the Windows test: real Android process lifecycle behavior (Chaquopy thread scheduling,
app backgrounding, Doze mode/battery optimization potentially affecting a long-running check-in
scheduler), and the Kotlin↔Python sync hand-off under real conditions.

## Verdict

**NOT VERIFIED** (physical Android). **PASS** (shared logic, live on Windows, including the real
fix this session produced).
