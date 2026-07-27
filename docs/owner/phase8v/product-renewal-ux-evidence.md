# Phase 8V — Product Renewal/Expiry UX Evidence (Part X)

## Scope actually delivered this pass

Milestone 5 introduced the `PENDING` activation decision but never gave either product client a
non-misleading message for it (Milestone 7 fixed the underlying Python handling; this phase's
research -- see `docs/owner/phase8/assertion-schema-and-product-ux.md` -- found the actual product-UI
code paths that needed the matching message). That gap is now closed on all four product surfaces:

| Surface | File | Change |
|---|---|---|
| Clinic Android | `LicensingScreen.kt` | New `result == "PENDING"` branch, informational (not error) message |
| Retail Android | `LicensingScreen.kt` | Same |
| Clinic Windows | `products/clinic/frontend/licensing.js` | New `status === 202 && body.result === 'PENDING'` branch |
| Retail Windows | `products/retail/frontend/licensing.js` | Same |

Clinic's Kotlin string map already had full Arabic coverage for its licensing screen and got a real
Arabic translation for the new message; Retail's Kotlin licensing screen has no Arabic entries at all
(pre-existing gap predating this phase, not backfilled here -- `tr()`'s own documented English
fallback means this degrades gracefully rather than breaking). Verified via a real Gradle build:
Clinic 82/82 unit tests green (after fixing a real regression this exact change caused in
`HardcodedStringAuditTest`, caught by actually running the build rather than assumed clean); Retail
build successful.

## What was NOT built this pass (broader Part X scope, disclosed not skipped)

The governing brief's Part X lists a much larger set of in-app messages (expires soon, renewal
pending, renewed, past due, grace, restricted, pilot ending, pilot completed, emergency extension
active/expiring, device replacement required, ...). Most of the underlying *local state* these
would display already exists and is already correctly labeled today (Phase 6/7's `stateLabel()` /
`STATE_LABELS` cover `ACTIVE_ONLINE`/`WARNING`/`GRACE_PERIOD`/`RESTRICTED`/`SUSPENDED`/`REVOKED`/
`EXPIRED` on both platforms already). What's net-new from Phase 8 and does NOT yet have a
product-facing string is renewal-in-progress/pilot/emergency-extension *commercial* metadata
specifically -- the nine new assertion fields (Part W) are present in every assertion today but no
product screen reads or displays them yet. Building that full UX pass (reading
`renewal_status`/`pilot_status`/`emergency_extension_id`/`commercial_grace_end` out of the local
state repository and rendering a dedicated string per value) is real, additional, estimable work,
correctly out of this closure phase's realistic scope given the PENDING-handling gap was the one
genuinely blocking item (Milestone 5 explicitly deferred enabling non-AUTOMATIC activation modes
until this got fixed). Recorded in `phase8v-residual-risk-register.md`.
