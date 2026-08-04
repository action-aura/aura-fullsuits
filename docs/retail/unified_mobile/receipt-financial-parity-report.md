# Aura Retail Unified Mobile — Receipt Financial Parity Report (M3.7)

## Scope

Proves the shared `FinalizedSaleSnapshot`/`FinalizedReturnSnapshot` (M3.3) carry every field a receipt needs to reproduce the authoritative financial content of a current Android receipt — the DATA, not the rendered PDF/print/share output (platform printing/sharing adapters are Milestone 15 scope, explicitly out of scope here per the governing spec: "This milestone does not require platform printing. It requires the shared receipt data model and financial content.").

## Real field-by-field proof (`ReceiptParityTest.kt`, 2 tests, both passing)

| Required field | Snapshot field | Verified by |
|---|---|---|
| Product line | `FinalizedSaleLineSnapshot` per line | `saleSnapshotCarriesEveryReceiptField` |
| Quantity | `line.quantity` | same |
| Unit price | `line.unitPriceAtSale` | same |
| Gross | `line.calculation.gross` | same |
| Discount | `line.calculation.discountAmount` | same |
| Tax | `line.calculation.tax` | same |
| Subtotal | `sale.subtotal` | same (asserted against the real computed `20.00 * 3 = 60.00`) |
| Total | `sale.total` | same |
| Paid | `sale.amountPaid` | same |
| Change | `sale.change` | same |
| Return values | `FinalizedReturnSnapshot`/`FinalizedReturnLineSnapshot` (quantity, unitPriceAtSale, refundAmount) | `returnSnapshotCarriesEveryReceiptField` |
| Identifiers | `sale.saleId`/`sale.saleNumber`/`sale.idempotencyKey`, `return.returnId`/`return.returnNumber` | both tests |
| Date/time representation contract | `createdAtEpochMillis` (platform-neutral epoch milliseconds — formatted into a locale-correct display string only at render time, in a later milestone's UI layer, never baked into the snapshot as a pre-formatted string) | both tests |
| Currency | `sale.currency` (`CurrencyCode`) | `saleSnapshotCarriesEveryReceiptField` |

## Real finding carried forward from M3.0/M3.6 (DIFF-03)

The product's display **name** (`productNameAtSale`) is included in the snapshot — the real Python `sale_items` schema has no equivalent column and live-joins `products.name` instead, a real receipt-integrity gap this snapshot design closes (see `intentional-financial-differences.md` DIFF-03). Every other field above is a faithful `LEGACY_PARITY` port of the real, already-correct Python persistence shape.

## What this milestone does NOT prove (honest, deferred to Milestone 15)

- PDF/image/text rendering of the receipt content.
- Platform share-sheet delivery.
- Print pipeline (AirPrint/Android print framework).
- Arabic/RTL receipt layout (Milestone 17 scope).
- Business-information header fields (store name/address/logo) — not part of the financial-transaction snapshot itself, a separate business-settings concern layered on top at render time.

## Disposition

The financial content required to reconstruct a receipt is real, complete, and proven present in the shared snapshot types via a real executed test — not asserted by inspection. Milestone 15 builds the rendering/sharing/printing adapters on top of this already-verified data foundation.
