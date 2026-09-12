# Receipt Printer Compatibility Matrix

| Path | Platform | Status |
|---|---|---|
| Windows OS print spooler (any installed printer: thermal-via-driver, regular, or PDF) | Retail Windows | **PROTOCOL SUPPORTED** — real implementation this wave, syntax-validated, confirmed served live by a running instance; not click-through tested against a real print dialog this wave (no interactive session available at that point) |
| Share sheet (text receipt, can target Android's own Print share target, messaging, email) | Retail Android | **PROTOCOL SUPPORTED** — real implementation this wave, compiles and unit-test suite passes; not click-tested on-device this wave |
| Direct USB ESC/POS thermal printing | Windows | **NOT IMPLEMENTED** — the OS-print-spooler path above covers this use case without needing raw ESC/POS |
| Network (LAN) ESC/POS thermal printing, raw TCP port 9100 | Retail Windows **and** Android | **PROTOCOL SUPPORTED (2026-09-11), HARDWARE UNVALIDATED** — `escpos_transport.send_to_network` (backend, any platform) and `printer/NetworkPrinterAdapter.kt` (Android) both send the bytes produced by the same tested `escpos_receipt` renderer. Proven end to end against a real socket listener asserting the exact bytes, including a 200 KB payload and the connect-timeout path; **no physical printer has ever received these bytes.** See the commercial wording note below before saying anything about this to a customer. |
| Cash-drawer kick (`ESC p`) on a completed cash sale | Retail Windows **and** Android | **SUPPORTED (2026-09-11), opt-in, HARDWARE UNVALIDATED** — previously reachable only from a Settings test button and never from the sale path. The server decides whether a sale warrants a kick (it reads `payment_method` itself), and every non-kick outcome is a success with a reason, never an error, because the sale is already committed. Drawer pulse timing against a real drawer is unverified. |
| Arabic text on an ESC/POS receipt | Retail, both platforms | **NOT SUPPORTED** — ESC/POS text mode is ASCII-only; non-ASCII degrades to `?`. This is a limitation of the byte path, not a bug: correct Arabic needs the printer's graphics/raster path or a vendor SDK's own text API, neither of which is built. The Android settings screen states this to the shopkeeper directly. The HTML/OS-spooler path above is unaffected and prints Arabic correctly. |
| Direct Bluetooth ESC/POS thermal printing | Android | **NOT IMPLEMENTED** — no hardware to verify against; contract prepared (see architecture doc), not built |
| Windows OS print spooler (invoice printing) | Clinic Windows | **PROTOCOL SUPPORTED** — `subsystem-clinic.js::_printInvoice()` opens a real print window against the OS spooler, same mechanism as Retail's receipt path. **Correction (docs/einvoicing/phase1/):** this row previously read "NOT APPLICABLE — no printing feature was ever requested or scoped for Clinic," which was inaccurate — the feature exists and predates this correction. |
| JoFotara QR code on printed receipt/invoice (docs/einvoicing/phase1/) | Retail + Clinic, both platforms | **PROTOCOL SUPPORTED**, opt-in — an `<img>` tag with a `data:` URI in the same HTML print path above; no printer-protocol work needed (see `../hardware/receipt-printer-architecture.md`'s "QR codes on receipts" section). Gated on the feature being enabled and the invoice having actually cleared; otherwise the receipt is unchanged. |

## Commercial wording guidance

Approved: *"Print receipts to any Windows-installed printer. On Android, share
receipts to any app that supports printing or sending documents."*

Not approved: any claim of direct Bluetooth/USB thermal printer support, on
either platform, until real hardware validates it.

**Not approved, 2026-09-11 — network (LAN) printing and the cash-drawer kick.**
Both are implemented and both are proven at the socket level, and neither has
ever driven a physical printer or a physical drawer. This document's standard
has always been that a SALES CLAIM waits for real hardware, and shipping code
does not change that: a shop told "it prints to your network printer" and handed
a till that produces nothing has been mis-sold, regardless of how good the tests
are. One afternoon with a cheap LAN thermal printer converts all three rows
above and unlocks the claim.

**Never approved on the ESC/POS path: any claim about Arabic receipts.** That
path prints ASCII only. Saying "Arabic receipts" while meaning the HTML/spooler
path and shipping the ESC/POS path to a Jordanian shop is the single most
damaging thing that could be said about this feature.
