"""Regenerate products/retail/packaging/aura_retail.ico from the brand SVG.

Not part of the build: the .ico is COMMITTED, so a build machine needs neither
Edge nor Pillow. Run this only when the brand mark changes:

    py -3.14 scripts/release/make_app_icon.py

How: Edge (always present on Windows 10/11) renders products/retail/frontend/
brand/aura-app-icon.svg at 256x256 on a transparent page, and Pillow packs that
into one multi-size .ico (16..256). Windows picks the nearest size for the
taskbar, Start menu, Explorer and the installer; a single-size .ico gets
scaled by the shell and looks soft at 16/24 px.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SVG = ROOT / "products" / "retail" / "frontend" / "brand" / "aura-app-icon.svg"
OUT = ROOT / "products" / "retail" / "packaging" / "aura_retail.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
RENDER = 256


def find_edge() -> str:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidate = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.is_file():
                return str(candidate)
    raise SystemExit("msedge.exe not found")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        # Inlined, not <img src=file://...>: Edge blocks a page in %TEMP% from
        # loading a file:// image from the repo (shows a broken-image icon).
        svg = SVG.read_text(encoding="utf-8")
        svg = svg[svg.index("<svg"):].replace("<svg ", '<svg width="%d" height="%d" ' % (RENDER, RENDER), 1)
        page.write_text(
            "<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:transparent}"
            "svg{display:block}</style>" + svg,
            encoding="utf-8",
        )
        png = Path(tmp) / "icon.png"
        subprocess.run(
            [find_edge(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--default-background-color=00000000", "--user-data-dir=" + str(Path(tmp) / "profile"),
             "--window-size=%d,%d" % (RENDER, RENDER), "--screenshot=" + str(png), page.as_uri()],
            check=True, timeout=120, capture_output=True,
        )
        image = Image.open(png).convert("RGBA")
        if image.size != (RENDER, RENDER):
            image = image.crop((0, 0, RENDER, RENDER))
        image.save(OUT, format="ICO", sizes=SIZES)
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    sys.exit(main())
