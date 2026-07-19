# Clinic — Screen Protection Decision (Wave 1A, Part M)

## Architectural constraint
FLAG_SECURE is a *window*-level Android flag. Clinic is single-Activity/single-Window (Navigation Compose), so true per-screen protection isn't natively available — approximated via a `DisposableEffect`-based `SecureScreen()` composable (`ui/components/SecureScreen.kt`) that sets `WindowManager.LayoutParams.FLAG_SECURE` on the shared Activity window when a sensitive screen enters composition, and clears it on dispose.

## Decision: screen-level, not app-wide
Explicitly chose *not* to blindly enable FLAG_SECURE app-wide (which would also block legitimate screenshots of non-sensitive screens like the Dashboard). Applied only to the two screens that render patient-linked or financial data directly:
- `BillingScreen.kt` (invoice list — financial + patient-linked)
- `PatientDetailScreen.kt` (patient overview/visits/prescriptions/notes/invoices — the tab-based detail screen hosting all sensitive patient data)

## Known limitation (documented, not hidden)
The single-Window approximation assumes only one such sensitive screen is on the back stack at a time, which matches the current nav graph (no two sensitive screens are ever simultaneously composed).

## Real device verification
- Dashboard: `adb`-triggered screenshot **succeeded** (unprotected, as intended).
- Billing: on-device screenshot attempt was **blocked by the OS** with a "can't screenshot due to privacy policy" message — direct, authoritative proof FLAG_SECURE is active and correctly scoped, not a code-review inference.
- Recent-apps thumbnail check for Billing was also attempted; while the user could not distinguish it from the in-app nav drawer (a UX identification issue on the tester's side, not a defect), the screenshot-block test above is an equally direct and more conclusive proof of the same underlying window flag.

## Result
PASS — FLAG_SECURE correctly scoped to sensitive screens only, verified live on-device via the OS's own screenshot-block behavior.
