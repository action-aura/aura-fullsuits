# Aura Retail Unified Mobile — Intentional Financial Differences (M3.6)

Every place the shared Kotlin core deliberately behaves differently from the real, current Python authority, per the governing spec's `LEGACY_PARITY_RULES` / `CANONICAL_UNIFIED_RULES` split. Each entry has an issue identifier, old behavior, new behavior, business reason, and test evidence. This is the authoritative reconciliation of every divergence found across M3.0–M3.5 — nothing here is undocumented, and no historical sale total is affected (all differences are at input-validation/formatting boundaries, never in the core arithmetic formulas, which are byte-for-byte `LEGACY_PARITY`).

## DIFF-01: NaN/Infinity quantity rejection

- **Old behavior**: `create_sale()`/`create_return()` call `float(item.get('quantity'))`, which does not raise for `"nan"`, `"inf"`, `"-inf"`, `"Infinity"`. Since `nan <= 0` and `nan > 0` are both `False` in IEEE-754, a NaN quantity string silently passes the subsequent `qty <= 0` rejection check too — a real, exploitable path to a persisted sale with a NaN quantity.
- **New behavior**: `Quantity.parse()`/`Money.parse()` explicitly reject `"nan"`/`"infinity"`/`"inf"`/their sign variants (case-insensitive) before ever reaching `BigDecimal.parseString()`.
- **Business reason**: a NaN quantity can never mean anything commercially; a real data-integrity bug in the current backend, not a feature.
- **Migration impact**: none — no legitimate Android client input ever sends these strings today (the Compose UI's numeric text fields don't produce them under normal use).
- **Android/iOS impact**: identical, shared behavior on both platforms (this is exactly the kind of bug class the unified core exists to eliminate once, for both platforms, rather than fixing twice or leaving latent).
- **Test evidence**: `CalculateLineTest.quantityRejectsNaNAndInfinity_closedPythonGap`, `ParsingDifferentialTest.intentionallyDivergesFromPython_nanAndInfinityRejected`.
- **Release-note requirement**: internal engineering note only — not user-visible (no legitimate input is affected).

## DIFF-02: Idempotency conflicting-payload detection

- **Old behavior**: an idempotency-key hit on `create_sale()`/`create_return()` unconditionally returns the first call's result, with zero comparison against the retried payload. A client bug or retry-with-different-cart-contents under the same key silently returns stale data instead of erroring.
- **New behavior**: `InMemorySaleRepository`/(the real SQLite-backed implementation, Milestone 4) compares the retried payload's line items (product + quantity) against the original; a mismatch returns `DUPLICATE_OPERATION_CONFLICT` instead of the stale result.
- **Business reason**: the spec's own explicit requirement ("Same key plus conflicting payload returns a conflict") — a real gap in a genuinely load-bearing offline-first-retry scenario (network drop after commit, before response reaches the client, client retries with a since-modified cart).
- **Migration impact**: none for legitimate retries (same payload still short-circuits identically); a client that was relying on the old silent-stale-return behavior (no known caller does) would need to handle the new conflict response.
- **Test evidence**: `InMemorySaleRepositoryTest.idempotentRetryConflictingPayloadReturnsConflict` (this exact test caught a real bug in the first implementation attempt — see `milestone-3-decision.md`).

## DIFF-03: Finalized sale line snapshots the product's display name

- **Old behavior**: `sale_items` has no `product_name` column; every read (receipt, history, return) live-joins `products.name`, so a renamed or archived product silently changes how old receipts display, contradicting the spec's own "even if product name changes... do not rely on live product values" requirement.
- **New behavior**: `FinalizedSaleLineSnapshot.productNameAtSale` freezes the name at sale time.
- **Business reason**: closes a real receipt-integrity gap; every other historical field (`unitPriceAtSale`, `discountPctAtSale`, `taxRateAtSale`) was already correctly frozen in the real Python schema — this brings the product name in line with that same already-correct pattern.
- **Migration impact**: **real, one-directional** — a Python-era `sale_items` row has no stored name to migrate forward; Milestone 4's Android data-preservation import must backfill `productNameAtSale` from whatever `products.name` currently resolves to at migration time (the best available approximation for pre-existing data; new sales are correct from day one).
- **Test evidence**: `InMemorySaleRepositoryTest.fullSalePersistsAndDecrementsStock` asserts the name is present; `fullReturnRestoresStockAndRefundsFromSnapshot` proves a later live-price change doesn't leak into a return computed from the frozen snapshot (same protection now extended to the name).

## DIFF-04: Exotic Python `float()` string permissiveness not replicated

Real Python evidence gathered this milestone (`float()` invoked directly, not inferred):

| Input | Python `float()` | Kotlin `Quantity.parse`/`Money.parse` |
|---|---|---|
| `"5_000"` (underscore digit grouping) | `5000.0` (accepted) | Rejected |
| `"٥5"` (Arabic-Indic digit + ASCII digit) | `55.0` (accepted — CPython's float parser normalizes some Unicode decimal digits) | Rejected |
| `"+5"` (leading plus) | `5.0` (accepted) | Rejected (bignum's `parseString` requires no leading `+`) |
| `".5"` / `"5."` (bare leading/trailing dot) | `0.5` / `5.0` (accepted) | Behavior not independently verified this milestone — deferred to Milestone 5 if a real UI input pattern needs it |

- **Business reason**: none of these are deliberate product requirements — they are accidental generality of Python's general-purpose numeric-literal grammar, never something a POS quantity/price field was designed to accept. A stricter parser for a financial input boundary is the more defensible canonical choice, not a regression.
- **Migration impact**: none — no legitimate stored data or Android UI input path produces underscore-grouped or Arabic-Indic-digit quantity/price strings today (the existing Compose numeric fields already only emit plain ASCII decimal strings).
- **Test evidence**: `ParsingDifferentialTest` (real, executed comparison for the cases that matter for input validation: whitespace-trimming and exponential notation are matched, per DIFF-05; comma-separator and garbage rejection are matched; NaN/Infinity rejection is DIFF-01).

## DIFF-05: What IS matched from Python's `float()` (not a difference, listed for completeness)

- Leading/trailing whitespace trimmed (`"  5.5  "` → `5.5`) — matched.
- Exponential notation (`"1e3"` → `1000`) — matched (standard decimal-literal syntax, not an exotic permissiveness quirk).
- Comma thousands-separators, hex literals, and non-numeric garbage all rejected in both systems — matched.

## Disposition

Five real differences, all `CANONICAL_UNIFIED`, all at the input-validation/snapshot boundary, none in the core arithmetic (`calculate_line`/`calculate_invoice`, proven byte-identical to Python by `PythonDifferentialTest`'s 29 real golden-value comparisons). No undocumented "cleanup" changed any historical calculation — every difference here is a closed gap, not a silent behavior drift.
