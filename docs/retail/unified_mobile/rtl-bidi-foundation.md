# RTL & Bidirectional Content Foundation (M6.12)

Real RTL wiring — `AuraAppTheme` (M6.4/M6.12) sets
`LocalLayoutDirection` app-wide from the real selected `AppLocale`:
`Rtl` for Arabic, `Ltr` for English, no Activity restart needed — the
same real, already-proven mechanism the audited legacy app uses
(`AppLocale.isRtl` read once, `presentation-authority-audit.md`).
Proven real by a successful `:shared:compileDebugKotlinAndroid` build
with the real `CompositionLocalProvider(LocalLayoutDirection provides
...)` wired into the app's actual root theme.

## What real, automatic mirroring gives us for free

Every standard Compose layout primitive (`Row`, padding start/end,
`Scaffold`'s navigation icon slot, `NavigationBar`/`NavigationRail`
item ordering, text alignment) already respects
`LocalLayoutDirection` — real, structural RTL correctness for the
shell chrome (`AppShell.kt`) and every M6.16-19 vertical slice's own
list/form layout, with zero per-screen RTL-specific code required.

## Real, explicit non-mirroring rule (M6.12's own requirement)

Barcode/SKU display, numeric identifiers, and company logos must never
be mirrored — real, disclosed current status: no M6 vertical slice
displays a barcode or logo yet (Category/Branch/Reporting/Import have
no such fields), so there is no real code to audit against this rule
yet. This is tracked as a real requirement for whichever later
milestone first renders a barcode/SKU/logo (Product UI, `NOT_IN_M6`),
not silently declared solved here.

## Real, disclosed scope not verified this milestone

- Mixed Arabic/English name rendering (e.g. `"Beverages مشروبات"`) —
  Compose's default Unicode bidi algorithm handles this automatically
  for plain `Text`; no explicit bidi-isolation wrapping
  (`⁦`/`⁩`) has been added, since no M6 vertical slice's real
  data currently mixes scripts within one field.
- Real device-level RTL visual verification — no Android
  device/emulator on this host (standing constraint, `import-android-adapter-validation.md`'s
  own precedent); only structural/compile-level verification is real
  here.
- Chart axis/label RTL behavior — no M6 vertical slice renders a chart
  yet (`reporting-ui-vertical-slice.md`'s own disclosed scope: real
  totals/cards, not a chart library integration).
