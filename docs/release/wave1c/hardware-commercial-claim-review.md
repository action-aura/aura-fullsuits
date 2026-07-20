# Wave 1C -- Hardware Commercial Claim Review (Part H)

Re-verified this wave via independent code/doc spot-check, not a re-read of Wave 1B's matrices alone. Baseline documents (`docs/hardware/barcode-scanner-compatibility-matrix.md`, `docs/hardware/receipt-printer-compatibility-matrix.md`) remain accurate -- no drift found.

## Narrowest accurate claim, per channel

| Channel | Status | Approved claim wording |
|---|---|---|
| **Windows HID scanner** | PROTOCOL SUPPORTED (not device-verified) | Implementation is real, pre-existing, code-reviewed. No physical USB/Bluetooth HID scanner was tested this wave or Wave 1B. Treat as "protocol supported," not "physically verified." |
| **Android HID scanner** | PROTOCOL SUPPORTED, unit-tested (not device-verified) | Keystroke-timing detection logic is unit-tested (7/7, `HidScanDetectorTest.kt`) against synthetic timing. Never run against a real physical HID scanner. |
| **Android camera scanning (CameraX + ML Kit)** | **PHYSICALLY VERIFIED** | The only scanning channel with real hardware verification -- physically tested on a real device (Infinix X6528) in Wave 1A, re-confirmed non-interfering with the new HID key-event listener this wave. |
| **Windows OS-spooler printing** | PROTOCOL SUPPORTED (not click-tested) | Print path is implemented and confirmed to serve correctly from a running instance. Never click-tested through an actual Windows print dialog against a real printer. |
| **Android share-sheet "printing"** | PROTOCOL SUPPORTED (not device-verified) | Not printing in the direct sense -- hands a text receipt to whatever app the user selects, which may or may not print. Compiles, unit-tests pass. Not exercised on a real device this wave. |
| **Direct ESC/POS (Windows or Bluetooth/Android)** | **NOT IMPLEMENTED** | Not built. Do not claim. |
| **Any specific printer brand/model** | **NOT VERIFIED** | No printer hardware has been tested against either product, at any wave. |
| **USB-serial or BLE barcode scanners** | **ADAPTER PREPARED, NOT IMPLEMENTED** | Contract exists (`ScannerAdapter.kt`); no implementation, no hardware to validate against. |
| **Vendor SDK scanners (Zebra DataWedge, etc.)** | **REQUIRES VENDOR SDK, NOT IMPLEMENTED** | Out of scope entirely to date. |
| **Clinic (either platform)** | **NOT APPLICABLE** | No POS/receipt/scanning concept exists in Clinic by design -- correctly absent, not a gap. |

## Approved commercial wording (unchanged from Wave 1B, re-confirmed accurate)
- Scanners: *"Supports standard USB and Bluetooth keyboard-mode barcode scanners, plus camera scanning on Android. Specialized devices may require an adapter."*
- Printing: *"Print receipts to any Windows-installed printer. On Android, share receipts to any app that supports printing or sending documents."*

## Not approved, anywhere, for either product
- "Supports every barcode scanner" or any unqualified universal-compatibility claim.
- Any claim of direct Bluetooth/USB thermal (ESC/POS) printer support.
- Any claim that Windows HID scanning or Windows/Android printing has been physically device-verified -- it has not; only Android camera scanning has.

## Gate impact
A missing physical printer/HID-scanner test is:
- **Acceptable as a disclosed Controlled Paid Pilot condition** for a Retail customer, provided the pilot profile states plainly that Android camera scanning is the only physically-verified input method and that printing is "protocol supported, not click-tested" (see `first-paid-pilot-profile.md`).
- **A General Paid SMB Release blocker** for any Retail customer whose business model requires a receipt printer or a dedicated HID scanner as a hard requirement (most brick-and-mortar retail) -- the product cannot be sold on an unqualified "works with your printer/scanner" claim until real hardware validates at least one representative device of each class.
- **Not applicable to Clinic** -- Clinic has no hardware dependency of this kind at all, so this entire gate is a non-issue for Clinic's release readiness.
