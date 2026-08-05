# Presentation Authority Audit (M6.0)

Real, cited audit of the existing, currently-shipping native Android
Retail app at `android/aura-retail/app/src/main/java/com/actionaura/retail/`
— a real Compose + Chaquopy-embedded-Flask app, entirely separate from
the new KMP "unified mobile" initiative (`mobile/aura-retail-unified/`).
Read in full (every screen file, nav graph, API client, licensing
flow, barcode mechanism, i18n, theme, session holder), not sampled.
Detailed per-route/per-screen breakdown lives in
`mobile-screen-route-matrix.md`; this document covers the structural
findings that matrix rows alone don't capture.

## Navigation graph — real, confirmed structure

`AppRoot.kt` is a 4-phase state machine (`LOADING`/`SETUP`/`LOGIN`/`READY`),
not itself a `NavHost` — only the `READY` phase's `MainShell()` contains
a real `NavHost`/`NavController`. **17 real `composable()` destinations**
exist in `retailGraph()`, confirming the checkpoint's own claimed count
exactly (full list in the matrix). 4 are bottom-nav tabs
(`dashboard`/`pos`/`products`/`more`); the other 13 are reached through
the `more` hub or `retail_settings`. There is **no nested/path-argument
route** anywhere (no `"products/{id}"`) — every detail view (sale
receipt, PO detail, customer/supplier statement, edit-product) is a
`ModalBottomSheet`/`AlertDialog` overlay driven by local `remember`
state, not a distinct nav-graph destination. This is a real, useful
precedent for M6.7's own typed-navigation design: most "detail" screens
in the new shared app can legitimately be sheets/dialogs rather than
full routes, matching the existing product's own UX shape rather than
inventing a different one.

## API coupling — real, universal local-Flask dependency

Every real screen except the dead `SettingsScreen.kt` (unrouted) and
the pure-UI `Pickers.kt`/`BarcodeScanner.kt` calls
`net.ApiClient.get()` — Retrofit+OkHttp+Gson against
`http://127.0.0.1:<dynamic-port>/`, the embedded Chaquopy/Flask server
started once per app-process lifetime from `AppRoot`'s
`LaunchedEffect(Unit)` (never explicitly stopped). Auth is a Flask
session cookie persisted to `SharedPreferences`, not a bearer token.
**None of this loopback/Chaquopy architecture is reused by M6** — the
checkpoint's own explicit instruction ("no Chaquopy migration into the
unified app... no local Flask server... no HTTP loopback for M6
vertical slices") is the correct call given this audit: every M6
vertical slice must call real shared Kotlin use cases directly against
the real SQLDelight database, never this HTTP surface.

## Real, confirmed dead Clinic residue — contained, not wired

16 unused `api/sub/clinic/*` Retrofit declarations in `AuraApi.kt`
(dashboard/patients/appointments/prescriptions/invoices/doctors/
services/payments/lab-expenses) plus matching `Models.kt` classes and
a `User.clinic_role` field — real, present, but **never called from any
screen**, confirmed by inspection of every `ui/screens/*` file. A
handful of doc-comments cite the sibling Clinic app for design
rationale (shared-panel patterns). No Clinic-named composable, screen,
or nav route exists. This matches the checkpoint's own framing
("dead Clinic residues already identified") — nothing new to flag,
and nothing here needs porting or cleanup as part of M6 since it isn't
wired to anything real.

## Real, functional bilingual/RTL support already exists

Not English-only. `AppLocale` (object, observable `mutableStateOf`)
persists an `EN`/`AR` choice; `AppRoot.kt` flips
`LocalLayoutDirection` app-wide when Arabic is active, no Activity
restart needed. Translation is a hardcoded `Map<String,String>` keyed
by the literal English string (~250+ entries), with graceful fallback
to the English key when untranslated. `Num.kt` parses Arabic-Indic and
Extended Arabic-Indic digits on input but always formats output in
`Locale.US` (Western digits) for deterministic round-tripping. This is
real, useful behavioral precedent for M6.11/M6.12 — the new shared
localization authority does not need to invent RTL/bidi behavior from
nothing, though the mechanism itself (hardcoded map vs. real string
resources/`kotlinx.serialization`-driven catalog) is a real, deliberate
redesign opportunity, not something to port as-is.

## Real design tokens — one fixed dark "Aurora" identity, no light mode

`AuraTheme` hardcodes `darkTheme = true` ("Aurora is a fixed dark
identity") and `dynamicColor = false` — a full `LightColors` M3 scheme
is defined in `Color.kt` but never wired up; **no light mode ships
today**. This is a real, direct conflict with M6.4's own explicit
requirement ("light theme; dark theme... respect system light/dark
mode while allowing the app's saved preference") — the new shared
design system must NOT simply port Aurora as the only theme; a real
light theme must be designed and wired, informed by (not copied from)
the existing unused `LightColors` definition. The "Aurora" brand
identity (teal/cyan/violet glow accents, frosted `GlowCard` panels,
`Success`/`Warning`/`Danger`/`Info` status colors) is real, shippable
brand equity worth carrying into the new design system as a dark-theme
option, not the assumed-adequate whole.

## Real barcode mechanism — two real input paths, one lookup function

HID/keyboard-wedge input via a real, pure, platform-independent
timing-gap algorithm (`HidScanDetector`, no dependency on any specific
hardware SDK) plus a real CameraX+ML-Kit (offline, bundled model)
camera path — both converge on one real `findProductByCode` lookup. A
5-adapter `ScannerAdapter` interface family (serial/Bluetooth-SPP/BLE/
Intent-broadcast/vendor-SDK) is declared but explicitly documented as
unimplemented, a forward-looking placeholder only. Real, useful
precedent for a later barcode milestone (not M6, which does not touch
POS/scanning) — noted here for completeness per the audit's own scope,
not acted on.

## Real per-domain shared-module readiness (cross-referenced against `mobile/aura-retail-unified/shared`)

| Legacy Android domain | Real shared-module status |
|---|---|
| Products/Categories/Branches/Inventory | Real repositories + SQLDelight schema exist (M5.1-M5.5) |
| Dashboard/Reports (summary, trend, top products, daily cash) | Real repositories + SQLDelight schema exist (M5.6/M5.7) — `ReportingRepository`/`DashboardRepository` |
| Sales (POS checkout, transaction history) | Real SQLDelight schema (`Sales.sq`) exists; only an in-memory `InMemorySaleRepository` is wired (M3) — **no real SQLDelight-backed Sale repository/use case exists yet** |
| Customers, AR/receivables | Real SQLDelight schema (`Parties.sq`, `Ledger.sq`) exists; **no repository/use-case layer exists** |
| Suppliers, AP/payables, Purchase Orders | Real SQLDelight schema (`Parties.sq`, `Purchasing.sq`, `Payments.sq`, `Ledger.sq`) exists; **no repository/use-case layer exists** |
| Returns | Real SQLDelight schema (`Returns.sq`) exists; **no repository/use-case layer exists** |
| Aging | No dedicated schema/repository — would derive from Ledger once AR/AP repositories exist |
| Backup/Restore | Interface marker only (`BackupRepository`, M16-scoped) — real proof the new `import_*` tables backup-compatibly exists (`import-backup-restore-report.md`), but no backup pipeline itself |
| Licensing/device identity | Interface markers only (`LicensingRepository`, `SessionRepository`, `UserRepository`, `BusinessRepository`, M7-M10-scoped) — the real Kotlin Ed25519/Keystore/Canonical-JSON machinery in `android/aura-retail/app/.../licensing/` is legacy-app-specific, not yet ported to `commonMain` |
| Settings (credit policy, currency, payment methods) | Real `SettingsRepository` exists in shared, scope/shape not yet cross-checked against the legacy screen's own credit-settings/payment-methods fields |

This table is the real basis for `mobile-screen-route-matrix.md`'s own
"shared use case"/"shared repository"/"later milestone dependency"
columns — every row there cites this real, current state, not an
assumption.

## Real conclusion for M6 scope

Only 4 real vertical slices have a fully-built shared backend to
integrate against today: **Category, Branch, Reporting, Import
Center** — exactly the 4 vertical slices M6.16-M6.19 already require.
Products has a real backend too, but is not one of M6's own required
slices (left to a later milestone per the checkpoint's own scope, since
POS/full catalog UI is explicitly deferred). Every other legacy screen
(Sales/POS, Customers, Suppliers, Returns, Purchase Orders, AR/AP,
Aging, Backup, Licensing) has either no repository at all or only an
in-memory stand-in — confirming the checkpoint's own explicit "M6 does
not declare every business workflow complete" framing is not a
formality here, it is the real, current state of the shared module.
