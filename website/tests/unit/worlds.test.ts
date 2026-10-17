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
});
