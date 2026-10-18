"""WCAG contrast of text colour vs. the pixels behind it.

Usage: py -3.14 tools/contrast.py shots

For every <name>.json (written by tests/site/shots.spec.ts) load <name>-bg.png (page with
main/nav/footer hidden) and measure each item's computed text colour, alpha-composited over
each pixel, against that pixel. Exit 1 if any min < 4.5.
"""
import json
import pathlib
import sys

from PIL import Image

THRESHOLD = 4.5


def lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(l1, l2):
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def parse_color(css):
    parts = css.replace("rgba(", "").replace("rgb(", "").rstrip(")").split(",")
    rgb = [float(v) for v in parts[:3]]
    alpha = float(parts[3]) if len(parts) > 3 else 1.0
    return rgb, alpha


def measure(im, box, color):
    x0 = max(0, int(box["x"]))
    y0 = max(0, int(box["y"]))
    x1 = min(im.width, int(box["x"] + box["width"] + 0.999))
    y1 = min(im.height, int(box["y"] + box["height"] + 0.999))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    px = list(im.crop((x0, y0, x1, y1)).getdata())
    rgb, alpha = parse_color(color)
    cs = []
    for p in px[:: max(1, len(px) // 20000)]:
        # translucent text is composited over the pixel behind it before measuring
        eff = [alpha * c + (1 - alpha) * q for c, q in zip(rgb, p[:3])]
        cs.append(contrast(lum(eff), lum(p)))
    cs.sort()
    return round(cs[0], 2), round(cs[len(cs) // 50], 2)


def main():
    if len(sys.argv) != 2:
        print("usage: contrast.py <shots-dir>")
        return 2
    root = pathlib.Path(sys.argv[1])
    jsons = sorted(root.glob("*.json"))
    if not jsons:
        print(f"no *.json in {root}")
        return 2
    failed = 0
    for jp in jsons:
        bg = jp.with_name(jp.stem + "-bg.png")
        if not bg.exists():
            print(f"LOW  {jp.stem}: missing {bg.name}")
            failed += 1
            continue
        im = Image.open(bg).convert("RGB")
        for n, item in enumerate(json.loads(jp.read_text(encoding="utf-8")).get("items", [])):
            b = item["box"]
            if b["width"] < 1 or b["height"] < 1:
                continue
            r = measure(im, b, item["color"])
            if r is None:
                continue
            mn, p2 = r
            flag = "OK " if mn >= THRESHOLD else "LOW"
            if mn < THRESHOLD:
                failed += 1
            print(f"{flag} {jp.stem} #{n} {item['kind']:<7} min {mn:>6}  p2 {p2:>6}  text {item['color']}")
    print(f"\n{failed} failing item(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
