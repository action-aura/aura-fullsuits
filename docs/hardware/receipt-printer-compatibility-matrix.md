# Receipt Printer Compatibility Matrix

| Path | Platform | Status |
|---|---|---|
| Windows OS print spooler (any installed printer: thermal-via-driver, regular, or PDF) | Retail Windows | **PROTOCOL SUPPORTED** — real implementation this wave, syntax-validated, confirmed served live by a running instance; not click-through tested against a real print dialog this wave (no interactive session available at that point) |
| Share sheet (text receipt, can target Android's own Print share target, messaging, email) | Retail Android | **PROTOCOL SUPPORTED** — real implementation this wave, compiles and unit-test suite passes; not click-tested on-device this wave |
| Direct USB ESC/POS thermal printing | Windows | **NOT IMPLEMENTED** — the OS-print-spooler path above covers this use case without needing raw ESC/POS |
| Direct Bluetooth ESC/POS thermal printing | Android | **NOT IMPLEMENTED** — no hardware to verify against; contract prepared (see architecture doc), not built |
| Clinic (either platform) | Clinic | **NOT APPLICABLE** — Clinic has no point-of-sale/receipt concept; invoices are viewed on-screen only, no printing feature was ever requested or scoped for Clinic |

## Commercial wording guidance
Approved: *"Print receipts to any Windows-installed printer. On Android, share receipts to any app that supports printing or sending documents."*

Not approved: any claim of direct Bluetooth/USB thermal printer support, on either platform, until real hardware validates it.
