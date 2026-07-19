# Language / RTL Device Validation (Wave 1A, Part O)

## Method
On real device, both apps' Settings → language switch to Arabic exercised directly.

## Retail
Tapping Settings **crashed the app** (MOB-006 — a stale `nav.navigate("settings")` route reference; the real route is `"retail_settings"`). Fixed, rebuilt, reinstalled, and reconfirmed working (Settings opens without crashing) before language switching could be tested further this session.

## Clinic
RTL layout mirroring works correctly — switching to Arabic visibly flips the UI right-to-left, confirming the underlying `tr()`/RTL infrastructure (`AppLocale.isRtl`-driven `LayoutDirection`) functions correctly. However, most screens (Dashboard, Patients, Appointments, Billing, and others) still show English text — only Login, the error-classifier messages (payment/login/appointment), backup/restore strings, and role names are actually wired to `tr()`. Registered as **MOB-007**, a real but large-scope localization-coverage gap, and explicitly deferred to Wave 1B per user decision (see defect registry and residual risk register).

## Result
MOB-006 (Retail crash) FIXED and verified. MOB-007 (Clinic translation coverage) OPEN, deferred — RTL mechanism itself verified working, string coverage is the outstanding gap.
