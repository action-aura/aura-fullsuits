# Phase 8V-P3 — Logcat Review Plan (for the next device session)

Not executed this session (no device). Documented now so the next session can move directly to
capture without re-deriving the approach.

## Mechanism

`adb logcat -c` (clear) immediately before each scenario step that matters, then
`adb logcat -d > <scenario>-<product>.log` (dump) immediately after, scoped to the app's own process
where practical (`adb logcat --pid=$(adb shell pidof com.actionaura.clinic)` /
`com.actionaura.retail`) to avoid drowning the evidence in unrelated system noise.

## When to capture

Startup, activation, check-in, renewal, expiry, past-due transition, restricted-mode entry, pilot
conversion, emergency extension (both creation and expiry), device replacement, plan downgrade,
one deliberately invalid/corrupted assertion (to check the failure-path log output specifically, not
just the happy path), and one deliberate network failure (e.g. Owner briefly stopped mid-check-in).

## Search terms (grep the dumped log for all of these, per capture)

Full license key pattern (the product's own key format, e.g. `AURA-CLN-` / `AURA-RTL-` prefix
followed by the secret body), any PEM/base64 private-key-looking block, `X-Aura-Internal-Secret`
value, any Owner signing-key material, any keystore password, any raw Authorization/Bearer header
value, unredacted full request/response JSON bodies, patient names/IDs/appointment/visit/
prescription/invoice/payment content (Clinic), sale/receipt/stock/supplier/customer/transaction-total/
tax/discount content (Retail), internal staff notes, and any stack trace that embeds a secret or a
raw exception message from an unhandled 500 (as opposed to a safe, bounded reason code).

## Expected result

Zero matches for all of the above, in both products' logs, across all scenarios and both deliberately
adverse cases (invalid assertion, network failure) -- a network failure or invalid-assertion path
must still only ever surface a safe, bounded reason code in the log, never a raw exception or stack
trace containing request/response content.

## Where evidence goes

`docs/owner/phase8vp3/logcat-privacy.md` -- not created this session, since there is nothing real to
review yet.
