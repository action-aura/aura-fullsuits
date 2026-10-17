export type World = 'master' | 'retail' | 'clinic' | 'gym';
export const WORLDS: readonly World[] = ['master', 'retail', 'clinic', 'gym'];

const smooth = (t: number) => t * t * (3 - 2 * t);
const zero = (): Record<World, number> => ({ master: 0, retail: 0, clinic: 0, gym: 0 });

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
      const t = smooth((c - centres[i]) / (centres[i + 1] - centres[i]));
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
