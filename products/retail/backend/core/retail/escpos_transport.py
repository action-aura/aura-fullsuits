"""Aura Retail -- Windows RAW print-spooler transport for ESC/POS bytes.

WHY CTYPES DIRECT AGAINST winspool.drv, NOT A LIBRARY

CLAUDE.md forbids adding a dependency without being asked, and `ctypes` is
stdlib -- the same choice this codebase already made for DPAPI in
`commercial_runtime/licensing_contracts/device_identity.py`. Sending raw
bytes to a Windows printer this way (OpenPrinter -> StartDocPrinter with a
DOCINFO whose datatype is `"RAW"` -> StartPagePrinter -> WritePrinter ->
End/Close) is Microsoft's own documented technique, distributed for years
as the "RawPrinterHelper" sample. `"RAW"` as the datatype matters
specifically: it tells the spooler to hand the driver the byte stream
UNMODIFIED, bypassing the EMF/rendering pipeline a normal print job goes
through -- the ESC/POS drawer-kick and cut commands in
`escpos_receipt.py`'s output must reach the printer byte-for-byte, not be
reinterpreted as printable text or reformatted.

WHY IMPORT MUST NEVER TOUCH ctypes.windll

CI runs on Linux (CLAUDE.md), which imports every backend module during
test collection. `ctypes.windll` (and `ctypes.WinDLL`) do not exist on
non-Windows CPython builds and raise `AttributeError` merely by being
ACCESSED, not called. So every Windows-only ctypes access in this module
stays inside a function body, gated behind `_require_windows()` as the
very first statement of that function -- never at module scope, never as a
class/default-argument value evaluated at import time. This is the exact
same guard `device_identity.py`'s `_dpapi_functions()` uses for
`crypt32`/`kernel32`; this module follows it for `winspool.drv`.

WHY FULL argtypes/restype ON EVERY ctypes CALL

`device_identity.py`'s `_dpapi_functions()` docstring documents a real bug
this avoids: calling a Windows API through ctypes with NO prototype
declared relies on ctypes' implicit argument conversion, which is
documented as unreliable for structure-pointer arguments -- a DPAPI
protect-then-unprotect round trip in that module failed ~35-45% of the
time with no prototypes declared, and 0 times once argtypes/restype were
declared explicitly. Every winspool.drv function used below has its full
signature declared for the same reason, even though this module's
structures (DOCINFO, PRINTER_INFO_4) are simpler than DATA_BLOB.
"""
from __future__ import annotations

import sys


class EscPosTransportError(RuntimeError):
    """Raised for any RAW-printer-transport failure -- including "this is
    not Windows" -- so a caller can catch ONE exception type instead of
    guessing between `AttributeError` (a Windows-only ctypes attribute that
    does not exist on this platform), `OSError`, and spooler-specific
    failures.
    """


def _require_windows() -> None:
    """Must be the first statement of any function in this module that
    touches `ctypes.windll`/`ctypes.WinDLL` -- see module docstring's
    "WHY IMPORT MUST NEVER TOUCH ctypes.windll" section. Checking
    `sys.platform` live (not caching it at import time) also means a test
    can monkeypatch `sys.platform` and immediately observe the change,
    which is exactly how this module's own test suite proves the
    non-Windows path without needing a second interpreter.
    """
    if sys.platform != "win32":
        raise EscPosTransportError(
            "ESC/POS RAW printing needs the Windows print spooler "
            f"(winspool.drv); running on {sys.platform!r}, not win32."
        )


def _winspool():
    """Builds and returns the ctypes machinery this module needs:
    `(ctypes, wintypes, DOCINFO, PRINTER_INFO_4, winspool, kernel32)`,
    every winspool.drv function declared with full argtypes/restype (see
    module docstring). Calls `_require_windows()` FIRST, before importing
    anything Windows-only, so a non-Windows caller gets
    `EscPosTransportError`, never an `AttributeError` from touching
    `ctypes.windll`/`ctypes.WinDLL` on a platform that doesn't have them.
    """
    _require_windows()
    import ctypes
    from ctypes import wintypes

    class DOCINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_int),
            ("pDocName", wintypes.LPCWSTR),
            ("pOutputFile", wintypes.LPCWSTR),
            ("pDatatype", wintypes.LPCWSTR),
        ]

    class PRINTER_INFO_4(ctypes.Structure):
        # The shallowest EnumPrinters info level -- all `list_printers`
        # needs is `pPrinterName` for a settings dropdown; levels 2/5 pull
        # in fields (driver name, port, security descriptor) this module
        # has no use for and would have to declare full structures for.
        _fields_ = [
            ("pPrinterName", wintypes.LPWSTR),
            ("pServerName", wintypes.LPWSTR),
            ("Flags", wintypes.DWORD),
        ]

    winspool = ctypes.WinDLL("winspool.drv")
    kernel32 = ctypes.windll.kernel32

    winspool.OpenPrinterW.argtypes = [
        wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p,
    ]
    winspool.OpenPrinterW.restype = wintypes.BOOL

    winspool.ClosePrinter.argtypes = [wintypes.HANDLE]
    winspool.ClosePrinter.restype = wintypes.BOOL

    winspool.StartDocPrinterW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(DOCINFO),
    ]
    winspool.StartDocPrinterW.restype = wintypes.DWORD

    winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    winspool.EndDocPrinter.restype = wintypes.BOOL

    winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.StartPagePrinter.restype = wintypes.BOOL

    winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.EndPagePrinter.restype = wintypes.BOOL

    winspool.WritePrinter.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    winspool.WritePrinter.restype = wintypes.BOOL

    winspool.EnumPrintersW.argtypes = [
        wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
    ]
    winspool.EnumPrintersW.restype = wintypes.BOOL

    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD

    return ctypes, wintypes, DOCINFO, PRINTER_INFO_4, winspool, kernel32


def list_printers() -> list[str]:
    """Names of every locally-installed or connected Windows printer.

    Uses the documented two-call `EnumPrinters` pattern: the first call
    (NULL buffer) asks only "how many bytes would you need", because there
    is no way to know that in advance -- the number and length of printer
    names varies per machine -- and the second call does the real read into
    a buffer of exactly that size.
    """
    ctypes, wintypes, _DOCINFO, PRINTER_INFO_4, winspool, kernel32 = _winspool()

    PRINTER_ENUM_LOCAL = 0x00000002
    PRINTER_ENUM_CONNECTIONS = 0x00000004
    flags = PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    level = 4

    needed = wintypes.DWORD(0)
    returned = wintypes.DWORD(0)
    # Probe call: expected to "fail" (too-small buffer) -- only `needed` matters.
    winspool.EnumPrintersW(flags, None, level, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if needed.value == 0:
        return []

    buf = ctypes.create_string_buffer(needed.value)
    ok = winspool.EnumPrintersW(
        flags, None, level, buf, needed.value, ctypes.byref(needed), ctypes.byref(returned)
    )
    if not ok:
        raise EscPosTransportError(f"EnumPrinters failed (GetLastError={kernel32.GetLastError()}).")

    entries = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4 * returned.value)).contents
    return [p.pPrinterName for p in entries if p.pPrinterName]


def send_raw(printer_name: str, payload: bytes, *, job_name: str = "Aura Retail receipt") -> None:
    """Sends `payload` to `printer_name` in RAW spooler mode.

    Every successful `OpenPrinter` is matched by `ClosePrinter` in a
    `finally`, every successful `StartDocPrinter` by `EndDocPrinter`, every
    successful `StartPagePrinter` by `EndPagePrinter` -- nested `finally`
    blocks, innermost first -- so a failure partway through (a
    `WritePrinter` error, say) never leaks a spooler handle or leaves a
    half-written job sitting open in the queue.
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
    payload = bytes(payload)

    ctypes, wintypes, DOCINFO, _PRINTER_INFO_4, winspool, kernel32 = _winspool()

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise EscPosTransportError(
            f"OpenPrinter({printer_name!r}) failed (GetLastError={kernel32.GetLastError()})."
        )
    try:
        doc_info = DOCINFO(ctypes.sizeof(DOCINFO), job_name, None, "RAW")
        job_id = winspool.StartDocPrinterW(handle, 1, ctypes.byref(doc_info))
        if not job_id:
            raise EscPosTransportError(
                f"StartDocPrinter({printer_name!r}) failed (GetLastError={kernel32.GetLastError()})."
            )
        try:
            if not winspool.StartPagePrinter(handle):
                raise EscPosTransportError(
                    f"StartPagePrinter({printer_name!r}) failed (GetLastError={kernel32.GetLastError()})."
                )
            try:
                buf = ctypes.create_string_buffer(payload, len(payload))
                written = wintypes.DWORD(0)
                ok = winspool.WritePrinter(handle, buf, len(payload), ctypes.byref(written))
                if not ok or written.value != len(payload):
                    raise EscPosTransportError(
                        f"WritePrinter({printer_name!r}) wrote {written.value} of "
                        f"{len(payload)} bytes (GetLastError={kernel32.GetLastError()})."
                    )
            finally:
                winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)


def send_to_file(path, payload: bytes) -> None:
    """Writes `payload` to `path` byte-for-byte -- how someone without a
    physical printer verifies ESC/POS output, and what this module's own
    tests use in place of a real printer.

    Binary mode (`"wb"`) is required, not incidental: on Windows, text mode
    silently rewrites every `0x0A` byte to `0x0D 0x0A` on write -- the exact
    bug `device_identity.py`'s `_dpapi_protect_verified` docstring documents
    corrupting an on-disk device key in this same codebase. An ESC/POS
    payload is at least as exposed to it: `0x0A` appears constantly (every
    printed receipt line ends with it, from `escpos_receipt._line`).
    """
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
    with open(path, "wb") as fh:
        fh.write(bytes(payload))
