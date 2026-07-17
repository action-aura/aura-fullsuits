# Phase 4 — Android Residual Risk Register

Every item below is a known, deliberately-not-fully-resolved gap after
this phase. Nothing here is hidden.

## No device/emulator available in this environment

Certain, unmitigated within this phase's environment. `adb devices` is
empty, no `emulator` package is installed under the Android SDK. Every
runtime/UI/camera/RTL-rendering/restart-persistence claim in this phase's
reports is marked `NOT TESTED` rather than assumed. **This is the single
largest gap for real paid-pilot readiness** — nothing in this phase
constitutes evidence the apps actually run correctly on a device.

## Clinic payment rejection UX surfaces through the wrong code path

`PaymentSheet`'s `if (r.status == "success")` branch is written for a
2xx JSON response; the real backend rejections (overpayment, zero/negative
amount) are HTTP 400, which Retrofit's default behavior turns into a
thrown `HttpException` rather than a deserialized `CreatePaymentResponse`
with `status: "error"`. The generic `catch (e: Exception) { error =
"Couldn't reach the server" }` block therefore catches real rejections too,
showing a misleading message ("couldn't reach the server" for a request
that *did* reach the server and was correctly rejected). **The payment is
never incorrectly recorded as successful either way** — this is a message
-accuracy gap, not a financial-authority or data-integrity gap. Not fixed
this phase: the same generic-catch idiom is used consistently throughout
both apps' existing checkout/return/invoice code (pre-existing convention,
not introduced by this phase), and fixing it project-wide would be a
larger UX-consistency pass beyond this phase's financial-contract-
correction mandate. Recommended for Wave 1.

## No client-side role-gated navigation (Clinic)

`clinic_role` is deserialized but never consumed by any navigation-gating
logic (grep-confirmed). Backend RBAC (`clinic_rbac_test.py`) is the actual
enforcement boundary — a Doctor/Secretary account cannot perform an
Admin-only action even if the UI shows the option, because the server
rejects it. This is a UX/parity gap (a Secretary sees Admin-only screens
that will error on submission), not a security gap. Not implemented this
phase (would be a new feature, not a migration correction).

## No discount-entry UI (Retail)

`discount_pct` on `SaleItemReq` always defaults to `0.0` — there is no
cart UI to enter a per-line discount. This was true before this phase too
(not a regression); the field exists on the wire contract specifically so
a future UI addition doesn't require another backend/model change.

## `FLAG_SECURE` not set (Clinic)

Recent-app-preview/screenshot protection is not implemented — documented
explicitly in `clinic-android-privacy-boundary.md`, not silently claimed.
Recommended for Wave 1 if screen-privacy on shared devices becomes a real
deployment concern.

## No backup/restore UI in either app

The backend `/api/backup/create`/`/api/backup/restore` endpoints
(Phase 3.7 / Wave 0) have no corresponding Android UI screen in either
product. A device running either app today has no in-app way to trigger a
backup. Not a regression (never existed), but a real functional gap for a
mobile deployment.

## Unsigned release artifacts

By design — no production signing key exists yet. Not a defect, but a
hard blocker for any real distribution (sideloading an unsigned APK works
for testing; the Play Store and most MDM systems require a signed,
consistent-key artifact for updates).

## `bundleRelease`'s AAB filename/task-name naming can mislead

`app-release.aab` (no `-unsigned` suffix, unlike the APK path) and a task
literally named `signReleaseBundle` both run even with no signing config
present. Verified genuinely unsigned via `jarsigner -verify` this phase —
documented explicitly (`android-signing-guide.md`, both build reports) so
this naming quirk doesn't get misread as "signed" in a future session.

## Backend regression gate: only the touched file was risk-relevant

`clinic_api.py`'s two-line privacy fix was the only backend file changed
this phase. The 279-test rerun covers this correctly, but it's worth
noting explicitly: no other backend file was touched, so a hypothetical
narrower regression run (just `clinic_workflow_test.py`) would have been
technically sufficient — the full rerun was still performed for maximum
confidence, per the addendum's explicit instruction not to under-scope
this gate.
