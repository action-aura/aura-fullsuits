export type World = 'master' | 'retail' | 'clinic' | 'gym';
export const WORLDS: readonly World[] = ['master', 'retail', 'clinic', 'gym'];

const smooth = (t: number) => t * t * (3 - 2 * t);
const zero = (): Record<World, number> => ({ master: 0, retail: 0, clinic: 0, gym: 0 });

// A world change is a short band, not a section-long blend. The old curve smoothed across the whole gap between two
// section centres (about 90vh of scroll), so for most of the page the screen sat on a half-and-half colour: the brown-grey
// between master and retail, the mauve between clinic and gym. Now the field holds its own colour and the hand-over
// happens inside BAND of the viewport height, centred half-way between the two centres, i.e. where both sections'
// copy is off-screen. Shared with stations.ts so the jewel steps aside inside the same band the colour changes in.
export const BAND = 0.22;

// 0 before the band, 1 after it, smooth inside. c = scroll centre, ca/cb = the two section centres, vh = viewport height.
export function crisp(c: number, ca: number, cb: number, vh: number): number {
  const band = Math.max(1, BAND * vh), mid = (ca + cb) / 2;
  return smooth(Math.min(1, Math.max(0, (c - mid) / band + 0.5)));
}

export function worldMix(
  sections: { top: number; height: number; world: World }[], scrollY: number, viewportH: number,
): Record<World, number> {
  const out = zero();
  if (sections.length === 0) { out.master = 1; return out; }
  const c = scrollY + viewportH / 2;
  const centres = sections.map((s) => s.top + s.height / 2);
  if (c <= centres[0]) { out[sections[0].world] = 1; return out; }
  const last = sections.length - 1;
  if (c >= centres[last]) { out[sections[last].world] = 1; return out; }
  for (let i = 0; i < last; i++) {
    if (c >= centres[i] && c <= centres[i + 1]) {
      const t = crisp(c, centres[i], centres[i + 1], viewportH);
      out[sections[i].world] += 1 - t;
      out[sections[i + 1].world] += t;
      return out;
    }
  }
  out.master = 1;
  return out;
}

export function dominant(mix: Record<World, number>): World {
  return WORLDS.reduce((a, b) => (mix[b] > mix[a] ? b : a), 'master');
}
