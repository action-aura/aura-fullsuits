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
