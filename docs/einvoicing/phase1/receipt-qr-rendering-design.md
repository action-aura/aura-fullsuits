# Receipt QR rendering design

## Finding: no printer-protocol work needed at all

`docs/hardware/receipt-printer-architecture.md`: both products print
through the OS print spooler as HTML — a hidden `<iframe>` +
`window.print()` on the Windows/desktop web frontend (Retail's
`subsystem-retail.js::_printReceipt`), and a native print bridge on
Android (`window.AndroidBridge.printHtml`, Clinic's
`subsystem-clinic.js::_printInvoice`). There is no ESC/POS or other
receipt-printer command language involved anywhere. A QR code on a receipt
is therefore just an `<img>` tag with a `data:` URI — no `GS ( k` support,
no printer compatibility matrix entry required.

This is the cheapest possible path and matches the architecture doc's own
data-safety rule: "every printed value comes from the server's
authoritative response."

## `qr.py`

Uses `segno` — pure Python, zero transitive dependencies, universal wheel,
safe under Chaquopy on Android without adding a new native dependency (see
`requirements/base.txt`). `qr_for_outbox_row(row)` prefers the tax
authority's own rendering (`qr_image_base64`) over locally re-encoding
`qr_payload` — never re-derive a value ISTD already gave us.

**Real bug found while building this:** segno's SVG writer emits `bytes`,
not `str` — writing to an `io.StringIO()` raised
`TypeError: string argument expected, got 'bytes'`. Fixed by using
`io.BytesIO()` for both SVG and PNG rendering. Caught by
`test_qr.py::test_svg_data_uri_shape` before it ever reached a real
receipt.

## Submission is async — the receipt usually predates clearance

The sale/invoice completes and prints before the outbox worker has
necessarily submitted it (see `jofotara-integration-architecture.md`'s
"why asynchronous" section). `_printReceipt` / `_printInvoice` therefore do
**one best-effort status check** at the moment of printing (never blocking
or failing the print on error):

- If the entry is `CLEARED`: embed the real QR
  (`/api/einvoicing/qr/<ref>.png`).
- Otherwise: print an honest "pending government clearance" line instead
  of fabricating a QR that doesn't exist yet.
- If the feature was never enabled for this sale/invoice
  (`saleData.einvoice` absent, or a 404 on the outbox lookup): nothing is
  added to the receipt at all — byte-for-byte identical to a disabled
  install's receipt.

A reprint after the worker has run (or after a manual "Submit queue now")
will show the real QR once cleared.

## Explicit Phase 1 scope cut

The on-screen receipt modal's status badge and the transactions list's
status column (both cosmetic, non-load-bearing) were not built in this
pass, to keep pace against the size of the rest of this wave. Tracked as a
deferred polish item — see `phase1-residual-risk-register.md`.
