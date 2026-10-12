"""Aura Retail -- ESC/POS RASTER receipt wrapper (pure, no I/O).

WHY THIS MODULE EXISTS (PRINT-1)

`escpos_receipt.render_receipt` is an ASCII text path: every Arabic character
on it prints as `?` (see that module's "TEXT ENCODING" section), and no
ESC/POS text code page reliably shapes Arabic across printer brands. The one
place in this stack that already renders Arabic correctly -- joined letters,
right-to-left, mixed with Latin digits -- is the browser. So the till draws
the receipt on a <canvas> (`frontend/receipt-raster.js`), thresholds it to a
1-bit bitmap, and sends the bitmap here. This module only WRAPS those bits in
printer commands; it never sees a font, a language or a sale.

ONE JOB, ONE SPOOLER DOCUMENT. The drawer-kick pulse is appended AFTER the cut
in the same byte string. Before this the till fired a separate
`POST /printer/kick` next to a browser print dialog, so "receipt printed but
the drawer opened before / after / twice" was possible; a single job removes
that whole class of ordering bug.

BAND SIZE 255. `GS v 0` carries its row count in `yL yH` (16 bits), so one
command could technically hold thousands of rows, but older and cheap
printers (the Xprinter / Rongta class sold in Jordan) overflow their receive
buffer or drop the command on tall rasters. 255 rows per `GS v 0` is the
conservative size every Epson TM-T88II-era firmware accepts. The row count
`yL` of the LAST band is the remainder, so the bands always sum to `height`
(pinned by retail_escpos_raster_test.py, which also mutation-proves 255).

WIDTHS. 576 dots = 80 mm paper, 384 dots = 58 mm paper (both 203 dpi). Any
other width is refused rather than guessed: a bitmap whose byte-width does not
match what the printer was told prints sheared diagonal garbage that wastes a
roll before anyone works out why.

QR. When `qr_payload` is given the printer draws it natively with the existing
`GS ( k` builder (`escpos_receipt._qr_command`) -- a printer-generated QR is
sharper and scans better than a thresholded canvas one, and the builder
already carries the length-prefix scar tissue (len+3). No second copy of any
QR / cut / kick bytes lives here: they are imported.
"""
from __future__ import annotations

from core.retail import escpos_receipt

ESC = escpos_receipt.ESC
GS = escpos_receipt.GS

#: Dots across the paper: 58 mm and 80 mm thermal heads at 203 dpi.
VALID_WIDTHS_DOTS = (384, 576)

#: Rows per `GS v 0` command -- see the module docstring ("BAND SIZE 255").
BAND_ROWS = 255

#: A receipt taller than this is a bug (or an attack), not a receipt: ~2 m of
#: paper at 203 dpi. Keeps a hostile height from allocating gigabytes.
MAX_HEIGHT_ROWS = 16000


class RasterError(ValueError):
    """The bitmap handed to `wrap_raster` is not a printable receipt bitmap."""


def _band(packed_row_bytes: int, rows: int, data: bytes) -> bytes:
    """One `GS v 0 m xL xH yL yH d...` raster command (m=0: normal density).

    `xL xH` is the width in BYTES per row (not dots); `yL yH` the rows in THIS
    band. Both little-endian 16-bit.
    """
    return (
        GS + b"v0" + b"\x00"
        + bytes([packed_row_bytes & 0xFF, (packed_row_bytes >> 8) & 0xFF,
                 rows & 0xFF, (rows >> 8) & 0xFF])
        + data
    )


def wrap_raster(
    packed_bits: bytes,
    width_dots: int,
    height: int,
    *,
    qr_payload: str | None = None,
    cut: bool = True,
    kick: bool = False,
) -> bytes:
    """Wrap a packed 1-bit bitmap into one complete ESC/POS print job.

    `packed_bits`: `height` rows of `width_dots // 8` bytes, MSB-first (the
    leftmost dot of a row is bit 7 of its first byte), 1 = black.

    Layout of the job: `ESC @`, the bitmap in <=255-row `GS v 0` bands, the
    optional native QR (centred), the partial cut, and -- only when `kick` --
    the drawer pulse AFTER the cut. Kick is last on purpose: the paper is
    already out and cut, so the drawer pops as the customer is handed it.

    Raises `RasterError` for an unsupported width, a non-positive/oversized
    height, or a byte length that does not equal `width_dots//8 * height`.
    """
    if width_dots not in VALID_WIDTHS_DOTS:
        raise RasterError(f"width_dots must be one of {VALID_WIDTHS_DOTS}, got {width_dots!r}")
    if not isinstance(height, int) or isinstance(height, bool) or height < 1 or height > MAX_HEIGHT_ROWS:
        raise RasterError(f"height must be an integer in 1..{MAX_HEIGHT_ROWS}, got {height!r}")
    row_bytes = width_dots // 8
    if len(packed_bits) != row_bytes * height:
        raise RasterError(
            f"bitmap is {len(packed_bits)} bytes; {width_dots} dots x {height} rows needs {row_bytes * height}"
        )

    out = bytearray()
    out += ESC + b"@"  # initialise: reset to power-on state
    for start in range(0, height, BAND_ROWS):
        rows = min(BAND_ROWS, height - start)
        out += _band(row_bytes, rows, packed_bits[start * row_bytes:(start + rows) * row_bytes])

    if qr_payload:
        out += ESC + b"a\x01"  # centre the symbol
        out += escpos_receipt._qr_command(qr_payload)
        out += b"\n"
        out += ESC + b"a\x00"

    if cut:
        out += escpos_receipt.partial_cut()
    if kick:
        out += escpos_receipt.drawer_kick()
    return bytes(out)
