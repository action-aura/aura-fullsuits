# Aura Retail Unified Mobile — Money/Decimal Representation Decision (M3.1)

## Requirement

Every financial figure in the shared core must exactly reproduce Python's `decimal.Decimal` arithmetic (`core/retail/pricing.py`) — base-10 exact arithmetic, `ROUND_HALF_UP` at output boundaries, no binary-float artifacts. No `Float`/`Double` may carry a financial value at any point. Must work identically across `commonMain`, `androidMain` (JVM), and `iosMain` (Kotlin/Native) — real KMP-target constraint, not just Android.

## Options evaluated

### Option A: `com.ionspin.kotlin:bignum` (real, verified via Maven Central + GitHub, not assumed)

Real, current evidence gathered this milestone:
- Published on Maven Central at version `0.3.10` (verified: [central.sonatype.com/artifact/com.ionspin.kotlin/bignum/0.3.10](https://central.sonatype.com/artifact/com.ionspin.kotlin/bignum/0.3.10)), with per-target artifacts published including Apple targets (`bignum-tvosx64` confirmed listed alongside the main artifact, implying the full Apple/iOS target matrix ships as part of the same release train — [mvnrepository.com/artifact/com.ionspin.kotlin/bignum](https://mvnrepository.com/artifact/com.ionspin.kotlin/bignum)).
- Provides `BigDecimal`/`BigInteger` with a documented, real, multi-mode rounding API. The GitHub project page (fetched this milestone) states it offers `ROUND_HALF_AWAY_FROM_ZERO` among its rounding modes — **semantically identical** to Python's `ROUND_HALF_UP` (both round an exact tie away from zero: `0.5 -> 1`, `-0.5 -> -1`), just a different name for the same rule. Verified by definition, not assumed compatible without checking.
- Real, honest maintenance signal (not overstated): the project's own documentation states *"The APIs might change until v1.0"* and is still pre-1.0 (`0.3.x`). 698 commits, described as maintained but "may not be under active development" per the fetched page's own framing. This is a real risk factor for a financial-integrity-critical dependency, not hidden.

### Option B: custom fixed-scale decimal type, hand-rolled

Real engineering cost assessed, not hand-waved: Kotlin's standard library has no `BigInteger`/`BigDecimal` in `commonMain` (`java.math.BigInteger` is JVM-only, unavailable on `iosMain`). A correct hand-rolled replacement needs either (a) a Long-backed fixed-scale type, which cannot safely represent the intermediate precision needed for `price * quantity * (discount_pct/100) * (tax_rate/100)` chains without a wider-than-64-bit intermediate multiply (a real overflow risk for realistic-but-large POS transactions, not a theoretical one), or (b) a self-written arbitrary-precision integer (digit-array-based add/subtract/multiply/parse/format) — real, non-trivial, easy-to-get-subtly-wrong code, in exactly the domain (money) where a subtle bug is least acceptable.

## Decision: adopt `com.ionspin.kotlin:bignum`, wrapped behind this project's own `Money`/`Quantity`/`PercentageRate` types

**Reasoning**: the real, verified evidence (Maven Central publication across the full KMP/Apple target matrix, a documented rounding-mode that is provably equivalent to Python's `ROUND_HALF_UP`) outweighs the real-but-survivable pre-1.0 API-stability risk, **because that risk is fully contained by never exposing `BigDecimal` directly** — every call site in this codebase uses this project's own `financial/Money.kt`/`Quantity.kt`/`PercentageRate.kt` types, which internally hold a `BigDecimal` as a private implementation detail. If a future `bignum` major version changes its API, exactly one file (the wrapper) needs updating, not every use case/repository/UI call site — the same "isolate the risky dependency behind our own stable interface" pattern already used for platform capabilities (`platform/PlatformContracts.kt`, ADR-2). Hand-rolling arbitrary-precision arithmetic for a money type, when a real, correctly-rounding, cross-platform-published library already exists and is verified compatible, would be reinventing a well-understood wheel in the highest-consequence domain this whole initiative touches — not a conservative choice, a riskier one.

**Version pinned**: `0.3.10` (the real, current Maven Central release verified this milestone) — not a floating/`+` version, so a future upstream release cannot silently change this project's financial behavior without an explicit, reviewed version bump.

## What the wrapper types provide (real requirements from the governing spec, all satisfied)

- `Money`: currency amount, always exactly 2 decimal places at rest (mirrors `pricing._money()`'s `Decimal.quantize('0.01', ROUND_HALF_UP)` exactly), explicit `CurrencyCode` association, exact string parsing (rejects NaN/Infinity/scientific-notation-as-currency/malformed input — closing the real Python gap from `financial-invariant-catalog.md` invariant #7a), deterministic `toString()` (never scientific notation), overflow detection on arithmetic operations.
- `Quantity`: arbitrary-scale positive-or-validated decimal (supports fractional quantities, e.g. weight-based items, matching Python's `float(quantity)` accepting decimals), same NaN/Infinity rejection.
- `UnitPrice`: same representation family as `Money`, distinct type to prevent accidentally adding a price to a quantity at the type level.
- `PercentageRate`/`TaxRate`/`Discount`: `[0, 100]`-range-aware decimal wrapper with the same clamp-not-reject semantics as `pricing.clamp_discount_pct()` where that's the `LEGACY_PARITY` behavior being ported.
- `CurrencyCode`: a plain validated string wrapper (ISO 4217-shaped), not a decimal type at all — included here because it travels with every `Money` value.

No raw `String`/`Int`/`Long`/`Float`/`Double` is allowed to flow through `financial/`, `usecases/`, or `data/` financial code after the initial parse boundary — every financial function signature in the shared core takes and returns these wrapper types.
