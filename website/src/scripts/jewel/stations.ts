// The About page's jewel is pinned (no scroll choreography any more), so all that is left of the old station system is
// the mirror for RTL and the fit for narrow-but-tall desktops. x/y are viewport fractions of the mark's centre;
// scale 1 = 58% of viewport height.
export type Station = { x: number; y: number; scale: number };

export const mirror = (s: Station): Station => ({ ...s, x: 1 - s.x });

// The mark is sized from viewport height; on narrow-but-tall desktops that makes it
// too wide for its column and the ring runs into the copy. Shrink it in proportion
// once the aspect ratio falls below 1.6 (1440x900). Never grows the mark.
export function fitScale(scale: number, width: number, height: number): number {
  const k = Math.min(1, (width / Math.max(1, height)) / 1.6);
  return scale * k;
}
