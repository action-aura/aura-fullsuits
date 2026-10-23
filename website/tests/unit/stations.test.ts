import { describe, it, expect } from 'vitest';
import { fitScale, mirror, type Station } from '../../src/scripts/jewel/stations';

describe('mirror', () => {
  const S: Station = { x: 0.72, y: 0.5, scale: 1.1 };
  it('flips only x', () => expect(mirror(S)).toEqual({ x: 1 - 0.72, y: 0.5, scale: 1.1 }));
  it('is an involution', () => expect(mirror(mirror(S))).toEqual(S));
});

describe('fitScale', () => {
  it('leaves the scale alone at 1440x900 (aspect 1.6)', () => {
    expect(fitScale(1, 1440, 900)).toBe(1);
  });
  it('never grows the mark on wide screens', () => {
    expect(fitScale(1, 1920, 900)).toBe(1);
  });
  it('shrinks in proportion on 1024x900', () => {
    expect(fitScale(1.14, 1024, 900)).toBeCloseTo(1.14 * (1024 / 900 / 1.6));
  });
  it('keeps zero scale at zero', () => {
    expect(fitScale(0, 1024, 900)).toBe(0);
  });
});
