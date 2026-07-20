# Wave 1B — Clinic Arabic Localization Completion (MOB-007)

## Scope of the audit
Every Clinic Android screen file was read and audited for hardcoded English UI text: `DashboardScreen.kt`, `PatientsScreen.kt`, `AppointmentsScreen.kt`, `BillingScreen.kt`, `ClinicExtraScreens.kt` (Doctors/Prescriptions/Lab Expenses), `SettingsScreen.kt`, `SetupScreen.kt` (onboarding), `PatientDetailScreen.kt` (Overview/Visits/Prescriptions/Invoices/Notes tabs), `LoginScreen.kt`, `BackupRestoreScreen.kt`, `Pickers.kt`, and the shared `Components.kt`. `net/ApiErrors.kt` (payment/login/appointment error classifiers) was already fully translated in Wave 1A.

## What was found
Prior to this wave, only a handful of screens were wired to `tr()`: Login, the HTTP-error classifiers, Backup/Restore, and role names. Every other screen — Dashboard's greeting/KPIs/quick actions, the entire Patients list and add-patient sheet, the entire Appointments booking flow (including buttons added during Wave 1A's MOB-005 fix, which were never translated), the entire Billing/invoice flow including the newly-added invoice drill-down sheet, Doctors/Prescriptions/Lab Expenses, and most of Patient Detail — had raw English string literals. RTL layout mirroring itself worked correctly (proving the `tr()`/`AppLocale` mechanism was sound); only string coverage was incomplete.

Also found and fixed as part of this audit: `SettingsScreen.kt`'s "About → Version" row showed a hardcoded fake `"2.5.0-beta.1"` instead of the app's real version — this is Clinic's actually-routed settings screen (confirmed via `AppRoot.kt`'s `composable("settings")`), so this was a real, live, user-visible defect, not cosmetic dead code. Fixed to read `BuildConfig.VERSION_NAME`. Retail's equivalent screen (`RetailSettingsScreen`) had no About/Version section at all; added one for parity.

## What was fixed
- ~150 `Text(...)`, `label = { Text(...) }`, `placeholder = { Text(...) }`, `EmptyState(title=...)`, snackbar-message, and error-message call sites across 8 screen files rewired to `tr("...")`.
- Domain status values shown via the shared `StatusChip` composable (`active`, `archived`, `scheduled`, `waiting`, `in_progress`, `completed`, `cancelled`, `no_show`, `unpaid`, `partial`, `paid`, `recorded`) now translate — these come straight from backend data, so the fix is a `tr(status)` lookup keyed on the raw backend string, not a display-label rewrite; the value sent back to the API is never affected.
- Payment method dropdown (`cash`/`card`/`insurance`/`bank_transfer`) now shows translated labels while still submitting the canonical English value to the backend (the display string and the submitted value are drawn from two separate parallel lists specifically so translating the label can never change what's sent over the wire).
- 99 new English→Arabic entries added to `Strings.kt` (8 more were already present with reusable, contextually-correct translations from the pre-existing shared string map and were not duplicated).

## Automated checks (new this wave)
Two JUnit tests audit the string map and screen source directly, so a future regression is caught without a device:
- `StringsCoverageTest` — no blank English/Arabic entries, no duplicate English keys, no accidentally-identical English/Arabic value (allow-listing only genuine exceptions like the brand name "Action Aura").
- `HardcodedStringAuditTest` — heuristic scan of every Clinic screen file for capitalized string literals not wrapped in `tr(...)`, allow-listing genuine non-UI text (the `"UTC"` timezone id) and the one legitimate translated-via-indirection case (the time-of-day greeting, which must stay a raw key stored in a variable so it re-translates live on a language switch rather than being baked in once).

Both pass: 49/49 Clinic Kotlin tests total (45 baseline + 4 new).

## Explicitly out of scope / not claimed
- Pluralization: no plural-sensitive strings were found in Clinic's current UI (counts are always shown as raw numbers next to a fixed-form label, e.g. "Total Patients: 12"), so no pluralization logic was added. If a future string needs it, `String.format`-style templates already exist elsewhere (`"%s in stock"` pattern in Retail's map) as a precedent.
- Long Arabic text wrapping: not adversarially tested against extremely long strings (e.g. a very long clinic name); existing `Column`/`Row` layouts use `Modifier.weight(1f)` and wrapping `Text`, which should reflow correctly, but this was not stress-tested with pathological input.
- Accessibility descriptions (`contentDescription`): audited and found to be `null` throughout (icons are decorative alongside visible text labels), so there was nothing to translate here — not a gap introduced or left by this wave.

## Physical device verification
**Status: FIXED AND VERIFIED**, with real evidence, not just code review. Device reconnected (Infinix X6528) and the real signed release build tested live:

1. First pass: user reported "patients and billing in clinic are not translated as titles, everything else is fine" — a precise, real finding.
2. Root cause: `AppRoot.kt` (nav tab labels, top-bar title `when` block, drawer items, AI-sheet suggestion chips) was never included in the original per-screen-file audit above — a different file from `ui/screens/*.kt`, easy to miss doing a folder-by-folder pass. `"Patients"` and `"Billing"` simply had no `Strings.kt` entry at all (distinct keys from `"Patient"` singular and various Billing-screen-*content* strings, which did exist); `"Doctors"`, `"Lab Expenses"`, and the drawer's `NavigationDrawerItem` labels for them were flat-out unwrapped (no `tr()` call at all).
3. Fixed: added the 8 missing `Strings.kt` entries (`Patients`, `Billing`, `Doctors`, `Lab Expenses`, plus the 4 AI-sheet suggestion strings), wrapped the drawer items and the `"Send"` icon content-description in `tr()`.
4. `HardcodedStringAuditTest` extended to also scan `AppRoot.kt` (previously `ui/screens/*.kt` only) — this exact gap is now guarded against for future changes, with the `Dest(...)` lookup-key definitions and brand names (`Action Aura`, `Aura AI`) correctly allow-listed rather than wrongly flagged.
5. Rebuilt, reinstalled the real signed release APK on-device, user re-confirmed: **"all good."**

This is now a real, device-verified, closed defect (tracked as part of MOB-007's closure, not a separate ID, since it's the same underlying "Clinic Arabic coverage" gap this whole report addresses) — not a claim made from code review alone.
