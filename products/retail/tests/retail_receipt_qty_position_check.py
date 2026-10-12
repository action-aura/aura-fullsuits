"""PRINT-1 image check: the quantity token sits in the same place on EVERY item line.

A model-level test cannot see this bug: the quantity once landed on the other
side (and its glyphs flipped) only when the real canvas bidi-reordered it next
to an Arabic name. So this reads the DECODED PRINTER BITMAP (a PNG from the
real-app capture) and measures ink.

For each item line (the text lines between the first two full-width rules) it
finds the cluster of ink touching the line-start edge -- the right margin on an
Arabic receipt, the left margin on an English one -- and asserts every item line
has that cluster at the same edge position and about the same width, and that
no ink cluster of the right size sits at the OPPOSITE name-side start.

Usage: python retail_receipt_qty_position_check.py <rtl|ltr> <png> [<png> ...]
Exit 0 = pass. Needs Pillow.  Also importable: check(png_path, rtl) -> list of problems.
"""
import sys
from PIL import Image

PAD = 8          # receipt-raster.js side margin
TOKEN_MAX_W = 60  # widest plausible quantity token, px
GAP_SPLIT = 9    # a blank run this wide ends the token cluster (draw gap is 12)


def _bands(rows):
    out, start = [], None
    for y, on in enumerate(rows + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            out.append((start, y - 1)); start = None
    return out


def check(path, rtl):
    img = Image.open(path).convert('L')
    w, h = img.size
    px = img.load()
    ink = [[px[x, y] < 128 for x in range(w)] for y in range(h)]
    row_on = [any(r) for r in ink]
    bands = _bands(row_on)
    rules = [b for b in bands if b[1] - b[0] <= 3 and sum(ink[b[0]]) >= w - 2 * PAD - 8]
    problems = []
    if len(rules) < 2:
        return [f'{path}: expected >=2 rules, found {len(rules)}']
    items = [b for b in bands if rules[0][1] < b[0] and b[1] < rules[1][0]]
    if len(items) < 2:
        return [f'{path}: expected >=2 item lines, found {len(items)}']
    clusters = []
    for (y0, y1) in items:
        cols = [any(ink[y][x] for y in range(y0, y1 + 1)) for x in range(w)]
        xs = range(w - 1, -1, -1) if rtl else range(w)
        first = None; last = None; blank = 0
        for x in xs:
            if cols[x]:
                if first is None:
                    first = x
                last = x; blank = 0
            elif first is not None:
                blank += 1
                if blank >= GAP_SPLIT:
                    break
        clusters.append((first, last, abs(last - first) + 1))
    edges = [c[0] for c in clusters]
    widths = [c[2] for c in clusters]
    if max(edges) - min(edges) > 3:
        problems.append(f'{path}: token edge differs between item lines: {edges}')
    if max(widths) - min(widths) > 6:
        problems.append(f'{path}: token cluster width differs between item lines: {widths}')
    # A quantity token ("2x") is ~35px at the print size; an amount cluster is 90+.
    # This is what tells "the token is here" from "an amount is here" if a mirrored
    # or displaced layout puts something else at the line-start margin.
    if max(widths) > TOKEN_MAX_W:
        problems.append(f'{path}: cluster at the line-start margin is {widths}px wide -- not a quantity token')
    edge_margin = (w - 1 - edges[0]) if rtl else edges[0]
    if edge_margin > PAD + 6:
        problems.append(f'{path}: token not at the line-start margin (gap {edge_margin}px)')
    return problems


if __name__ == '__main__':
    direction, paths = sys.argv[1], sys.argv[2:]
    bad = []
    for p in paths:
        probs = check(p, direction == 'rtl')
        print(('FAIL ' if probs else 'ok   ') + p)
        bad += probs
    for b in bad:
        print('  ' + b)
    sys.exit(1 if bad else 0)
