# Phase 8V-P4 — Backend Enforcement — **PASS (real, both direct-request and UI tiers)**

## Real evidence: LICENSE_INACTIVE blocks a protected mutation before activation

Before Clinic's real physical activation, a direct request to the embedded local backend was made
for real:

```
$ curl -X POST http://127.0.0.1:.../api/sub/clinic/patients -d '{"name": "Synthetic Patient One", ...}'
{"message": "This action is not available in the current licensing state.",
 "reason_code": "LICENSE_INACTIVE", "status": "error"}
```

Same real result surfaced through the actual app UI (not a bypassable client-side check -- the
identical backend call the UI itself makes): tapping `Save Patient` before activation produced
`"Couldn't reach the server"` on-screen -- a real, safe, non-crashing message, though its exact
wording is a **minor, real, disclosed UX finding**: it implies a network problem rather than the
true cause (no active license), which could mislead a real support technician. Low severity, not a
P0/P1 -- the *security* property (protected mutation genuinely denied, not silently allowed) holds
regardless of the message's precision.

## No UI-only bypass

The direct `curl` request (bypassing the UI entirely) got the identical `LICENSE_INACTIVE` denial
the UI did -- confirms the enforcement is server-side, not merely a disabled button.

## No partial mutation

The patient was **not** created in the local database -- confirmed by the Patients screen still
showing "No patients yet" after the failed attempt, and confirmed again by the *first successful*
patient creation only happening after real activation completed.

## After activation: protected mutation succeeds normally

Post-activation, the identical UI flow (`Save Patient`) succeeded for real -- `Synthetic Patient
One`, code `P-1785442202-782`, `active`. Confirms the guard is state-driven, not a permanent block.

## Not independently exercised this session

Testing the SAME guard in a genuinely RESTRICTED (post-activation, then-expired) state specifically
-- not reached this session for the reasons in `scenario3-past-due.md`. Retail's own capability
guards (protected sale/inventory mutations) were not separately probed via direct request this
session (Retail was activated before any pre-activation mutation attempt was tried against it).

## Result: **PASS** for the one real, concrete case exercised (Clinic, pre-activation,
`LICENSE_INACTIVE`) -- server-side, no UI-only bypass, no partial write, safe reason code. Not
exhaustively re-run across every commercial state this session.
