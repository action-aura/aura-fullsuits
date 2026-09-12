"""Measure the Sign In button's real contrast from a device screenshot.

sellability-status.md ("Android Sign In contrast") left one unknown: the
declared pair is #0D1B2E text on #6EA8FF fill (7.18:1), but an earlier
device rendered the old fill at ~72% of its declared value. Sample the fill
and the darkest glyph pixel from the phone's own screenshot and compute the
WCAG ratio -- measured, not assumed.
"""
import sys

from PIL import Image

PATH = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\MSI\AppData\Local\Temp\phone_watcher.png"
img = Image.open(PATH).convert("RGB")
w, h = img.size
print("image", w, "x", h)

# Button spans roughly x 130..950, y 1565..1710 on the 1080x2340 screen; the
# label sits in the middle third. Fill sample: a patch left of the label.
fill_box = (200, 1610, 300, 1660)
text_box = (440, 1600, 640, 1675)


def median_rgb(box):
    px = list(img.crop(box).getdata())
    px.sort(key=lambda p: sum(p))
    return px[len(px) // 2]


def darkest_rgb(box):
    px = list(img.crop(box).getdata())
    return min(px, key=lambda p: 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2])


def rel_lum(rgb):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = rel_lum(a), rel_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


fill = median_rgb(fill_box)
glyph = darkest_rgb(text_box)
declared_fill = (0x6E, 0xA8, 0xFF)
declared_text = (0x0D, 0x1B, 0x2E)
print("sampled fill  #%02X%02X%02X" % fill, "(declared #6EA8FF)")
print("darkest glyph #%02X%02X%02X" % glyph, "(declared #0D1B2E)")
print("fill brightness vs declared: %.0f%%" % (100 * sum(fill) / sum(declared_fill)))
print("measured contrast: %.2f:1" % contrast(fill, glyph))
print("declared contrast: %.2f:1" % contrast(declared_fill, declared_text))
print("WCAG AA normal text needs 4.5:1; large text 3.0:1")
