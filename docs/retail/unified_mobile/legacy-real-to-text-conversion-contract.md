# Aura Retail Unified Mobile — Legacy REAL-to-TEXT Conversion Contract (M5.0.C)

## What this contract does NOT claim

Migration cannot recover decimal intent already lost inside a legacy SQLite `REAL` (IEEE-754 double) value. If the original Python write already introduced binary-representation drift (it mostly did not, since `pricing.py` always writes 2dp-quantized values — `python-financial-authority-map.md`), that drift is already baked into the stored bytes before this migration ever reads them. **The correct claim is that future persistence becomes exact after a documented, deterministic migration of the legacy observed value — not that original precision is restored.**

## The deterministic rule

`value: Double -> value.toString()`, written verbatim as the new `TEXT` column's content. No rounding, no re-quantization, no reformatting beyond what `Double.toString()` itself performs (Java/Kotlin's documented shortest-round-trip-string algorithm). This is the exact function `CatalogImporter.kt` calls; the rule is proven against the exact real pipeline, not a hypothetical one.

## Real, executed evidence (`LegacyRealToTextConversionTest.kt`, `./gradlew :shared:testDebugUnitTest`, 78/78 passing)

Each value below was written into a real in-memory SQLite `REAL` column via `JdbcSqliteDriver`, read back through real JDBC `getDouble()`, and converted via `Double.toString()` — not reasoned about abstractly.

| Input (legacy-representative) | Real observed TEXT output | Money.parse() succeeds |
|---|---|---|
| `19.99` | `"19.99"` | yes |
| `59.97` | `"59.97"` | yes |
| `0.1` | `"0.1"` | yes |
| `0.2` | `"0.2"` | yes |
| `0.3` | `"0.3"` | yes |
| `100.0` | `"100.0"` | yes |
| `1000000.0` | `"1000000.0"` | yes |
| `0.5` | `"0.5"` | yes |
| `1234567.89` (large value) | `"1234567.89"` | yes, no scientific notation |
| `19.123456789` (high-scale, more digits than a legacy 2dp price would ever carry) | round-trips to the exact same double bit pattern the legacy DB already stored | yes, honestly not claimed to recover intent beyond what was stored |
| `-0.0` (negative zero) | `"0.0"` — **not** `"-0.0"` | yes, and `Money.parse("0.0") == Money.ZERO` |

## The negative-zero finding (real, not assumed)

The original test draft assumed `Double.toString(-0.0) == "-0.0"` would survive the pipeline — true for `Double.toString()` in isolation, but the real, executed round trip through SQLite `REAL` storage does **not** preserve the sign bit: writing `-0.0` into a `REAL` column and reading it back via JDBC `getDouble()` returns plain positive `0.0`. The sign is lost specifically inside the SQLite storage/retrieval step, not in Kotlin's own formatting. This was discovered by running the test, not by inspecting SQLite's source — the test build initially failed (`expected:<[-]0.0> but was:<[]0.0>`), and the assertion was corrected to match the real observed behavior rather than the assumption.

Practical consequence: no legacy `-0.00` price/quantity value can ever reach the importer as a negative-zero string in the first place — SQLite itself normalizes it before the importer ever sees it. `Money`'s own sign-agnostic equality (`-0.00 == 0.00` via `BigDecimal.compareTo`) remains in place as defense-in-depth, but this specific artifact turns out not to occur via this storage path.

## Disposition

No further conversion logic is required. The rule is proven deterministic, non-worsening, and exact for every representative value class the legacy schema can actually produce (2dp currency values, large values, zero, negative zero). `CatalogImporter.kt` requires no changes as a result of this investigation — its existing use of `.toString()` is already the correct, now-proven rule.
