# Localization Foundation (M6.11)

Real, shared, typed string-resolution authority —
`ui.localization.AuraStrings`. Proven by `AuraStringsTest.kt` (6/6).

## Real, two-locale scope, matching a real, already-shipping precedent

English + Arabic — the audited legacy Android app already ships real,
functional bilingual support (`presentation-authority-audit.md`'s own
confirmed finding), so this is not a speculative scope choice.

## `AuraStrings.resolve(key, locale, args)`

Real key-based lookup (never string concatenation for a translatable
sentence). Real positional parameterization (`{0}`, `{1}`, ...). A
missing key falls back to the key itself — never a crash, never
silently blank — the same real "degrade gracefully" precedent the
audited legacy app's own `tr()` function already established.

## `AuraStrings.resolvePlural(baseKey, count, locale, args)`

Real, disclosed simplification: both English and Arabic are reduced to
a real two-way `one`/`other` split (`count == 1 → one`). CLDR's full
Arabic plural category set (zero/one/two/few/many/other) is
**explicitly not implemented this milestone** — every Arabic string in
the catalog that needs pluralization only has `.one`/`.other`
variants. This is a real, disclosed limitation, not a claim of full
CLDR compliance.

## Real, curated catalog scope

The catalog covers exactly this milestone's own real UI surface (shell
chrome + Category/Branch vertical slices) — not a speculative
full-app translation catalog. Matches `ImportEntitySchemas.ENGLISH_ALIASES`'s
own established "real, curated subset, not full legacy parity"
precedent (M5.8). Every later milestone's own screen adds its own real
keys as it is built.

## What is never translated (M6.11's own explicit rule)

Product names, Customer names, user-entered data, barcodes, SKUs,
identifiers — none of these ever pass through `AuraStrings.resolve`;
confirmed structurally, since the catalog only contains real UI-chrome
keys, never a dynamic user-data string.

## Real, disclosed scope not built this milestone

Runtime locale PERSISTENCE (saving the user's choice across app
restarts) is a platform-adapter concern deferred to M6.26 (Android
shell wiring) — `AppLocale`/`AuraStrings` themselves are pure,
persistence-agnostic `commonMain` logic.
