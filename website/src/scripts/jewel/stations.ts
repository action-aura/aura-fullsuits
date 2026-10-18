export type Station = { x: number; y: number; scale: number };

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

export const PRODUCT_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1 },
  { x: 0.75, y: 0.5, scale: 0.7 },
  { x: 0.75, y: 0.5, scale: 0.7 },
  { x: 0.75, y: 0.5, scale: 0.7 },
  { x: 0.75, y: 0.5, scale: 0.7 },
  { x: 0.73, y: 0.56, scale: 0.9 },
];
export const PRODUCT_PHONE: Station[] = [
  { x: 0.5, y: 0.5, scale: 1 }, { x: 0.5, y: 0.5, scale: 0 }, { x: 0.5, y: 0.5, scale: 0 },
  { x: 0.5, y: 0.5, scale: 0 }, { x: 0.5, y: 0.5, scale: 0 }, { x: 0.5, y: 0.5, scale: 0 },
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

export const mirror = (s: Station): Station => ({ x: 1 - s.x, y: s.y, scale: s.scale });
const smooth = (t: number) => t * t * (3 - 2 * t);

export function stationAt(stations: Station[], centres: number[], c: number, rtl: boolean): Station {
  const pick = (s: Station) => (rtl ? mirror(s) : s);
  if (c <= centres[0]) return pick(stations[0]);
  const last = Math.min(stations.length, centres.length) - 1;
  if (c >= centres[last]) return pick(stations[last]);
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const t = smooth((c - centres[i]) / (centres[i + 1] - centres[i]));
      const a = stations[i], b = stations[i + 1];
      return pick({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, scale: a.scale + (b.scale - a.scale) * t });
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
