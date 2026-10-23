import { describe, it, expect } from 'vitest';
import { worldMix, dominant } from '../../src/scripts/worlds/mix';

const S = [
  { top: 0, height: 900, world: 'master' as const },
  { top: 900, height: 900, world: 'retail' as const },
  { top: 1800, height: 900, world: 'clinic' as const },
];
const sum = (m: Record<string, number>) => Object.values(m).reduce((a, b) => a + b, 0);

describe('worldMix', () => {
  it('is fully the first world at the top', () => expect(worldMix(S, 0, 900).master).toBeCloseTo(1));
  it('is fully the section world when centred on it', () => expect(worldMix(S, 900, 900).retail).toBeCloseTo(1));
  it('is half and half midway between two centres', () => {
    const m = worldMix(S, 450, 900);
    expect(m.master).toBeCloseTo(0.5); expect(m.retail).toBeCloseTo(0.5);
  });
  it('always sums to 1 and is never negative, at any scroll including deep loads', () => {
    for (let y = -500; y <= 5000; y += 37) {
      const m = worldMix(S, y, 900);
      expect(sum(m)).toBeCloseTo(1);
      for (const v of Object.values(m)) expect(v).toBeGreaterThanOrEqual(0);
    }
  });
  it('past the last centre stays on the last world', () => expect(worldMix(S, 4000, 900).clinic).toBeCloseTo(1));
  it('dominant picks the largest weight', () => expect(dominant(worldMix(S, 1700, 900))).toBe('clinic'));
  it('handles no sections as master', () => expect(worldMix([], 0, 900).master).toBe(1));

  // E1: the hand-over is a short band, not a section-long blend. Replaces the old reading of 'half and half midway'
  // (which was only the single midpoint of a smoothstep over the whole 900px gap). What it can no longer catch: nothing
  // that the new tests below do not; what it newly catches: a return to a long muddy blend.
  it('a section shows its own world pure everywhere outside the band (no mid-tones)', () => {
    const vh = 900, band = 0.22 * vh;
    // master centre 450, retail centre 1350, mid 900; scrollY = c - vh/2
    for (let y = 0; y <= 900; y += 10) {
      const c = y + vh / 2, m = worldMix(S, y, vh);
      if (c < 900 - band / 2) expect(m.master, `c=${c}`).toBeCloseTo(1, 6);
      else if (c > 900 + band / 2) expect(m.retail, `c=${c}`).toBeCloseTo(1, 6);
    }
  });
  it('the blended stretch is at most 22% of the viewport height of scroll', () => {
    const vh = 900;
    let blended = 0;
    for (let y = 0; y <= 1800; y += 1) {
      const m = worldMix(S, y, vh);
      if (m.master > 0.001 && m.retail > 0.001) blended++;
      if (m.retail > 0.001 && m.clinic > 0.001) blended++;
    }
    // two boundaries, each at most one band
    expect(blended).toBeLessThanOrEqual(2 * Math.ceil(0.22 * vh));
  });
  it('the band scales with the viewport height, not the section height', () => {
    const tall = [{ top: 0, height: 3000, world: 'master' as const }, { top: 3000, height: 3000, world: 'retail' as const }];
    let blended = 0;
    for (let y = 0; y <= 6000; y += 1) { const m = worldMix(tall, y, 900); if (m.master > 0.001 && m.retail > 0.001) blended++; }
    expect(blended).toBeLessThanOrEqual(Math.ceil(0.22 * 900));
    expect(blended).toBeGreaterThan(100);
  });
});
