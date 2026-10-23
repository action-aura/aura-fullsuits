import { crisp } from '../worlds/mix';

// alpha: how visible the mark is at the station, 0..1; absent means 1. Used where a section's content spans the
// full width (the product day rail and features grid): no station inside the clear-space band clears them, so the
// mark dissolves there and condenses back for the next copy section. The world glow stays at the station.
export type Station = { x: number; y: number; scale: number; alpha?: number };

// Values from docs/design-system.md section 5 (Home row). x/y are viewport
// fractions of the mark's centre; scale 1 = 58% of viewport height.
// E1: the hero keeps the big mark; every chapter after it is the same small mark (about 60%) on the side opposite its
// copy. It used to be 0.9 to 1.14, which left the ring touching the copy column on common laptop sizes.
export const CHAPTER_SCALE = 0.6;
export const HOME_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1.0 },            // hero
  { x: 0.73, y: 0.52, scale: 0.62 },           // company (copy left)
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE },  // retail (copy flipped right)
  // E2: the retail chapter now holds three playable full-width demos (till, offline switch, what-it-does). No station
  // inside the clear-space band clears a full-width card, so the mark dissolves for all of them (alpha 0) and condenses
  // back at the clinic. Two zero-height "gate" stations, one before the demos and one after, place the dissolve where
  // it is needed: a dissolve runs half-way between two section CENTRES, and a demo's centre is far below its top edge,
  // so without the first gate the mark was still at 19% while the till rose into view (measured 2026-10-23); without the
  // second it came back while the pinned what-it-does stage was still scrolling off.
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // retail: gate (zero height, right after the chapter)
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // retail: live till
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // retail: offline switch
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // retail: what it does (pinned)
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // retail: tail gate (after the pinned stage)
  { x: 0.73, y: 0.54, scale: CHAPTER_SCALE },  // clinic
  // E3: the clinic and the gym each get a full-width preview card after their copy, so each gets the same treatment
  // as the retail demos: a zero-height gate before it, the card (alpha 0) and a zero-height gate after it.
  { x: 0.73, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // clinic: gate
  { x: 0.73, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // clinic: booking preview
  { x: 0.73, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // clinic: tail gate
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE },  // gym (copy flipped right)
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // gym: gate
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // gym: check-in preview
  { x: 0.27, y: 0.54, scale: CHAPTER_SCALE, alpha: 0 },  // gym: tail gate
  { x: 0.73, y: 0.56, scale: CHAPTER_SCALE },  // close
];
// Phones: the mark lives in the hero only. Every entry is the same pose, so nothing glides or shrinks behind a
// chapter's text; the Jewel script places the pose under the hero copy (phoneHeroPose) and fades it out as the hero
// leaves (phoneHeroFade). x is ignored on phones (centred).
const PHONE_POSE: Station = { x: 0.5, y: 0.62, scale: 0.6 };
export const HOME_PHONE: Station[] = Array.from({ length: 17 }, () => ({ ...PHONE_POSE }));

// One station per [data-w] section of ProductPage.astro, in DOM order: hero, day rail, features, details, demo CTA.
// stationAt caps at the number of section centres, so an extra entry is unreachable: a sixth entry once made the .9
// CTA station dead and the mark stayed at .7 through the CTA (measured 2026-10-18).
export const PRODUCT_DESKTOP: Station[] = [
  { x: 0.73, y: 0.54, scale: 1 },                       // hero
  { x: 0.75, y: 0.5, scale: CHAPTER_SCALE, alpha: 0 },  // day rail: full-width cards, the mark dissolves
  { x: 0.75, y: 0.5, scale: CHAPTER_SCALE, alpha: 0 },  // features grid: full-width cards, stays dissolved
  { x: 0.75, y: 0.5, scale: CHAPTER_SCALE },            // details: condenses back beside the copy
  { x: 0.73, y: 0.56, scale: 0.62 },                    // demo CTA
];
export const PRODUCT_PHONE: Station[] = Array.from({ length: 5 }, () => ({ ...PHONE_POSE }));
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

// The glide between two stations (and a dissolve's alpha) runs inside the same short band as the world colour change
// (worlds/mix.ts crisp): half-way between the two section centres, where both sections' copy is off-screen, so the
// mark never travels across text. It used to glide over the whole gap between centres. viewportH sets the band's size.
export function stationAt(stations: Station[], centres: number[], c: number, rtl: boolean, viewportH: number): Station {
  const pick = (s: Station) => (rtl ? mirror(s) : s);
  if (c <= centres[0]) return pick(stations[0]);
  const last = Math.min(stations.length, centres.length) - 1;
  if (c >= centres[last]) return pick(stations[last]);
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const t = crisp(c, centres[i], centres[i + 1], viewportH);
      const a = stations[i], b = stations[i + 1];
      const out: Station = { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t, scale: a.scale + (b.scale - a.scale) * t };
      // alpha is emitted only when a neighbour defines it, so tables without alpha still compare equal to their stations.
      if (a.alpha !== undefined || b.alpha !== undefined) {
        const aa = a.alpha ?? 1, ba = b.alpha ?? 1;
        out.alpha = aa + (ba - aa) * t;
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

// Phone hero: the whole mark must show, under the buttons and not behind them. The still frame is 152.4vw * scale wide
// (components.css), apex 9.72% below the frame top; from the apex down the frame is about 1.27 * viewport width * scale
// tall (mark plus ground shadow). So: apex 16px under the copy, then the biggest scale that still ends above the bottom
// edge. Returns --jy (the apex as a viewport fraction) and --js. Clamped so a very short screen shrinks the mark
// rather than hiding it, and a tall one never exceeds .8 (the old phone mark was 1.0 and swamped the copy).
export const PHONE_MARK_MIN = 0.3, PHONE_MARK_MAX = 0.8;
export function phoneHeroPose(copyBottom: number, vw: number, vh: number): { y: number; scale: number } {
  const apex = Math.min(vh * 0.9, copyBottom + 16);
  const scale = Math.min(PHONE_MARK_MAX, Math.max(PHONE_MARK_MIN, (vh - apex - 8) / (1.27 * vw)));
  return { y: apex / vh, scale };
}

// 0 while the hero still fills the viewport, 1 once 30% of the viewport has scrolled off its bottom. The first chapter's
// copy starts about 135px under the hero's bottom edge and reveals when it is within 12% of the viewport bottom, so
// the mark is gone before any chapter text can reach it.
export function phoneHeroFade(heroBottom: number, viewportH: number): number {
  return Math.min(1, Math.max(0, (viewportH - heroBottom) / (viewportH * 0.3)));
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
