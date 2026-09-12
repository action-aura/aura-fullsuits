# Receipt Printer Architecture (Wave 1B, Part O)

## Windows (Retail)
Uses the **standard OS print spooler** via a hidden `<iframe>` + `window.print()` — not a raw ESC/POS thermal protocol. This works with:
- A thermal receipt printer installed via its own Windows driver (the driver presents it as a normal system printer; the app never talks to it directly).
- A regular document printer.
- A PDF printer (useful for testing without any physical printer at all).

Paper width (58mm / 80mm) is a `@page { size: ... }` CSS setting, configurable in **Settings → Barcode Scanner** page (which this wave also extended into a general "device configuration" area, see Part Q) and persisted to `localStorage` the same way scanner settings already were.

This approach was chosen specifically because it requires no vendor SDK, no ESC/POS command generation, and no USB/Bluetooth device enumeration — any printer the user can already print a Word document to, works.

## Android (Retail)

**Updated 2026-09-11: network (LAN) ESC/POS printing now exists.** This section
previously said no direct thermal-printer protocol was implemented. Read the
reasoning below for what changed and what did not, because only half of the
original objection has gone away.

The original objection had two halves: (a) no hardware to verify against, and
(b) "building an untested ESC/POS byte-stream generator would risk shipping
something that produces garbled output on real hardware." **(b) no longer
applies** — `products/retail/backend/core/retail/escpos_receipt.py` is a real,
tested byte generator (36 suites) that already ships in the Windows drawer-kick
path, so Android reuses a proven renderer rather than a new untested one.
**(a) still stands in full**: no printer hardware has been available, and
nothing below is verified against a physical printer.

What that made possible is a transport that can be proven WITHOUT hardware:

  * `GET /api/sub/retail/printer/receipt-payload` (retail_api.py) renders an
    existing sale through `escpos_receipt.render_receipt` and returns the bytes
    base64-encoded. The backend is shared, so this is the same renderer the
    desktop uses.
  * `printer/ReceiptPrinterAdapter.kt` — the adapter contract this document's
    next section used to describe as future work.
  * `printer/NetworkPrinterAdapter.kt` — raw bytes to a LAN printer's TCP port
    9100. Chosen deliberately over Bluetooth and over a vendor SDK: `INTERNET`
    is already declared so it needs no new permission, it needs no dependency,
    and — unlike a vendor AIDL interface, whose transaction ids are positional
    so a hand-written partial copy silently calls the WRONG method — a network
    printer is just a socket. That is what makes it testable: the unit test
    stands a real `java.net.ServerSocket` up on localhost and asserts the exact
    bytes arrive, with the connect-timeout path exercised against an
    unroutable address.
  * A "Print Receipt" button on `PaymentSuccess` beside the existing Share
    button, plus an opt-in auto-print, both gated on a configured printer.
    Settings live in `printer/PrinterPrefs.kt` (SharedPreferences), default OFF.

**Still true, and still the fallback for everyone else:** `PaymentSuccess`'s
"Share Receipt" button builds a plain-text receipt from the authoritative
`SaleResult` and hands it to Android's share sheet (`Intent.ACTION_SEND`), which
can target the system print framework, a messaging app, or email. It is
untouched, and for a shop with no LAN printer it remains the only option.

### What is NOT verified, stated plainly

  * No physical printer has ever received these bytes. Transport mechanics are
    proven (connect, write, close, timeout, failure naming); on-paper output is
    not.
  * The Compose wiring (the button and the auto-print path) is compiled and
    reasoned through but not exercised in a running app — there are no
    instrumented tests in this project, only JVM unit tests.
  * **ESC/POS text mode is ASCII-only, so these receipts print in English.**
    Arabic degrades to `?`. In this product's primary market that is a real
    limitation, not a rough edge: printing Arabic needs the printer's
    graphics/raster path (rendering each line as a bitmap) or a vendor SDK's own
    text API. Neither is built. The Android settings screen says so to the
    shopkeeper rather than leaving them to discover it at the counter.
  * Bluetooth and vendor SDKs (Sunmi's AIDL service, iMin's Gradle library) are
    still not implemented. They are the right next step for the cheap
    all-in-one Android POS hardware this market actually runs, and both need
    real devices to validate.

## The adapter contract (BUILT 2026-09-11 — this section used to say "not implemented")
`printer/ReceiptPrinterAdapter.kt` exists and `NetworkPrinterAdapter` implements
it. It follows `barcode/ScannerAdapter.kt`'s pattern as this document originally
proposed, with one deliberate departure recorded in its own doc comment: there is
no `connect`/`disconnect`/`isConnected`/reconnect-policy lifecycle. A receipt
print is a single short-lived operation, not a long-lived input session like a
scanner, so a persistent connection would be state to get wrong for no benefit —
every `print()` opens and closes its own socket.

A future Bluetooth or vendor-SDK printer implements this same interface. That is
the point of it: the button, the settings, the payload route and the byte
renderer are all transport-agnostic already, so adding Sunmi or Bluetooth is one
new class plus (for Bluetooth) manifest permissions and a device picker — not a
rework.

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
