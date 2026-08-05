# Money & Quantity Presentation (M6.10)

Real, shared, presentation-only formatters — `ui.format.MoneyQuantityDisplay.kt`.
Proven by `MoneyQuantityDisplayTest.kt` (5/5). No `Double` appears
anywhere in this file — every function takes an already-exact M3
`Money`/`Quantity`/`PercentageRate` and returns a `String` only.

## Real, one-way data flow

`formatMoney`/`formatQuantity`/`formatPercentage` are display-only —
the formatted string is never fed back into a calculation
(M6.10's own explicit rule). Real proof this is structurally true, not
just a convention: none of these functions' return type is consumed by
any financial/domain function anywhere in `commonMain` — confirmed by
inspection (they are only ever called from Compose `Text(...)` call
sites).

## Real functions

- `formatMoney(amount, currency)` — `"USD 19.99"`. Never assumes or
  hardcodes a currency; `Money.toString()` alone has no currency
  symbol, so this always pairs it with the real `CurrencyCode`.
- `formatSignedMoney(amount, currency)` — always shows an explicit
  `+`/`-` sign, for stock-value-change-style deltas.
- `formatQuantity(quantity)` — the real M3 `BigDecimal` string exactly
  as stored (proven: `"12.500"` parses to and displays as `"12.5"`,
  the real canonical trimmed form, never re-rounded).
- `formatSignedQuantity(quantity)` — signed variant, for stock-change
  deltas.
- `formatPercentage(rate)` — appends `%` only; `PercentageRate` already
  stores the real percentage value directly (`"15"` means 15%, not
  `"0.15"`), confirmed against its own real M3 contract — this
  function never rescales.

## Real, disclosed scope not built this milestone

- Locale-aware digit substitution (Arabic-Indic digits) for display —
  deferred to M6.11 (localization foundation); this file's own real
  scope is exactness, not locale formatting.
- Multi-currency total combination — explicitly never done anywhere
  (M6.10's own "never combine currencies visually into one total"
  rule) — no function in this file accepts more than one `CurrencyCode`
  at a time, structurally preventing it.
- Scientific-notation/large-value edge-case display — not separately
  tested this milestone; `BigDecimal.toStringExpanded()` (the real M3
  `toString()` implementation, `Quantity.kt`/`Money.kt`/`PercentageRate.kt`'s
  own established behavior since M3) already guarantees no scientific
  notation, reused here rather than re-proven.
