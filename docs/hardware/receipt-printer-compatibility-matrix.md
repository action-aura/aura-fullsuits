# Receipt Printer Compatibility Matrix

| Path | Platform | Status |
|---|---|---|
| Windows OS print spooler (any installed printer: thermal-via-driver, regular, or PDF) | Retail Windows | **PROTOCOL SUPPORTED** — real implementation this wave, syntax-validated, confirmed served live by a running instance; not click-through tested against a real print dialog this wave (no interactive session available at that point) |
| Share sheet (text receipt, can target Android's own Print share target, messaging, email) | Retail Android | **PROTOCOL SUPPORTED** — real implementation this wave, compiles and unit-test suite passes; not click-tested on-device this wave |
| Direct USB ESC/POS thermal printing | Windows | **NOT IMPLEMENTED** — the OS-print-spooler path above covers this use case without needing raw ESC/POS |
| Direct Bluetooth ESC/POS thermal printing | Android | **NOT IMPLEMENTED** — no hardware to verify against; contract prepared (see architecture doc), not built |
| Windows OS print spooler (invoice printing) | Clinic Windows | **PROTOCOL SUPPORTED** — `subsystem-clinic.js::_printInvoice()` opens a real print window against the OS spooler, same mechanism as Retail's receipt path. **Correction (docs/einvoicing/phase1/):** this row previously read "NOT APPLICABLE — no printing feature was ever requested or scoped for Clinic," which was inaccurate — the feature exists and predates this correction. |
| JoFotara QR code on printed receipt/invoice (docs/einvoicing/phase1/) | Retail + Clinic, both platforms | **PROTOCOL SUPPORTED**, opt-in — an `<img>` tag with a `data:` URI in the same HTML print path above; no printer-protocol work needed (see `../hardware/receipt-printer-architecture.md`'s "QR codes on receipts" section). Gated on the feature being enabled and the invoice having actually cleared; otherwise the receipt is unchanged. |

## Commercial wording guidance
Approved: *"Print receipts to any Windows-installed printer. On Android, share receipts to any app that supports printing or sending documents."*

Not approved: any claim of direct Bluetooth/USB thermal printer support, on either platform, until real hardware validates it.
