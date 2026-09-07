"""Rebuild aura-lockup.svg with the wordmark as outlines (no font dependency).
Fetches Outfit 700 and 300 from Google Fonts (woff2), extracts the glyphs for
AURA / RETAIL with fontTools, and writes the paths with the lockup's tracking.
"""
import io
import re
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

OUT = Path(r"C:\Users\MSI\Desktop\aura-fullsuits\.claude\worktrees\ci-hardening-w0.3-continue\products\retail\frontend\brand\aura-lockup.svg")
OUT_DARK = OUT.with_name("aura-lockup-on-dark.svg")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def fetch_font(weight):
    css_url = f"https://fonts.googleapis.com/css2?family=Outfit:wght@{weight}&display=swap"
    req = urllib.request.Request(css_url, headers={"User-Agent": UA})
    css = urllib.request.urlopen(req, timeout=30).read().decode()
    # The API answers one @font-face per unicode subset; take the block whose
    # unicode-range covers basic Latin (U+0000-00FF), not merely the first.
    blocks = re.findall(r"@font-face\s*\{([^}]*)\}", css)
    url = None
    for blk in blocks:
        if "U+0000-00FF" in blk:
            m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", blk)
            if m:
                url = m.group(1)
                break
    if not url:
        raise SystemExit(f"no Latin woff2 url in the CSS response ({len(blocks)} blocks)")
    data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read()
    return TTFont(io.BytesIO(data))


def word_paths(font, text, size, x, baseline_y, tracking_px):
    """SVG path data for `text` set at `size` px with the given tracking, in
    user units, starting at x with the baseline at baseline_y."""
    upem = font["head"].unitsPerEm
    scale = size / upem
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    hmtx = font["hmtx"]
    paths = []
    pen_x = x
    for ch in text:
        gname = cmap[ord(ch)]
        pen = SVGPathPen(glyphs)
        # y flips (font units go up), then scale, then translate.
        tpen = TransformPen(pen, (scale, 0, 0, -scale, pen_x, baseline_y))
        glyphs[gname].draw(tpen)
        d = pen.getCommands()
        if d:
            # Two decimals are plenty at 640 user units; drop the float noise.
            d = re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group(0)):.2f}".rstrip("0").rstrip("."), d)
            paths.append(d)
        adv = hmtx[gname][0] * scale
        pen_x += adv + tracking_px
    return " ".join(paths), pen_x


bold = fetch_font(700)
light = fetch_font(300)
aura_d, aura_end = word_paths(bold, "AURA", 88, 210, 112, 14)
retail_d, _ = word_paths(light, "RETAIL", 30, 214, 158, 15)
print("wordmark width:", round(aura_end - 210))

MARK = """  <g transform="translate(20 20) scale(0.625)">
    <circle cx="128" cy="128" r="94" fill="none" stroke="url(#lk-ring)" stroke-width="15"
            stroke-linecap="round" stroke-dasharray="492 99" stroke-dashoffset="-32"
            transform="rotate(-90 128 128)"/>
    <circle cx="196" cy="60" r="20" fill="url(#lk-spark)"/>
    <circle cx="196" cy="60" r="6.5" fill="#ffffff"/>
    <path d="M 80 178 L 128 76 L 176 178" fill="none" stroke="{ink}" stroke-width="19"
          stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M 106 142 L 150 142" fill="none" stroke="{ink}" stroke-width="15" stroke-linecap="round"/>
  </g>"""


def svg(ink, product_fill, ring_stops):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 200" width="640" height="200" role="img" aria-labelledby="t d">
  <title id="t">Aura Retail lockup</title>
  <desc id="d">The Aura mark beside the AURA wordmark with RETAIL beneath it. The wordmark is outlined (Outfit 700 and 300), so no font is needed to render it.</desc>
  <defs>
    <linearGradient id="lk-ring" x1="0.15" y1="0.9" x2="0.85" y2="0.1">
      {ring_stops}
    </linearGradient>
    <radialGradient id="lk-spark" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="0.45" stop-color="#bff7ee"/>
      <stop offset="1" stop-color="#5fe3d0" stop-opacity="0"/>
    </radialGradient>
  </defs>
{MARK.format(ink=ink)}
  <!-- AURA, Outfit 700, 88 px, tracked 14 px -- as outlines -->
  <path fill="{ink}" d="{aura_d}"/>
  <!-- RETAIL, Outfit 300, 30 px, tracked 15 px -- as outlines -->
  <path fill="{product_fill}" d="{retail_d}"/>
</svg>
"""


light_stops = '<stop offset="0" stop-color="#1745a9"/>\n      <stop offset="0.55" stop-color="#3f7be6"/>\n      <stop offset="1" stop-color="#5fe3d0"/>'
dark_stops = '<stop offset="0" stop-color="#3f7be6"/>\n      <stop offset="0.55" stop-color="#6ea8ff"/>\n      <stop offset="1" stop-color="#5fe3d0"/>'
OUT.write_text(svg("#0f1319", "#1745a9", light_stops), encoding="utf-8")
OUT_DARK.write_text(svg("#edf2f8", "#5fe3d0", dark_stops), encoding="utf-8")
print("wrote", OUT.name, round(OUT.stat().st_size / 1024), "KB and", OUT_DARK.name)
