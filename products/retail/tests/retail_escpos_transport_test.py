"""core/retail/escpos_transport.py -- Windows RAW print-spooler transport tests.

WHY THIS FILE EXISTS

`escpos_transport.py` is how `escpos_receipt.py`'s byte stream actually
reaches a printer: Windows print-spooler RAW mode via ctypes against
`winspool.drv` (see that module's docstring for why ctypes-direct, and why
every Windows-only access is gated behind `_require_windows()`). CI runs on
Linux and imports every backend module during collection (CLAUDE.md), so
this file proves two things beyond "it prints": the module IMPORTS cleanly
off Windows, and its public functions raise a NAMED error there rather than
an `AttributeError` from touching `ctypes.windll` on a platform that
doesn't have it.

No real printer is used anywhere in this file -- `send_to_file` (a
byte-for-byte round trip) stands in for `send_raw` wherever a destination
is needed, exactly as the module's own docstring describes it being used
for.

Self-booting, matching the convention here (there is no shared conftest.py
for products/retail/tests/). Run on its own, from the repo root:

    <python> -m pytest products/retail/tests/retail_escpos_transport_test.py -q
"""
import importlib
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]         # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                     # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.retail import escpos_transport as transport  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════
# send_to_file -- byte-for-byte round trip
# ═════════════════════════════════════════════════════════════════════════

def test_send_to_file_round_trips_bytes_exactly(tmp_path):
    payload = bytes(range(256)) + b"\x1b\x40\x1d\x56\x42\x03"  # every byte value, plus real ESC/POS bytes
    out_path = tmp_path / "receipt.bin"
    transport.send_to_file(str(out_path), payload)
    assert out_path.read_bytes() == payload


def test_send_to_file_rejects_non_bytes_payload(tmp_path):
    with pytest.raises(TypeError):
        transport.send_to_file(str(tmp_path / "x.bin"), "not bytes")


def test_send_to_file_accepts_bytearray(tmp_path):
    payload = bytearray(b"\x1b\x40hello\n")
    out_path = tmp_path / "receipt2.bin"
    transport.send_to_file(str(out_path), payload)
    assert out_path.read_bytes() == bytes(payload)


# ═════════════════════════════════════════════════════════════════════════
# list_printers -- real Windows spooler call, no real printer required
# ═════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(sys.platform != "win32", reason="exercises the real Windows print spooler")
def test_list_printers_returns_strings_and_does_not_raise():
    names = transport.list_printers()
    assert isinstance(names, list)
    for name in names:
        assert isinstance(name, str)


# ═════════════════════════════════════════════════════════════════════════
# Non-Windows behaviour -- named error, not AttributeError; clean import
# ═════════════════════════════════════════════════════════════════════════

def test_module_imports_cleanly_when_platform_is_not_windows(monkeypatch):
    # Reload under a patched sys.platform to prove the module's TOP-LEVEL
    # code never touches ctypes.windll/ctypes.WinDLL -- if it did, this
    # reload would itself raise AttributeError before any test assertion
    # ran, regardless of what functions we go on to call.
    monkeypatch.setattr(sys, "platform", "linux")
    reloaded = importlib.reload(transport)
    assert reloaded is transport
    # monkeypatch restores the real sys.platform when this test returns;
    # nothing in this module caches platform at import time, so no further
    # reload is needed to bring it back to normal behaviour.


def test_send_raw_raises_named_error_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(transport.EscPosTransportError):
        transport.send_raw("Any Printer", b"\x1b@")


def test_list_printers_raises_named_error_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(transport.EscPosTransportError):
        transport.list_printers()


def test_non_windows_error_is_not_attributeerror(monkeypatch):
    # The regression this guards: forgetting `_require_windows()` as the
    # FIRST statement before touching ctypes.windll would surface as a bare
    # AttributeError instead of this module's own named error.
    monkeypatch.setattr(sys, "platform", "linux")
    try:
        transport.send_raw("Any Printer", b"\x1b@")
    except AttributeError:
        pytest.fail("send_raw raised AttributeError instead of EscPosTransportError on a non-Windows platform")
    except transport.EscPosTransportError:
        pass
