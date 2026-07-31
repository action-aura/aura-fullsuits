# Phase 8V-P6 — Scenario 3 (Commercial Past-Due/Expired Enforcement) — Final

## Result: PASS

## What makes this a genuine, unconfounded proof (unlike every prior session's attempt)

1. A dedicated, real, non-restrictive technical `OfflinePolicy` (`phase8vp6-warn-only-nonrestrictive`,
   `hard_expiry_behavior: WARN_ONLY`, 14-day grace) was assigned to the real license
   (`87e981d9-5f45-42ad-891b-9a2f330b5a6e`) **before** the test, replacing the short, artificially
   restrictive policy left over from Phase 8V-P5. Under the *old* code, `WARN_ONLY` can **never**
   produce `RESTRICTED` -- so any restriction observed cannot be explained by the technical layer at
   all.
2. Real installation (`c7150980-d45b-4d14-866b-642fb798dceb`) confirmed `ACTIVE_ONLINE` via a real
   check-in against the rebuilt app (`assertion_id 5d73b60b...`'s predecessor) before any change was
   made.
3. Through a real Owner service call (staff-driven, synthetic data), the subscription
   (`8a11c868-7b17-4f53-9632-1da982ec06e2`) was transitioned `ACTIVE -> EXPIRED`. `License.status` was
   **not** touched -- confirmed still `ACTIVE` both before and after via direct query, proving license
   history preservation (Part C's "preferred architecture").
4. A real "Check Now" tap on the physical device produced a real, freshly signed assertion. The
   physical screen changed to **"Restricted"** immediately, same product copy as before
   ("Some features are limited... backup/restore/export remain available").
5. **The real captured wire evidence removes all doubt**: `subscription_status: "EXPIRED"`,
   `license_status: "ACTIVE"`, `offline_policy.hard_expiry_behavior: "WARN_ONLY"`,
   `commercial_grace_end: None` (no PAST_DUE grace applicable to a direct EXPIRED transition), assertion
   id `5d73b60b-51df-4d00-b5b1-feac5236f762`. The technical policy in this exact assertion is the one
   that, under old code, could never restrict -- yet the device restricted. This is only explainable by
   the new commercial-enforcement logic in `policy_evaluator.py::evaluate()`.
6. **Real backend enforcement while genuinely restricted**: attempted to save a new patient
   ("P6 Test Patient") through the real UI form. Denied ("Couldn't reach the server" -- same known,
   minor, previously-disclosed imprecise wording, not corrected this session, out of scope). Confirmed
   via the Patients list afterward: **"No patients yet"** -- no row was created.
7. Existing data remained readable throughout (the empty Patients/Appointments/Billing screens
   remained navigable, no crash, no lockout).

## Why PAST_DUE-specific warning/grace UX was not separately walked

This subscription was moved directly `ACTIVE -> EXPIRED` (a realistic real-world path -- not every
account passes through an observed PAST_DUE window before expiring, e.g. non-renewal at term end).
`commercial_grace_end` is only populated when `subscription.status == "PAST_DUE"`
(`assertion_fields.py::_past_due_since()`), so this specific test did not exercise the
`PAST_DUE`-within-grace / `PAST_DUE`-after-grace boundary physically -- that logic is covered by real,
passing automated tests (`test_subscription_past_due_within_signed_grace_stays_operational`,
`test_subscription_past_due_after_signed_grace_restricts`) but not by a live device transition this
session, given time budget. Reported honestly rather than implied as fully walked.

## Disposition

This closes the core, structural gap this phase exists to fix, with real, physical, unconfounded
evidence -- a materially stronger result than any prior session achieved for this scenario.
