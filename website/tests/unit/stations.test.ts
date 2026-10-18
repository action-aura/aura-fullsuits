import { describe, it, expect } from 'vitest';
import { HOME_DESKTOP, HOME_PHONE, fitScale, footerFade, mirror, stationAt, stationsFor, type Station } from '../../src/scripts/jewel/stations';

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

describe('stationsFor', () => {
  const desktop = (kind: 'home' | 'product' | 'about' | 'contact' | 'read') => {
    const r = stationsFor(kind, false);
    if (r === 'none') throw new Error(`expected stations for ${kind}`);
    return r;
  };

  it('product desktop has one station per product section: hero, day rail, features, details, demo CTA', () => {
    const s = desktop('product');
    expect(s).toHaveLength(5);
    expect(s[0]).toEqual({ x: 0.73, y: 0.54, scale: 1 });
  });

  it('product desktop dissolves the mark at the day rail and the features grid only', () => {
    expect(desktop('product').map((st) => st.alpha ?? 1)).toEqual([1, 0, 0, 1, 1]);
  });

  it('product desktop reaches the .9 demo CTA station with five section centres', () => {
    const five = [450, 1270, 2300, 3300, 4100];
    expect(stationAt(desktop('product'), five, 99999, false)).toEqual({ x: 0.73, y: 0.56, scale: 0.9 });
  });

  it('phone product has one station per product section too', () => {
    const s = stationsFor('product', true);
    if (s === 'none') throw new Error('expected phone stations for product');
    expect(s).toHaveLength(5);
  });

  it('read has no stations on desktop or phone', () => {
    expect(stationsFor('read', true)).toBe('none');
    expect(stationsFor('read', false)).toBe('none');
  });

  it('product and contact desktop x lie within [0.25, 0.75]', () => {
    for (const kind of ['product', 'contact'] as const) {
      for (const s of desktop(kind)) {
        expect(s.x).toBeGreaterThanOrEqual(0.25);
        expect(s.x).toBeLessThanOrEqual(0.75);
      }
    }
  });

  it('about desktop x lies within [0.25, 0.75] except the hero at 0.5', () => {
    const s = desktop('about');
    expect(s[0].x).toBe(0.5);
    for (const st of s.slice(1)) {
      expect(st.x).toBeGreaterThanOrEqual(0.25);
      expect(st.x).toBeLessThanOrEqual(0.75);
    }
  });

  it('phone product stations after the hero have scale 0', () => {
    const s = stationsFor('product', true);
    if (s === 'none') throw new Error('expected phone stations for product');
    expect(s[0].scale).toBeGreaterThan(0);
    for (const st of s.slice(1)) expect(st.scale).toBe(0);
  });

  it('home returns the existing HOME tables by reference', () => {
    expect(stationsFor('home', false)).toBe(HOME_DESKTOP);
    expect(stationsFor('home', true)).toBe(HOME_PHONE);
  });
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

describe('dissolve', () => {
  const A: Station = { x: 0.75, y: 0.5, scale: 0.7 };
  const B: Station = { x: 0.75, y: 0.5, scale: 0.7, alpha: 0 };
  const G = [0, 1000];
  it('holds full alpha to 40% of the gap, is half at 55%, gone from 70%', () => {
    expect(stationAt([A, B], G, 350, false).alpha).toBe(1);
    expect(stationAt([A, B], G, 550, false).alpha).toBeCloseTo(0.5, 6);
    expect(stationAt([A, B], G, 750, false).alpha).toBe(0);
    expect(stationAt([A, B], G, 1000, false).alpha).toBe(0);
  });
  it('condenses back the same way', () => {
    expect(stationAt([B, A], G, 350, false).alpha).toBe(0);
    expect(stationAt([B, A], G, 550, false).alpha).toBeCloseTo(0.5, 6);
    expect(stationAt([B, A], G, 750, false).alpha).toBe(1);
  });
  it('never emits alpha for stations that do not define it', () => {
    expect('alpha' in stationAt(HOME_DESKTOP, C, 1800, false)).toBe(false);
  });
  it('mirror keeps alpha and flips only x', () => {
    expect(mirror(B)).toEqual({ x: 0.25, y: 0.5, scale: 0.7, alpha: 0 });
  });
});

describe('footerFade', () => {
  it('is 0 while the footer top is at or below 82% of the viewport', () => {
    expect(footerFade(900, 900)).toBe(0);
    expect(footerFade(738, 900)).toBe(0);
  });
  it('is 1 once the footer top reaches 62% of the viewport', () => {
    expect(footerFade(558, 900)).toBe(1);
    expect(footerFade(0, 900)).toBe(1);
  });
  it('is linear between 82% and 62%', () => {
    expect(footerFade(648, 900)).toBeCloseTo(0.5, 6);
  });
});
