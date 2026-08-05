# Accessibility Foundation (M6.13)

Real, structural accessibility baseline built into the shared design
system and component catalog (M6.4/M6.8), not a separate bolt-on.

## Minimum touch targets

`AuraDimensions.minTouchTarget = 48.dp` — the real WCAG/Material
minimum. Every real interactive component this milestone built
(`NavigationBarItem`/`NavigationRailItem` in `AppShell.kt`,
`AuraTextField`, buttons in `CommonComponents.kt`) uses Material3's own
components, which already enforce this minimum internally — no custom
small tap target was introduced anywhere in M6's own code.

## Content descriptions

Every real `Icon` this milestone added has an explicit
`contentDescription` (never `null` for a meaningful, interactive icon)
— confirmed by inspection of `AppShell.kt` (`contentDescription =
spec.label`) and `CommonComponents.kt`'s `AuraEmptyState`. The one
purely decorative icon (`AuraErrorState`'s icon, removed per
`common-component-catalog.md`'s own disclosed icon-availability
finding) never had one to begin with, correctly.

## Form field errors

`AuraTextField` surfaces `domainError.messageKey` via Material3's own
`supportingText`/`isError`, which TalkBack/VoiceOver announce as part
of the field's real accessible state — never a separate,
disconnected error label a screen reader user could miss.

## Financial totals

`money-quantity-presentation.md`'s own `formatMoney`/`formatQuantity`
always include the real currency code / exact value as plain text
(never an icon-only or color-only representation) — a screen reader
reads the real currency and sign, matching M6.13's own "financial
totals must be read with currency and sign" requirement structurally.

## Real, disclosed scope not verified this milestone

- No real screen-reader (TalkBack/VoiceOver) run exists — no
  device/emulator on this host, the same standing constraint as every
  other "real device execution" gap disclosed throughout this
  milestone.
- Reduced-motion awareness — not implemented; `AuraMotion`'s duration
  constants are fixed, not yet gated behind a real
  `LocalReduceMotion`-style check. Real, disclosed gap, deferred to
  whichever later milestone first ships an animation substantial
  enough to need it (none of M6's own screens have significant motion).
- Keyboard-navigation focus order — not explicitly tested; Compose's
  default focus order (declaration order) is relied on as-is; no
  custom `Modifier.focusOrder` was needed for M6's own simple
  list/form layouts.
