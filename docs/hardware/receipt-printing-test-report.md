# Receipt Printing Test Report (Wave 1B, Part P)

## Real defect found and fixed: printed values were not 100% authoritative
Before this wave, Windows' existing on-screen receipt popup (`_showReceipt`) mixed the server's authoritative `saleData` response with `payload` (what the client submitted) and `this._cart` (the client's local working state) — using `payload.total` for the displayed Total and `this._cart` for line items, instead of `saleData`'s own fields. This is the same *class* of bug as MOB-001 (a client-side value silently standing in for the server's authoritative one) — not exploitable in the current single-tender-type flow, but a real violation of "printed/displayed values must come only from persisted authoritative backend records," and a real risk if a future change made the client and server compute even slightly different numbers.

**Fixed**: `_showReceipt` and the new `_printReceipt` both read exclusively from `saleData` (the real `POST /api/sub/retail/sales` response) — `saleData.total`, `saleData.subtotal`, `saleData.discount_amount`, `saleData.tax_amount`, `saleData.amount_paid`, `saleData.change`, `saleData.lines` (server-computed line items, not the client's cart). Verified by reading the server's actual response contract (subtotal/discount_amount/tax_amount/total/amount_paid/change/lines all present, confirmed via a real API call earlier this wave) before writing the fix, not assumed.

## A real security issue introduced and immediately fixed
The first draft of the "Print" button embedded `JSON.stringify(saleData)` directly into an inline `onclick` HTML attribute. This is a real injection risk: a product name containing a quote or HTML-special character (admin-entered data, not sanitized on input) could break out of the attribute context. Caught by an automated security-review hook immediately after the edit, and fixed before moving on: the sale data is now stored on `this._lastSaleData` and the button references it by name only — no data is ever interpolated into attribute text.

## Field-by-field correctness check
Confirmed every printed field maps to a real, named field on the authoritative response (not a guess at what the field might be called):
`sale_number`, `created_at`, `lines[].name`/`lines[].quantity`/`lines[].line_total`, `subtotal`, `discount_amount`, `tax_amount`, `total`, `amount_paid`, `change`.

## Arabic / mixed-language handling
Not stress-tested this wave — the receipt template is plain HTML/CSS text rendering (no canvas/bitmap rendering, so any font the OS has installed handles Arabic script natively), but a printer's actual Arabic-rendering behavior depends on the physical printer/driver, which was not available to test. Documented as an open item, not claimed as verified.

## What was and wasn't verified live
- **Syntax-checked**: `node --check` passed on the modified frontend file.
- **Served correctly**: confirmed via a real running Windows exe instance that the updated JavaScript (including `_printReceipt`, `_testPrint`, `_lastSaleData`) is what gets served, not a stale cached copy.
- **Not click-through tested**: an interactive session to actually click "Test Print" and observe a real print dialog was not available at the point this was built. This is stated plainly rather than claimed as done.
- **Android**: compiles clean, 36/36 Kotlin unit tests pass (unaffected by this change — no new automated test was added specifically for the share-receipt text formatting, since it's a simple string builder with no branching logic worth a dedicated test). **Device-verified**: the signed release APK was reinstalled on the physical Infinix X6528, a real sale was rung up, and the user confirmed "Share Receipt" opens the Android share sheet correctly with no crash.

## Result
PASS on correctness (authoritative-data-sourcing verified and a real bug fixed), PASS on the security finding (caught and fixed before shipping), PASS on Android device verification (real device, real sale, real share-sheet trigger). Windows' print path remains code-reviewed/syntax-checked/served-correctly but not click-through tested against a real print dialog this wave — disclosed honestly, not claimed as done.
