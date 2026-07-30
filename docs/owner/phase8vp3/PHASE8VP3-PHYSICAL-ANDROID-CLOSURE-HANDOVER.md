# Phase 8V-P3 — Handover

## One-paragraph status

Nothing changed about the substance of Phase 8 this session -- it was already fully proven at the
Owner/service tier and, for Windows, at the real-installed-product tier. This session's job was
narrowly to run it on a physical Android device; none was connected (third consecutive session with
this exact result). One genuinely useful new thing was found and documented: the current rc.3
Android artifacts need a one-command rebuild (`-PownerLicensingBaseUrl=...`) before they're usable
for a licensing physical-validation session -- previously undocumented, now solved in writing so the
next session doesn't have to discover it mid-validation.

## Git state at handover

```
Starting commit this session: 4a87894aef3357d9f311d426c1e792722b60b26c
Conditional tag (unmoved):     aura-owner-commercial-ops-phase8-conditional-complete -> 7150564af95bd75cd475df57a6b42ae9ad4b3fb5
Final tag:                     NOT created (aura-commercial-licensing-operations-phase8-complete)
```

This session's own commit(s): documentation only, `docs/owner/phase8vp3/` plus additive pointers in
the Phase 8V-P2 decision/risk docs. No `owner/`, `commercial_runtime/`, `products/`, or `android/`
source changed -- run `git show --stat HEAD` to confirm.

## The one remaining blocker, stated plainly (unchanged for the third session running)

No physical Android device was connected to this machine during this session (`adb devices -l`
returned an empty list, checked for real). See `device-readiness.md`.

## The one new thing the next session needs to know that wasn't documented before

The rc.3 APK/AAB files in `dist/android/{clinic,retail}/` currently have `OWNER_LICENSING_BASE_URL=""`
compiled in (the deliberate fail-safe default for a build with no `-PownerLicensingBaseUrl` gradle
property passed). Installing them as-is will show licensing `NOT_CONFIGURED`, not a working
connection. **Before installing for a real licensing session**, rebuild both products with:

```
adb reverse tcp:5551 tcp:5551
cd android/aura-clinic && ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 testDebugUnitTest lintRelease assembleRelease bundleRelease
cd ../aura-retail && ./gradlew --no-daemon -PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1 testDebugUnitTest lintRelease assembleRelease bundleRelease
```

(Adjust the port if the real dev Owner server is running on a different one that session -- see
`docs/owner/phase8vp2/PHASE8VP2-FINAL-ANDROID-CLOSURE-HANDOVER.md` §6.3 for how to start it.) This
will produce new APK/AAB bytes and a new SHA-256 -- expected, not a regression -- re-verify
certificate continuity as a matter of course afterward (same keystore, should be unchanged).

## Everything else needed for a device session is already in place

- Owner environment: real, preflight-clean (`ok: true`), reconfirmed live this session.
- Automated regression: 394/394 owner, 219/219 commercial_runtime, both reconfirmed at this exact
  HEAD.
- Certificate continuity: reconfirmed, full SHA-256 match, both products.
- Traffic-capture and Logcat-review methodology: pre-written (`traffic-capture-plan.md`,
  `logcat-review-plan.md`) so the device session can move straight to execution.
- Synthetic test credentials from prior sessions still valid: `phase8vp-admin@example.com` /
  `Sup3r-Str0ng-Pass!` (no MFA, super admin) -- see the Phase 8V-P handover for the MFA-enrolled
  approver account and its TOTP secret.

## Doc-set note (same principle as Phase 8V-P2)

Per-scenario evidence files (Parts H-N), the signed-installation-upgrade file (Part E), and the
persistence/enforcement/traffic/Logcat/data-preservation evidence files (Parts O-S) are **not
created** this session -- they would require actual device execution, and empty or templated
versions would misrepresent untested work as evidence. `physical-validation-matrix.md` and
`final-physical-validation-matrix.md` are the honest record of what remains open and why.

## Standing rules unchanged from every prior phase

Original `AuraEnterprise` repo: read-only, always. No Phase 9 work. No fabricated physical evidence.
No new commercial-operations UI/features/domain redesign. Synthetic data only. No Android
signing-key regeneration. No destructive git operations. Never move the conditional tag. No
VPS/payment-gateway/WhatsApp/SMS/Aura-Core additions. Not publicly deployed, not production-operated,
not internet-scale, not ready for unsupervised public release.
