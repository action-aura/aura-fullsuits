// alpha: how visible the mark is at the station, 0..1; absent means 1. Used where a section's content spans the
// full width (the product day rail and features grid): no station inside the clear-space band clears them, so the
// mark dissolves there and condenses back for the next copy section. The world glow stays at the station.
export type Station = { x: number; y: number; scale: number; alpha?: number };

// Values from docs/design-system.md section 5 (Home row). x/y are viewport
// fractions of the mark's centre; scale 1 = 58% of viewport height.
export const HOME_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1.0 },  // hero
  { x: 0.66, y: 0.52, scale: 1.14 }, // company
  { x: 0.27, y: 0.54, scale: 0.9 },  // retail (copy flipped right)
  { x: 0.73, y: 0.54, scale: 0.9 },  // clinic
  { x: 0.27, y: 0.54, scale: 0.9 },  // gym (copy flipped right)
  { x: 0.73, y: 0.58, scale: 0.76 }, // close
];
export const HOME_PHONE: Station[] = [
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 },
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 0.9 },
];

// One station per [data-w] section of ProductPage.astro, in DOM order: hero, day rail, features, details, demo CTA.
// stationAt caps at the number of section centres, so an extra entry is unreachable: a sixth entry once made the .9
// CTA station dead and the mark stayed at .7 through the CTA (measured 2026-10-18).
export const PRODUCT_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1 },            // hero
  { x: 0.75, y: 0.5, scale: 0.7, alpha: 0 }, // day rail: full-width cards, the mark dissolves
  { x: 0.75, y: 0.5, scale: 0.7, alpha: 0 }, // features grid: full-width cards, stays dissolved
  { x: 0.75, y: 0.5, scale: 0.7 },           // details: condenses back beside the copy
  { x: 0.73, y: 0.56, scale: 0.9 },          // demo CTA
];
export const PRODUCT_PHONE: Station[] = [
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 0 }, { x: 0.5, y: 0.5, scale: 0 },
  { x: 0.5, y: 0.5, scale: 0 }, { x: 0.5, y: 0.5, scale: 0 },
];
export const ABOUT_DESKTOP: Station[] = [
  { x: 0.5, y: 0.36, scale: 1.2 },
  { x: 0.75, y: 0.52, scale: 0.8 },
  { x: 0.75, y: 0.52, scale: 0.8 },
  { x: 0.75, y: 0.52, scale: 0.8 },
];
export const ABOUT_PHONE: Station[] = [
  { x: 0.5, y: 0.4, scale: 1 }, { x: 0.5, y: 0.4, scale: 1 },
  { x: 0.5, y: 0.4, scale: 1 }, { x: 0.5, y: 0.4, scale: 1 },
];
export const CONTACT_DESKTOP: Station[] = [{ x: 0.75, y: 0.62, scale: 0.9 }];
export const CONTACT_PHONE: Station[] = [{ x: 0.5, y: 0.15, scale: 0.4 }];

export type PageKind = 'home' | 'product' | 'about' | 'contact' | 'read';

export function stationsFor(kind: PageKind, phone: boolean): Station[] | 'none' {
  if (kind === 'read') return 'none';
  if (kind === 'home') return phone ? HOME_PHONE : HOME_DESKTOP;
  if (kind === 'product') return phone ? PRODUCT_PHONE : PRODUCT_DESKTOP;
  if (kind === 'about') return phone ? ABOUT_PHONE : ABOUT_DESKTOP;
  return phone ? CONTACT_PHONE : CONTACT_DESKTOP;
}

export const mirror = (s: Station): Station => ({ ...s, x: 1 - s.x });
const smooth = (t: number) => t * t * (3 - 2 * t);
// A dissolve runs inside the middle 30% of the gap between two section centres (scroll fraction .4 to .7): the mark
// holds, then fades, then is gone, instead of sitting half-faded behind cards for the whole glide. Symmetric on the
// way back. Takes the raw scroll fraction, not the smoothed one.
const alphaStep = (u: number) => smooth(Math.min(1, Math.max(0, (u - 0.4) / 0.3)));

export function stationAt(stations: Station[], centres: number[], c: number, rtl: boolean): Station {
  const pick = (s: Station) => (rtl ? mirror(s) : s);
  if (c <= centres[0]) return pick(stations[0]);
  const last = Math.min(stations.length, centres.length) - 1;
  if (c >= centres[last]) return pick(stations[last]);
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const u = (c - centres[i]) / (centres[i + 1] - centres[i]);
      const t = smooth(u);
      const a = stations[i], b = stations[i + 1];
      const out: Station = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, scale: a.scale + (b.scale - a.scale) * t };
      // alpha is emitted only when a neighbour defines it, so tables without alpha still compare equal to their stations.
      if (a.alpha !== undefined || b.alpha !== undefined) {
        const aa = a.alpha ?? 1, ba = b.alpha ?? 1;
        out.alpha = aa + (ba - aa) * alphaStep(u);
      }
      return pick(out);
    }
  }
  return pick(stations[last]);
}

// The mark is sized from viewport height; on narrow-but-tall desktops that makes it
// too wide for its column and the ring runs into the copy. Shrink it in proportion
// once the aspect ratio falls below 1.6 (1440x900). Never grows the mark.
export function fitScale(scale: number, width: number, height: number): number {
  const k = Math.min(1, (width / Math.max(1, height)) / 1.6);
  return scale * k;
}

// The footer spans the full width, so the mark dissolves as it rises (design-system dissolve rule): it
// ran behind the footer link columns at the CTA/close station (axe colour-contrast, 2026-10-18).
// 0 while the footer's top is below 82% of the viewport (the bottom of the mark at the CTA station: centre .56,
// half of 58vh * .9), 1 once it reaches 62%, where the first footer links meet the mark. Starting earlier dimmed
// the mark beside the centred CTA copy (measured --jhide .126), which the CTA station must show in full.
export function footerFade(footerTop: number, viewportHeight: number): number {
  const start = viewportHeight * 0.82, end = viewportHeight * 0.62;
  if (footerTop >= start) return 0;
  if (footerTop <= end) return 1;
  return (start - footerTop) / (start - end);
}
