# Action Aura — Brand Kit v2

The applied identity system: master brand + three product sub-brands, dark-first. Upload this file (with the SVGs) to any Claude chat or Project to continue brand work with full context, or hand it to a developer as the spec.

Full design canvas (all rounds, construction grid, rationale, banners, hero): https://claude.ai/artifact/4kX5xFHK7VjSqjwisRjobj

## Brand

- **Name:** Action Aura (never "Action Aura Lite")
- **Tagline:** Go Bigger · Go Smarter
- **Positioning:** Next-gen enterprise systems — multi-domain platform with shared departments and real-time analytics
- **Primary presentation context: dark surfaces** (`#101A30` cards on `#0E1729`/`#0B1322` fields). Light versions exist for print and light UIs.

## The mark ("pierced A")

A faceted **A** read through the Greek **delta** (change), pierced by a tilted orbit — the aura. The ring passes behind the letter's shoulders, shows through the counter, and sweeps across the front, ending in a bright energy node that sits exactly on the ring's seam.

**Construction (120-unit canvas):** 45° apex · apex overshoots the cap line by 1.5% (optical correction) · orbit centered on the golden section of the height (60, 69) · ellipse 36×8 (4.5:1) rotated −12° · node at (95.2, 61.5) · lit ridge 1.3u at x=60.

## Files in this kit

| File | Use |
|---|---|
| `aura-mark-dark.svg` | Master, dark surfaces — the primary logo |
| `aura-mark-light.svg` | Master, light surfaces / print |
| `aura-mark-flat-dark.svg` | Master favicon/small (≤64px) on dark — ivory A + teal ring |
| `aura-mark-flat-light.svg` | Master favicon/small on light — navy A + teal ring |
| `aura-retail-dark.svg` / `-light.svg` | Aura Retail primary, per surface |
| `aura-clinic-dark.svg` / `-light.svg` | Aura Clinic primary, per surface |
| `aura-owner-dark.svg` / `-light.svg` | Aura Owner primary, per surface |

**App icons:** use the primary SVG and delete the two glow `<circle>` elements and the shadow `<ellipse>` (first three elements) — that is the official app-icon variant.

## Color

| Role | Hex |
|---|---|
| Navy (trust; shadow facet on light surfaces) | `#16233D` |
| Lifted navy (shadow facet on **dark** surfaces, so the letter doesn't sink) | `#22304A` |
| Teal (master accent) | `#2F7B7B` · light `#7FCAC5` / `#8FD0D0` |
| Ivory | `#F6F4EF` |
| Dark surfaces | `#101A30` on `#0E1729` |

### Products
| Product | Dark | Mid | Light | Counter glyph | Glyph color |
|---|---|---|---|---|---|
| **Aura Retail** — shops & showrooms (POS, inventory, purchasing, invoicing) | `#A06030` | `#C97B3D` | `#F0B87A` | Square (module on the shelf) | light on dark, mid on light |
| **Aura Clinic** — clinics & practices (appointments, records, treatment, billing) | `#33699C` | `#3D7FB5` | `#8CC7EE` | Plus (care inside the A) | light on dark, mid on light |
| **Aura Owner** — owner's console (branches, KPIs, alerts) | `#96761F` | `#C9A227` | `#E8CD7A` | Diamond (command tier) | light on dark, mid on light |

Owner wordmark text on **light** backgrounds uses deeper gold `#A8841C` for contrast.

## Family rule

One geometry for every product. A product owns exactly two things: its **accent trio** and its **counter glyph**. The shadow facet always falls to navy (`#16233D` on light, `#22304A` on dark) — the shared constant that makes the marks read as siblings. Below ~32px (favicons) the glyph cannot survive; products differentiate by **ring color only**, which is expected at that size.

## Typography & wordmarks

- **Space Grotesk 700** — master: "Action" ivory/navy + "Aura" teal; products: "Aura" ivory/navy + product name in its light accent (dark surfaces) or mid/deep accent (light surfaces)
- **Work Sans** — supporting text
- Tagline set in Space Grotesk 500, wide tracking, with a teal dot separator: Go Bigger **·** Go Smarter

## Usage

- Clear space: 25% of mark height on all sides · minimum 64px for 3D versions, flat below that
- Staging effects (soft glow, ground shadow) are baked into the primary SVGs for hero/branding use; strip them (see App icons note) for UI chrome
- Gradients don't survive single-color print — the flat versions are the mono fallback
