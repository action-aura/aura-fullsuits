# Receipt Printer Architecture (Wave 1B, Part O)

## Windows (Retail)
Uses the **standard OS print spooler** via a hidden `<iframe>` + `window.print()` — not a raw ESC/POS thermal protocol. This works with:
- A thermal receipt printer installed via its own Windows driver (the driver presents it as a normal system printer; the app never talks to it directly).
- A regular document printer.
- A PDF printer (useful for testing without any physical printer at all).

Paper width (58mm / 80mm) is a `@page { size: ... }` CSS setting, configurable in **Settings → Barcode Scanner** page (which this wave also extended into a general "device configuration" area, see Part Q) and persisted to `localStorage` the same way scanner settings already were.

This approach was chosen specifically because it requires no vendor SDK, no ESC/POS command generation, and no USB/Bluetooth device enumeration — any printer the user can already print a Word document to, works.

## Android (Retail)
No direct thermal-printer protocol implemented — no hardware was available to verify against, and per instruction, direct printing should only be implemented "when a supported existing implementation or testable printer is available." Instead: a real, working **share fallback** — `PaymentSuccess`'s "Share Receipt" button builds a plain-text receipt from the authoritative `SaleResult` and hands it to Android's standard share sheet (`Intent.ACTION_SEND`), which can target a print service (Android has a built-in "Print" share target that goes through the system print framework, itself capable of reaching networked/cloud printers), a messaging app, email, or anywhere else the user chooses. This is honestly a fallback, not "printing," and is documented as such.

## Contract for a future Bluetooth/USB ESC/POS adapter (not implemented)
`android/aura-retail/app/src/main/java/com/actionaura/retail/barcode/ScannerAdapter.kt`'s adapter-contract pattern (Part N) is the template a future `ReceiptPrinterAdapter` interface would follow: `connect`/`disconnect`/`isConnected`/`requiredPermissions`/bounded reconnect policy, plus a `print(bytes: ByteArray)` method instead of a scan-event callback. Not built this wave — no printer hardware to validate against, and building an untested ESC/POS byte-stream generator would risk shipping something that produces garbled output on real hardware with no way to catch that before a customer does.

## Data safety (ties to Part P)
Every printed/shared value comes from the server's authoritative sale response (`SaleResult` on Android, `saleData` from `POST /api/sub/retail/sales` on Windows) — see `receipt-printing-test-report.md` for the specific bug this caught and fixed.

## QR codes on receipts (docs/einvoicing/phase1/)

Jordan JoFotara e-invoicing (opt-in, default off) needs a QR code on the
printed receipt/invoice once an invoice clears with the tax authority.
Because both Windows and Clinic already print through the OS spooler as
HTML (this doc's Windows section above; Clinic's `_printInvoice()` is the
identical mechanism), a QR code required **zero printer-protocol work** —
it's just an `<img src="data:image/png;base64,...">` tag inserted into the
same HTML the browser already prints, gated behind the feature being
enabled and the specific invoice having actually cleared. See
`../einvoicing/phase1/receipt-qr-rendering-design.md` for the full design,
including how a receipt printed before clearance shows an honest "pending"
line instead.

The Android share-fallback path above is untouched by this wave — no
Android UI work was done for e-invoicing (see
`../einvoicing/phase1/phase1-residual-risk-register.md` item 2). Adding an
e-invoicing status line to the existing plain-text "Share Receipt" builder
remains a Phase 2 item alongside the rest of the Android UI gap.
