import { describe, it, expect } from 'vitest';
import { HOME_DESKTOP, HOME_PHONE, mirror, stationAt } from '../../src/scripts/jewel/stations';

const C = [450, 1350, 2250, 3150, 4050, 4950];

describe('stations', () => {
  it('home has six desktop and six phone stations', () => {
    expect(HOME_DESKTOP).toHaveLength(6); expect(HOME_PHONE).toHaveLength(6);
  });
  it('sits exactly on a station at its centre', () => expect(stationAt(HOME_DESKTOP, C, 2250, false)).toEqual(HOME_DESKTOP[2]));
  it('clamps before the first and after the last centre', () => {
    expect(stationAt(HOME_DESKTOP, C, -999, false)).toEqual(HOME_DESKTOP[0]);
    expect(stationAt(HOME_DESKTOP, C, 99999, false)).toEqual(HOME_DESKTOP[5]);
  });
  it('rtl mirrors x and never y or scale', () => {
    const s = stationAt(HOME_DESKTOP, C, 450, true);
    expect(s.x).toBeCloseTo(1 - HOME_DESKTOP[0].x); expect(s.y).toBe(HOME_DESKTOP[0].y); expect(s.scale).toBe(HOME_DESKTOP[0].scale);
  });
  it('midway is between both stations', () => {
    const s = stationAt(HOME_DESKTOP, C, 1800, false);
    expect(s.x).toBeLessThan(HOME_DESKTOP[1].x); expect(s.x).toBeGreaterThan(HOME_DESKTOP[2].x);
  });
  it('mirror is an involution', () => expect(mirror(mirror(HOME_DESKTOP[2]))).toEqual(HOME_DESKTOP[2]));
  it('desktop stations keep the 25% clear space horizontally', () => {
    for (const s of HOME_DESKTOP) { expect(s.x).toBeGreaterThanOrEqual(0.25); expect(s.x).toBeLessThanOrEqual(0.75); }
  });
});
