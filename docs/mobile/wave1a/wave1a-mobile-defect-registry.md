# Wave 1A Mobile Defect Registry

All defects below were found through real physical-device testing (Infinix X6528, Android 13) during Wave 1A, not code review. Each was fixed, rebuilt, reinstalled, and re-verified on the same device before being marked resolved.

## MOB-001 — Retail: taxed cash sales rejected as "credit sale"
- **Found**: worked example checkout (Test Product A, $100 + 10% tax = $110) failed with "Credit sales require a customer (walk-in not allowed)" on every attempt.
- **Root cause**: `CreateSaleRequest.amount_paid` was populated with the client's local pre-tax preview total for all payment methods, including full-payment cash. The server computes `balance_due = total - amount_paid`; since the client-side amount was less than the authoritative (tax-inclusive) total, the server saw an implicit partial payment and required a customer.
- **Fix**: `amount_paid` made nullable; omitted entirely for non-credit payment methods so the server defaults it to its own computed total.
- **Files**: `android/aura-retail/app/src/main/java/com/actionaura/retail/net/Models.kt`, `.../ui/screens/RetailScreens.kt`
- **Verification**: direct curl reproduction, then full rebuild+reinstall+real UI retry producing `SALE-000005` (subtotal 100, tax 10, total 110, amount_paid 110).
- **Status**: FIXED, verified on device.

## MOB-002 — Clinic: embedded backend never started
- **Found**: Clinic's on-device Flask/waitress server had no listening socket on any launch; login always failed.
- **Root cause**: Chaquopy's `pip` dependency block in `android/aura-clinic/app/build.gradle` was missing `requests`, which `clinic_api.py` imports at module level (`import time, requests`) for a best-effort, off-by-default event-bus POST. The original import scan that trimmed the pip list missed the compound import statement.
- **Fix**: added `install "requests==2.32.3"` to Clinic's Chaquopy pip block (Retail's build.gradle already had it correctly).
- **Verification**: `/api/health` returned `200 {"status":"ok"}` on-device after rebuild; also reconfirmed present in the release build's Chaquopy pip install log this session.
- **Status**: FIXED, verified on device.

## MOB-003 — Clinic: payment rejections showed "Couldn't reach the server" for any real HTTP error
- **Found**: a real device payment test (overpayment, zero/negative amount, missing invoice) always showed a generic connectivity message instead of the actual reason.
- **Root cause**: Retrofit suspend functions returning a plain body type throw `HttpException` for any non-2xx response instead of deserializing it, so the `r.status == "error"` branch written for business-logic rejections was unreachable for real HTTP-level rejections (400/404/500) — they fell into a generic `catch (e: Exception)`.
- **Fix**: `paymentErrorMessage(e: Throwable)` classifier in new `android/aura-clinic/app/src/main/java/com/actionaura/clinic/net/ApiErrors.kt`, wired into `BillingScreen.kt`'s `PaymentSheet`. Distinguishes 404 (invoice not found), 400+"greater than zero", 400+"must be a number", 400+"exceeds the outstanding balance", 5xx, and network/timeout failures. Never shows raw exception text.
- **Tests**: 10 unit tests in `ApiErrorsTest.kt`.
- **Verification**: live device test — recording a $150 payment against a $100 invoice showed "This amount is more than what's owed on this invoice."
- **Status**: FIXED, verified on device.

## MOB-004 — Clinic: login failures always showed "Couldn't reach the server"
- **Found**: attempting login with wrong credentials (the tester tried Retail's admin credentials against Clinic, which has a separate registry) showed "Couldn't reach the server" even though the server was demonstrably healthy (confirmed via direct curl returning a real `401 {"error":"Invalid email or password."}`).
- **Root cause**: same systemic gap as MOB-003, in `LoginScreen.kt`'s catch block. Real backend responses are 401 (bad credentials), 403 (disabled account), 429 (locked out), 400 (missing fields) — see `commercial_runtime/identity/auth_routes.py`.
- **Fix**: `loginErrorMessage(e: Throwable)` added to `ApiErrors.kt`, wired into `LoginScreen.kt`.
- **Tests**: 7 unit tests added to `ApiErrorsTest.kt`.
- **Status**: FIXED, verified on device (wrong-credential retry showed "Incorrect email or password.").

## MOB-005 — Clinic: appointments booked with malformed free-text dates silently vanished
- **Found**: a real device appointment booking ("saved it yet doesn't show on appointments") succeeded (200, `id: 1` created) but never appeared in any schedule view.
- **Root cause**: the date/time field was raw free text ("YYYY-MM-DD HH:MM" hint only, no validation). A real typed entry (`2026-7-18`, missing zero-padding and time) was accepted by the backend (which only validates `patient_id`) but stored a string SQLite's `date()` function could never match against `date(appointment_dt)=?` schedule queries — the appointment was permanently invisible while still sitting in the database.
- **Fix**: replaced free text with native Material3 `DatePicker`/`TimePicker` dialogs in `AppointmentsScreen.kt`'s `BookAppointmentSheet`, guaranteeing a canonical `yyyy-MM-dd HH:mm` string. Added `appointmentErrorMessage()` classifier (404/409/400/5xx) for real booking-time HTTP rejections (same systemic class as MOB-003/004).
- **Follow-up (same investigation, not a separate device defect)**: the Appointments screen could only ever show a single exact day at a time, with no way to browse forward — a legitimately booked appointment for a future date looked "lost" the same way. Backend `list_appointments` extended with an additive `from_date` range mode (`WHERE date(appointment_dt)>=?`, existing `date=` exact-match behavior untouched for backward compatibility); client now defaults to an unbounded "upcoming from today" view grouped by date, with a date-picker to move the window's start.
- **Files**: `android/aura-clinic/.../ui/screens/AppointmentsScreen.kt`, `net/ApiErrors.kt`, `net/AuraApi.kt`, `products/clinic/backend/api/clinic_api.py`.
- **Tests**: 4 unit tests for `appointmentErrorMessage()`; backend `clinic_workflow_test.py` (29/29) unaffected.
- **Status**: FIXED, verified on device (today + tomorrow appointments both correctly visible after fix).

## MOB-006 — Retail: app crashed opening Settings
- **Found**: tapping "Settings" in the Retail nav drawer crashed the app.
- **Root cause**: `IllegalArgumentException: Navigation destination that matches route settings cannot be found in the navigation graph` — the drawer's `NavigationDrawerItem` called `nav.navigate("settings")`, a stale route name left over from an earlier rename; the actual registered route is `"retail_settings"`.
- **Fix**: corrected the route string and the `selected =` comparison in `AppRoot.kt`; removed a matching dead `"settings" -> "Settings"` label-lookup branch.
- **Status**: FIXED, verified on device (Settings opens without crashing).

## MOB-007 — Clinic: Arabic mode only partially translated (registered, not fixed this wave)
- **Found**: switching Clinic to Arabic correctly mirrors the layout to RTL (proving the underlying `tr()`/RTL infrastructure works), but most screens (Dashboard, Patients, Appointments, Billing, and others) show English text — only Login, payment/login/appointment error messages, backup/restore strings, and role names call `tr()`.
- **Scope**: large — likely 100+ untranslated strings across most screen files, a localization-coverage gap rather than a bug in the existing mechanism.
- **Decision**: per explicit user direction, registered as a known gap and deferred to Wave 1B rather than attempted as an in-session fix (out of proportion to a device-testing wave; see residual risk register).
- **Status**: OPEN, deferred to Wave 1B.
