import { describe, it, expect } from 'vitest';
import { HOME_DESKTOP, HOME_PHONE, fitScale, mirror, stationAt, stationsFor } from '../../src/scripts/jewel/stations';

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

  it('product desktop has six entries, hero first at {x:0.73,y:0.54,scale:1}', () => {
    const s = desktop('product');
    expect(s).toHaveLength(6);
    expect(s[0]).toEqual({ x: 0.73, y: 0.54, scale: 1 });
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
