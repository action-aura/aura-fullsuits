"""core/retail/escpos_raster.py -- pure byte-layer unit tests (PRINT-1).

The wrapper turns a canvas-rendered 1-bit bitmap into one ESC/POS job: bands of
at most 255 rows, optional native QR, partial cut, and the drawer pulse AFTER
the cut. Every guard here has a mutation proof recorded in the PRINT-1 report
(band size 255 -> 256 goes RED; kick-before-cut goes RED; QR always-on goes
RED).

Run on its own:
    <python> -m pytest products/retail/tests/retail_escpos_raster_test.py -q
"""
import math
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import escpos_raster as raster  # noqa: E402
from core.retail import escpos_receipt as receipt  # noqa: E402

GS_V0 = b"\x1dv0\x00"
CUT = receipt.partial_cut()
KICK = receipt.drawer_kick()


def _bitmap(width, height, fill=0x00):
    return bytes([fill]) * ((width // 8) * height)


def _bands(job, width):
    """Parse every `GS v 0` band out of `job` -> [(rows, data)]. Fails loudly
    if the bytes between bands are not exactly the next command, so a wrong
    xL/yL cannot be hidden by a lenient scan."""
    row_bytes = width // 8
    pos = job.index(GS_V0)
    out = []
    while job.startswith(GS_V0, pos):
        xl, xh, yl, yh = job[pos + 4:pos + 8]
        assert xl + xh * 256 == row_bytes, "xL xH must be bytes per row"
        rows = yl + yh * 256
        start = pos + 8
        data = job[start:start + rows * row_bytes]
        assert len(data) == rows * row_bytes
        out.append((rows, data))
        pos = start + rows * row_bytes
    return out, pos


@pytest.mark.parametrize("width", [384, 576])
@pytest.mark.parametrize("height", [1, 254, 255, 256, 510, 511, 1000])
def test_band_count_and_heights(width, height):
    job = raster.wrap_raster(_bitmap(width, height), width, height, cut=False)
    bands, _ = _bands(job, width)
    assert len(bands) == math.ceil(height / 255)
    assert sum(r for r, _ in bands) == height
    assert all(r <= 255 for r, _ in bands)
    # Only the last band may be short.
    assert all(r == 255 for r, _ in bands[:-1])


def test_band_size_is_exactly_255_not_256():
    # 256 rows must split 255 + 1 (the mutation target: BAND_ROWS = 256 makes
    # this a single band and goes RED).
    job = raster.wrap_raster(_bitmap(576, 256), 576, 256)
    bands, _ = _bands(job, 576)
    assert [r for r, _ in bands] == [255, 1]


def test_header_bytes_exact():
    job = raster.wrap_raster(_bitmap(576, 3), 576, 3, cut=False)
    # ESC @ then GS v 0 m=0, xL xH = 72 0, yL yH = 3 0
    assert job.startswith(b"\x1b@" + b"\x1d\x76\x30\x00" + bytes([72, 0, 3, 0]))


def test_bitmap_data_is_carried_verbatim_and_in_order():
    width, height = 384, 300
    row_bytes = width // 8
    bits = bytes((i * 7 + 3) % 256 for i in range(row_bytes * height))
    job = raster.wrap_raster(bits, width, height, cut=False)
    bands, _ = _bands(job, width)
    assert b"".join(d for _, d in bands) == bits


def test_golden_bytes_for_tiny_bitmap():
    # 384 dots = 48 bytes/row; 2 rows; first byte of row 1 = 0x80 (leftmost dot).
    bits = bytearray(48 * 2)
    bits[0] = 0x80
    bits[48 + 47] = 0x01
    job = raster.wrap_raster(bytes(bits), 384, 2, cut=True, kick=True)
    expected = (
        b"\x1b\x40"
        + b"\x1d\x76\x30\x00\x30\x00\x02\x00" + bytes(bits)
        + b"\x1d\x56\x42\x03"
        + b"\x1b\x70\x00\x19\x64"
    )
    assert job == expected


def test_kick_only_when_requested_and_after_the_cut():
    bits = _bitmap(576, 10)
    no_kick = raster.wrap_raster(bits, 576, 10, kick=False)
    assert KICK not in no_kick
    with_kick = raster.wrap_raster(bits, 576, 10, kick=True)
    assert with_kick.endswith(CUT + KICK)
    assert with_kick.index(CUT) < with_kick.index(KICK)


def test_kick_without_cut_still_last():
    job = raster.wrap_raster(_bitmap(384, 4), 384, 4, cut=False, kick=True)
    assert CUT not in job
    assert job.endswith(KICK)


def test_qr_present_only_with_payload():
    bits = _bitmap(576, 10)
    assert b"\x1d(k" not in raster.wrap_raster(bits, 576, 10)
    job = raster.wrap_raster(bits, 576, 10, qr_payload="ABC123")
    assert receipt._qr_command("ABC123") in job
    # QR sits after the bitmap and before the cut.
    assert job.index(receipt._qr_command("ABC123")) < job.index(CUT)


def test_cut_optional():
    assert CUT not in raster.wrap_raster(_bitmap(576, 2), 576, 2, cut=False)
    assert CUT in raster.wrap_raster(_bitmap(576, 2), 576, 2, cut=True)


@pytest.mark.parametrize("width", [0, 8, 383, 385, 575, 577, 640, -576])
def test_invalid_width_refused(width):
    with pytest.raises(raster.RasterError):
        raster.wrap_raster(b"\x00" * 100, width, 1)


@pytest.mark.parametrize("height", [0, -1, raster.MAX_HEIGHT_ROWS + 1, True, 2.5])
def test_invalid_height_refused(height):
    with pytest.raises(raster.RasterError):
        raster.wrap_raster(b"\x00" * 72, 576, height)


@pytest.mark.parametrize("delta", [-1, 1, 72])
def test_wrong_length_refused(delta):
    with pytest.raises(raster.RasterError):
        raster.wrap_raster(b"\x00" * (72 * 5 + delta), 576, 5)


def test_render_receipt_still_ends_with_the_shared_cut_bytes():
    # partial_cut() was factored out of render_receipt; the text path must
    # still emit the identical bytes.
    out = receipt.render_receipt({"lines": [], "total": 1}, kick=True)
    assert out.endswith(CUT + KICK)
