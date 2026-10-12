"""Regenerate the cross-language ESC/POS raster golden files (PRINT-2).

WHY: the phone re-implements `core.retail.escpos_raster.wrap_raster` in Kotlin
(android/.../printer/EscPosRaster.kt). Two hand-written encoders drift
silently; a byte-for-byte golden produced by the PYTHON module is the contract
the Kotlin unit test (EscPosRasterGoldenTest) must reproduce. For each case this
writes three files into android/aura-retail/app/src/test/resources/escpos_golden/:

    <name>.json      parameters (width, height, qr, cut, kick)
    <name>.in.bin    the packed 1-bit bitmap fed to the encoder
    <name>.out.bin   what Python's wrap_raster returned for it

Run from the repo root:
    .venv/Scripts/python.exe products/retail/tests/tools/gen_escpos_golden.py
Regenerating must produce an empty `git diff`; a diff means the Python wire
format changed and the Kotlin encoder must follow in the same commit.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "products" / "retail" / "backend"))

from core.retail import escpos_raster  # noqa: E402

OUT = ROOT / "android" / "aura-retail" / "app" / "src" / "test" / "resources" / "escpos_golden"

QR = "ISTD-TLV-0123456789"


def white(w, h):
    return bytes(w // 8 * h)


def checker(w, h):
    rb = w // 8
    return b"".join(bytes([0xAA if r % 2 == 0 else 0x55] * rb) for r in range(h))


def ramp(w, h):
    # Distinct bytes per position so a mis-sliced band or a shifted row shows.
    rb = w // 8
    return bytes(((r * 7 + c * 13) & 0xFF) for r in range(h) for c in range(rb))


CASES = {
    # name: (width, height, bitmap fn, qr, cut, kick)
    "white_576_plain": (576, 10, white, None, True, False),
    "checker_384_qr": (384, 20, checker, QR, True, False),
    "tall_576_two_bands_kick": (576, 300, ramp, None, True, True),
    "tall_384_qr_kick_nocut": (384, 300, ramp, QR, False, True),
    "exact_band_576": (576, 255, ramp, None, True, False),
    "band_plus_one_384_kick": (384, 256, ramp, None, True, True),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (w, h, fn, qr, cut, kick) in CASES.items():
        bits = fn(w, h)
        out = escpos_raster.wrap_raster(bits, w, h, qr_payload=qr, cut=cut, kick=kick)
        (OUT / f"{name}.in.bin").write_bytes(bits)
        (OUT / f"{name}.out.bin").write_bytes(out)
        (OUT / f"{name}.json").write_text(
            json.dumps({"width": w, "height": h, "qr": qr, "cut": cut, "kick": kick}, indent=1) + "\n",
            newline="\n")
        print(name, len(bits), "->", len(out))


if __name__ == "__main__":
    main()
