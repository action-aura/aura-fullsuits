# Phase 8V-P4 — Handover

## One-paragraph status

For the first time across four sessions, a physical Android device was actually available and used
for real. Both products were really, physically, cryptographically activated against the real
Owner server on that device -- the single biggest open item across this entire multi-session Phase
8 closure effort. Two of seven commercial scenarios are now fully, physically proven end to end
(early renewal, pilot conversion); three more have their core mechanics proven real with specific,
disclosed sub-checks unreached; two (device replacement, plan downgrade) were not re-verified from
this device at all due to the single-device constraint, though their Owner-side mechanics remain
real and proven from prior sessions. Phase 8 remains CONDITIONAL PASS -- narrower than ever before,
but still not unconditional.

## Git state at handover

```
Starting commit this session: ab774e5f50c9f27ce646360385a90b8029fc8157
Conditional tag (unmoved):     aura-owner-commercial-ops-phase8-conditional-complete -> 7150564af95bd75cd475df57a6b42ae9ad4b3fb5
Final tag:                     NOT created (aura-commercial-licensing-operations-phase8-complete)
```

No product/Owner source changed this session (two temporary diagnostic prints were added and fully
reverted -- confirmed via clean `git status` before this session's commit).

## The physical device

Infinix X6528, serial `1122070476060894`, Android 13 / API 33. Real, present, used for hours of
real interactive testing this session, including one real mid-session USB disconnect and real
recovery (see `phase8vp4-baseline.md`).

## The real, previously-undocumented environment gap found and resolved this session

The `adb reverse tcp:5551 tcp:5551` tunnel silently drops on its own between setup and use (and
again after any `pm clear`/app-restart cycle) -- this looked exactly like the Android build's
`OWNER_LICENSING_BASE_URL` being broken (matching the exact symptom "Could not reach the licensing
service") and cost significant investigation time before being correctly root-caused as a dropped
tunnel, not a build defect. **Operational note for the next device session**: re-run
`adb reverse tcp:5551 tcp:5551` and confirm `adb reverse --list` shows it before every real
activation/check-in attempt, not just once at the start of the session.

## The real, new, low-severity UX finding

`android/aura-clinic/app/src/main/java/com/actionaura/clinic/ui/screens/LicensingScreen.kt` line
116: the license-key entry form only renders in `NOT_CONFIGURED`/`ACTIVATION_REQUIRED` states, never
in `DEVICE_DEACTIVATED` -- a customer who taps "Deactivate This Device" has no in-app path back to
reactivation (the confirmation dialog's own text, "You will need to reactivate with a license key,"
is not actually reachable from that screen). Not fixed this session (out of scope for a
validation-only phase); real, disclosed, worth a future small fix. Same file/logic almost certainly
applies to Retail's `LicensingScreen.kt` (not independently confirmed this session).

## Real synthetic licenses/installations created this session (all in the live `aura_owner_dev`
database, all fake data)

| Product | Subscription | License | Installation (device) | Key |
|---|---|---|---|---|
| Clinic (Scenario 1) | `0a6e6b36-...` | `adf83923-...` | `bb23591e-...` (deactivated mid-session for Scenario 4 setup) | `AURA-CLN-1-RUAC-...` |
| Clinic (Scenario 4/5, pilot) | `70c9bfa2-...` | `159212c1-...` | `de10cfb1-...` (currently active on-device) | `AURA-CLN-1-NBS4-...` |
| Retail (Scenario 2) | `4c9f5821-...` | `41670a9e-...` | `e77bd448-...` (currently active on-device) | `AURA-RET-1-D56D-...` |

## Real synthetic app-level accounts created this session

`phase8vp4-clinic-admin@example.com` and `phase8vp4-retail-admin@example.com`, both
`Sup3r-Str0ng-Pass!` -- these are **product-level** app accounts (Clinic/Retail's own onboarding),
distinct from the Owner-side staff accounts documented in earlier phases' handovers.

## Exact next session's scope

See `phase8-final-unconditional-decision.md`'s own "Recommended next session's exact scope" section
-- narrow, concrete, and achievable with the same device and same real Owner environment already
proven working this session (once the `adb reverse` operational note above is followed).

## Doc-set note

Every file this phase's own brief named was created. Where a scenario's evidence is genuinely
partial or absent, the file says so plainly (CONDITIONAL / NOT VERIFIED, with the specific reason)
rather than being invented or omitted -- consistent with every prior phase's own "no false
completion" principle.

## Standing rules unchanged from every prior phase

Original `AuraEnterprise` repo: read-only, always. No Phase 9 work. No fabricated physical evidence.
No new commercial-operations UI/features/domain redesign. Synthetic data only. No Android
signing-key regeneration. No destructive git operations. Never move the conditional tag. No
VPS/payment-gateway/WhatsApp/SMS/Aura-Core additions. Not publicly deployed, not production-operated,
not internet-scale, not ready for unsupervised public release, not yet ready for a controlled paid
pilot (Phase 8 itself remains conditional).
