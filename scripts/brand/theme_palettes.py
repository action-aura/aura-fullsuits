"""Design + verify the three new Aura themes as token sets, with WCAG contrast
computed for the pairs the desktop contrast test enforces. Prints the worst
pairs per theme and the CSS blocks ready to paste."""
import colorsys

def lum(hexs):
    r, g, b = [int(hexs.lstrip('#')[i:i+2], 16) / 255 for i in (0, 2, 4)]
    def lin(c): return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

def cr(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

SURFACES = ["surface-app", "surface-till", "surface-panel", "surface-sunken", "surface-raised", "surface-hover", "surface-active", "surface-accent-soft"]
TEXTS = ["text-primary", "text-secondary", "text-tertiary", "text-money", "text-money-positive", "text-money-negative",
         "state-success-text", "state-warning-text", "state-danger-text", "state-info-text", "accent-action"]

THEMES = {
    # NIGHT -- deep ink-navy ground, luminous aurora-teal accent. Not pure black
    # (halation on cheap panels), elevation lightens like the calm dark set.
    "night": {
        "surface-app": "#070b12", "surface-sunken": "#04070c", "surface-panel": "#0b111b",
        "surface-raised": "#0e1520", "surface-till": "#111a27", "surface-hover": "#172233", "surface-active": "#1d2b3f",
        "surface-accent-soft": "#0f2a30",
        "text-primary": "#e9f1fb", "text-secondary": "#bfcbdb", "text-tertiary": "#9aaabd",
        "text-on-accent": "#04201d", "text-money": "#e9f1fb", "text-money-positive": "#8fe8bd", "text-money-negative": "#ffb0a6",
        "state-success-text": "#7fdfa9", "state-warning-text": "#e9c97e", "state-danger-text": "#ff9b92", "state-info-text": "#8fbaff",
        "state-success-surface": "#0f2d1f", "state-warning-surface": "#30250d", "state-danger-surface": "#3b1512", "state-info-surface": "#12223c",
        "state-success-border": "#1c4a33", "state-warning-border": "#544119", "state-danger-border": "#63241e", "state-info-border": "#233f6a",
        "accent-action": "#5fe3d0", "accent-action-hover": "#7cebdb", "accent-action-active": "#49d2bf",
        "border-hairline": "#1a2432", "border-default": "#26344a", "border-strong": "#3f5271",
        "focus-ring-color": "#5fe3d0",
    },
    # DUSK -- violet-charcoal ground, lavender accent. Same elevation logic.
    "dusk": {
        "surface-app": "#13111c", "surface-sunken": "#0e0c16", "surface-panel": "#191626",
        "surface-raised": "#1c192a", "surface-till": "#211d31", "surface-hover": "#29253d", "surface-active": "#312c49",
        "surface-accent-soft": "#2a2350",
        "text-primary": "#f0edf9", "text-secondary": "#c9c3dc", "text-tertiary": "#a49dbd",
        "text-on-accent": "#150f2e", "text-money": "#f0edf9", "text-money-positive": "#95e6b6", "text-money-negative": "#ffb0a6",
        "state-success-text": "#86dfa8", "state-warning-text": "#ebc97f", "state-danger-text": "#ff9d94", "state-info-text": "#9dbcff",
        "state-success-surface": "#132d22", "state-warning-surface": "#332711", "state-danger-surface": "#3e1717", "state-info-surface": "#16233f",
        "state-success-border": "#1f4a36", "state-warning-border": "#57431c", "state-danger-border": "#672622", "state-info-border": "#27406d",
        "accent-action": "#b9a6ff", "accent-action-hover": "#c9baff", "accent-action-active": "#a793f6",
        "border-hairline": "#26223a", "border-default": "#34304c", "border-strong": "#4d4870",
        "focus-ring-color": "#b9a6ff",
    },
    # SAND -- warm paper light theme, amber-brown ink accent that takes white.
    "sand": {
        "surface-app": "#efe8dc", "surface-sunken": "#f4eee4", "surface-panel": "#fbf7f0",
        "surface-raised": "#faf6ee", "surface-till": "#fffdf8", "surface-hover": "#f1eadf", "surface-active": "#e8e0d2",
        "surface-accent-soft": "#f6e7d3",
        "text-primary": "#2a2119", "text-secondary": "#4d4034", "text-tertiary": "#63564a",
        "text-on-accent": "#ffffff", "text-money": "#2a2119", "text-money-positive": "#1d5a34", "text-money-negative": "#9a1f14",
        "state-success-text": "#1d5a34", "state-warning-text": "#6e4300", "state-danger-text": "#9a1f14", "state-info-text": "#1c4a9e",
        "state-success-surface": "#e5f1e6", "state-warning-surface": "#f8edd6", "state-danger-surface": "#f9e6e1", "state-info-surface": "#e6ecf7",
        "state-success-border": "#b3d6bb", "state-warning-border": "#e3c896", "state-danger-border": "#efc0b8", "state-info-border": "#bccbe8",
        "accent-action": "#9a4f12", "accent-action-hover": "#84420d", "accent-action-active": "#6d3609",
        "border-hairline": "#e4dccf", "border-default": "#d4cabb", "border-strong": "#b7ab99",
        "focus-ring-color": "#9a4f12",
    },
}

STATE_PAIRS = [("state-success-text", "state-success-surface"), ("state-warning-text", "state-warning-surface"),
               ("state-danger-text", "state-danger-surface"), ("state-info-text", "state-info-surface")]

ORDER = ["surface-app", "surface-till", "surface-panel", "surface-sunken", "surface-raised", "surface-hover", "surface-active",
         "text-primary", "text-secondary", "text-tertiary", "text-on-accent", "text-money", "text-money-positive", "text-money-negative",
         "state-success-text", "state-warning-text", "state-danger-text", "state-info-text",
         "state-success-surface", "state-warning-surface", "state-danger-surface", "state-info-surface",
         "state-success-border", "state-warning-border", "state-danger-border", "state-info-border",
         "accent-action", "accent-action-hover", "accent-action-active", "surface-accent-soft",
         "border-hairline", "border-default", "border-strong", "focus-ring-color"]
SCRIMS = {
    "night": ('rgba(1, 4, 9, 0.74)', 'rgba(0, 0, 0, 0.62)'),
    "dusk":  ('rgba(6, 4, 14, 0.74)', 'rgba(0, 0, 0, 0.62)'),
    "sand":  ('rgba(40, 30, 18, 0.45)', 'rgba(40, 30, 18, 0.40)'),
}
DARKISH = {"night": True, "dusk": True, "sand": False}
import sys
if len(sys.argv) > 1 and sys.argv[1] == "emit":
    for name, t in THEMES.items():
        print(f'\n/* [design-tokens-{name}:begin] */\nhtml[data-theme="{name}"] {{')
        for k in ORDER:
            print(f"  --{k}: {t[k]};")
        scrim, sheet = SCRIMS[name]
        print(f"  --surface-scrim: {scrim};")
        if DARKISH[name]:
            print("  --focus-ring-halo: #000000;")
            print("  --elevation-card:  0 1px 2px rgba(0, 0, 0, 0.55), 0 4px 12px rgba(0, 0, 0, 0.45);")
            print("  --elevation-panel: 0 8px 28px rgba(0, 0, 0, 0.55);")
            print("  --elevation-modal: 0 24px 64px rgba(0, 0, 0, 0.70);")
        else:
            print("  --focus-ring-halo: #ffffff;")
            print("  --elevation-card:  0 1px 2px rgba(60, 45, 25, 0.10), 0 4px 12px rgba(60, 45, 25, 0.08);")
            print("  --elevation-panel: 0 8px 28px rgba(60, 45, 25, 0.14);")
            print("  --elevation-modal: 0 24px 64px rgba(60, 45, 25, 0.22);")
        print(f"  --sheet-scrim: {sheet};")
        r, g, b = [int(t['accent-action'].lstrip('#')[i:i+2], 16) for i in (0, 2, 4)]
        print(f"  --sub-accent-rgb: {r}, {g}, {b};")
        print(f"}}\n/* [design-tokens-{name}:end] */")
    sys.exit(0)

for name, t in THEMES.items():
    worst = []
    for tx in TEXTS:
        for sf in SURFACES:
            worst.append((cr(t[tx], t[sf]), tx, sf))
    for tx, sf in STATE_PAIRS:
        worst.append((cr(t[tx], t[sf]), tx, sf))
    for acc in ("accent-action", "accent-action-hover", "accent-action-active"):
        worst.append((cr(t["text-on-accent"], t[acc]), "text-on-accent", acc))
    worst.sort()
    fails = [w for w in worst if w[0] < 4.5]
    prim = min(cr(t["text-primary"], t[sf]) for sf in SURFACES)
    print(f"== {name}: {len(fails)} pairs under 4.5 | primary text worst {prim:.2f} | worst 6:")
    for c, a, b in worst[:6]:
        print(f"   {c:5.2f}  {a} on {b}")
