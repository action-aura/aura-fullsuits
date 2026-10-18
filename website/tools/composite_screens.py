"""Composites real Aura Retail screenshots onto the blank displays of the two mood photos.

Why this exists: the photos are AI-generated illustrations (Nano Banana, 2026-10-18) of shop counters with
blank light-grey screens. The site shows them only with a real Aura screen on the display and a caption that
calls them illustrations (design-system.md section 7). This tool is the single place the screen corners and
crops live, so a re-render is reproducible.

Run from website/:  py -3.14 tools/composite_screens.py
Inputs : tools/photos/<master>.jpg (blank-screen masters), src/assets/shots/<shot>.png
Outputs: src/assets/photos/retail-<photo>-<lang>.jpg, 1264x848, JPEG q86 progressive, plus a self-check.

Method: Pillow perspective transform at 2x supersample (the mask is warped the same way, so the screen edge is
antialiased when it comes back to 1x); the master's own screen luminance, relative to its mean, is multiplied
back over the screenshot at 75% strength so the photo's glare and gradient survive; 7% of the blank screen's
grey-blue is mixed in as the LCD cast; gain .96 keeps the ivory UI from blowing out.

Self-check, three guards, exit 1 on any failure (thresholds measured 2026-10-18, job tmp photos-proto):
  1. The quad IS a blank screen in the master: luminance std < 12 and mean chroma (max - min channel) < 28
     inside it. The real screens measure std 2 and chroma 12 to 15; a quad shifted onto wood, glass or the
     window measures std 49 to 60 and chroma 44 to 55. This is what catches wrong corners or a wrong master.
  2. The UI landed: mean(R - B) inside rises by more than 15 over the blank grey (Aura's field is warm ivory,
     the blank screens are cool grey at about -12 to -15; measured rises are +25 and +31).
  3. Nothing outside the screen changed: beyond the quad dilated 3px, fewer than 0.05% of pixels differ from
     the master by more than 24/255 in any channel. JPEG re-encoding alone gives 0.0003%; a paste that spills
     outside the quad gives about 5%. This is what catches a mask or shading bug.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Screen corners in master-photo pixels, order TL, TR, BR, BL. Measured 2026-10-18 (job tmp photos-proto/aspect.py):
# largest bright, low-saturation blob inside a region of interest, contour simplified to 4 points, checked by eye at 2x.
# ASPECT is the screen's true width/height from its two vanishing points (Zhang and He, whiteboard scanning);
# the screenshot is cropped to that aspect, never stretched.
PLACEMENTS = [
    {
        "photo": "grocery",
        "master": "grocery.jpg",
        "quad": [(522, 368), (754, 371), (685, 589), (449, 556)],
        "aspect": 1.34,
        # The dashboard's content area without its sidebar is 2402x1800, which is 4:3 already: sidebar edge at x=478
        # (EN, left) and x=2402 (AR, right), measured on the PNGs. Height trimmed 3px top and 4px bottom to 1.34.
        "shots": {"en": ("dashboard-en.png", (478, 3, 2880, 1796)), "ar": ("dashboard-ar.png", (0, 3, 2402, 1796))},
    },
    {
        "photo": "phone-shop",
        "master": "phone-shop.jpg",
        "quad": [(717, 326), (979, 330), (945, 490), (675, 467)],
        "aspect": 1.695,
        # 16:10 till to a 1.695 screen: keep the header, trim 101px off the bottom, where the third row of product
        # cards is already cut by the screenshot's own edge.
        "shots": {"en": ("pos-en.png", (0, 0, 2880, 1699)), "ar": ("pos-ar.png", (0, 0, 2880, 1699))},
    },
]
SUPERSAMPLE = 2
SHADE_STRENGTH = 0.75
LCD_CAST = 0.07
GAIN = 0.96
JPEG_QUALITY = 86
MAX_KB = 220
OUT_SIZE = (1264, 848)


def perspective_coeffs(dst_quad, src_rect):
    """Coefficients for Image.transform(PERSPECTIVE): output (x, y) -> input (u, v), 8 unknowns from 4 pairs."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dst_quad, src_rect):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    return np.linalg.solve(np.array(rows, float), np.array(rhs, float)).tolist()


def composite(master_path: Path, quad, shot_path: Path, crop, out_path: Path) -> dict:
    master = Image.open(master_path).convert("RGB")
    width, height = master.size
    shot = Image.open(shot_path).convert("RGB").crop(crop)
    big_quad = [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in quad]
    edge = max(np.hypot(big_quad[1][0] - big_quad[0][0], big_quad[1][1] - big_quad[0][1]),
               np.hypot(big_quad[2][0] - big_quad[3][0], big_quad[2][1] - big_quad[3][1]))
    # Pre-shrink the 2880px screenshot to twice its on-canvas width: transform() does not prefilter.
    pre_w = int(round(edge * 2))
    pre_h = int(round(pre_w * shot.height / shot.width))
    shot = shot.resize((pre_w, pre_h), Image.LANCZOS)
    coeffs = perspective_coeffs(big_quad, [(0, 0), (pre_w, 0), (pre_w, pre_h), (0, pre_h)])
    big = (width * SUPERSAMPLE, height * SUPERSAMPLE)
    warped = shot.transform(big, Image.PERSPECTIVE, coeffs, Image.BICUBIC).resize((width, height), Image.LANCZOS)
    mask = (Image.new("L", (pre_w, pre_h), 255).transform(big, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
            .resize((width, height), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.5)))
    m = np.asarray(mask, float)[..., None] / 255
    photo = np.asarray(master, float)
    screen = np.asarray(warped, float)

    inside = m[..., 0] > 0.98
    lum = photo @ np.array([0.299, 0.587, 0.114])
    shade = np.clip(lum / lum[inside].mean(), 0.55, 1.25)
    shade = np.asarray(Image.fromarray((shade * 127).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), float) / 127
    screen = screen * (1 - SHADE_STRENGTH + SHADE_STRENGTH * shade[..., None]) * GAIN
    screen = screen * (1 - LCD_CAST) + photo[inside].mean(0) * LCD_CAST

    out = np.clip(photo * (1 - m) + screen * m, 0, 255).astype(np.uint8)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out).save(out_path, quality=JPEG_QUALITY, progressive=True, optimize=True)

    # Self-check (see the module docstring). Reads the JPEG back so it checks what ships.
    shipped_img = Image.open(out_path).convert("RGB")
    shipped = np.asarray(shipped_img, float)
    poly = Image.new("L", (width, height), 0)
    ImageDraw.Draw(poly).polygon(quad, fill=255)
    quad_px = np.asarray(poly) > 0
    dilated = np.asarray(poly.filter(ImageFilter.MaxFilter(7))) > 0
    blank_std = float(lum[quad_px].std())
    blank_chroma = float((photo.max(2) - photo.min(2))[quad_px].mean())
    rb_master = float((photo[..., 0] - photo[..., 2])[inside].mean())
    rb_out = float((shipped[..., 0] - shipped[..., 2])[inside].mean())
    spill_pct = float((np.abs(shipped - photo).max(2)[~dilated] > 24).mean() * 100)
    kb = os.path.getsize(out_path) / 1024
    checks = {
        "blank_screen": blank_std < 12 and blank_chroma < 28,
        "ui_landed": (rb_out - rb_master) > 15,
        "no_spill": spill_pct < 0.05,
        "size": kb <= MAX_KB and shipped_img.size == OUT_SIZE,
    }
    return {"out": str(out_path), "kb": round(kb, 1), "blank_std": round(blank_std, 1),
            "blank_chroma": round(blank_chroma, 1), "rb_master": round(rb_master, 1), "rb_out": round(rb_out, 1),
            "spill_pct": round(spill_pct, 4), "failed": [k for k, v in checks.items() if not v]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--masters", default="tools/photos")
    ap.add_argument("--shots", default="src/assets/shots")
    ap.add_argument("--out", default="src/assets/photos")
    a = ap.parse_args()
    results = []
    for p in PLACEMENTS:
        for lang, (shot, crop) in p["shots"].items():
            results.append(composite(Path(a.masters) / p["master"], p["quad"], Path(a.shots) / shot, crop,
                                     Path(a.out) / f"retail-{p['photo']}-{lang}.jpg"))
    for r in results:
        print(("OK   " if not r["failed"] else "FAIL ") + f"{r['out']}  {r['kb']} KB  blank std {r['blank_std']} "
              f"chroma {r['blank_chroma']}  R-B {r['rb_master']} -> {r['rb_out']}  spill {r['spill_pct']}%"
              + (f"  FAILED: {', '.join(r['failed'])}" if r["failed"] else ""))
    return 0 if all(not r["failed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
