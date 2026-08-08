"""JoFotara e-invoicing -- QR code rendering for the printed receipt.

Retail/Clinic receipts print through the OS print spooler as HTML (a hidden
<iframe> + window.print() -- see
docs/hardware/receipt-printer-architecture.md's "QR codes on receipts"
section, added by this wave). There is no ESC/POS or other printer-protocol
QR command involved at all -- a QR code on a receipt is just an <img> tag,
so this module only needs to produce a data: URI, not talk to any hardware.

Uses segno (pure Python, zero dependencies, universal wheel -- safe under
Chaquopy on Android; see requirements/base.txt).
"""
from __future__ import annotations

import base64
from typing import Optional


def render_qr_svg_data_uri(payload: str, scale: int = 4) -> str:
    import io
    import segno

    qr = segno.make(payload, error='m')
    buf = io.BytesIO()  # segno's SVG writer emits bytes, not str
    qr.save(buf, kind='svg', scale=scale, xmldecl=False)
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/svg+xml;base64,{encoded}"


def render_qr_png_data_uri(payload: str, scale: int = 6) -> str:
    import io
    import segno

    qr = segno.make(payload, error='m')
    buf = io.BytesIO()
    qr.save(buf, kind='png', scale=scale)
    encoded = base64.b64encode(buf.getvalue()).decode('ascii')
    return f"data:image/png;base64,{encoded}"


def qr_for_outbox_row(row: dict) -> Optional[str]:
    """Prefers the tax authority's own rendering (qr_image_base64) over
    locally re-encoding qr_payload -- never re-derive a value ISTD already
    gave us. Matches receipt-printer-architecture.md's data-safety rule:
    "every printed value comes from the server's authoritative response."

    `row` is any mapping with 'qr_image_base64' and/or 'qr_payload' keys
    (an einvoice_outbox row, typically). Returns None if neither is present
    (e.g. the submission hasn't cleared yet) -- callers must not print a QR
    block in that case."""
    image_b64 = row.get('qr_image_base64')
    if image_b64:
        return f"data:image/png;base64,{image_b64}"
    payload = row.get('qr_payload')
    if payload:
        return render_qr_png_data_uri(payload)
    return None
