import { describe, it, expect } from 'vitest';
import { HOME_DESKTOP, HOME_PHONE, PHONE_MARK_MAX, PHONE_MARK_MIN, fitScale, footerFade, mirror, phoneHeroFade, phoneHeroPose, stationAt, stationsFor, type Station } from '../../src/scripts/jewel/stations';

const C = [450, 1350, 2250, 3150, 4050, 4950];
const VH = 900;

describe('stations', () => {
  it('home has six desktop and six phone stations', () => {
    expect(HOME_DESKTOP).toHaveLength(6); expect(HOME_PHONE).toHaveLength(6);
  });
  it('sits exactly on a station at its centre', () => expect(stationAt(HOME_DESKTOP, C, 2250, false, VH)).toEqual(HOME_DESKTOP[2]));
  it('clamps before the first and after the last centre', () => {
    expect(stationAt(HOME_DESKTOP, C, -999, false, VH)).toEqual(HOME_DESKTOP[0]);
    expect(stationAt(HOME_DESKTOP, C, 99999, false, VH)).toEqual(HOME_DESKTOP[5]);
  });
  it('rtl mirrors x and never y or scale', () => {
    const s = stationAt(HOME_DESKTOP, C, 450, true, VH);
    expect(s.x).toBeCloseTo(1 - HOME_DESKTOP[0].x); expect(s.y).toBe(HOME_DESKTOP[0].y); expect(s.scale).toBe(HOME_DESKTOP[0].scale);
  });
  it('midway is between both stations', () => {
    const s = stationAt(HOME_DESKTOP, C, 1800, false, VH);
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

  it('product desktop reaches the small demo CTA station with five section centres', () => {
    const five = [450, 1270, 2300, 3300, 4100];
    expect(stationAt(desktop('product'), five, 99999, false, VH)).toEqual({ x: 0.73, y: 0.56, scale: 0.62 });
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

  // Was: phone product stations after the hero have scale 0 (the mark shrank away across each section gap, behind the
  // text). Now: every phone station is the same pose, so the mark never moves or shrinks behind a chapter; it is placed
  // under the hero copy and faded out by phoneHeroFade. Can no longer catch: a phone station table that hides the mark
  // by scale. The new guard below catches the mark being hidden or moved by the table at all.
  it('phone home and product stations are one identical pose (the mark lives in the hero only)', () => {
    for (const kind of ['home', 'product'] as const) {
      const s = stationsFor(kind, true);
      if (s === 'none') throw new Error('expected phone stations');
      for (const st of s) expect(st).toEqual(s[0]);
      expect(s[0].scale).toBeGreaterThan(0.01);
    }
  });

  it('desktop chapters after the hero are 55 to 65 percent size; the hero stays full size', () => {
    expect(HOME_DESKTOP[0].scale).toBe(1);
    for (const st of HOME_DESKTOP.slice(1)) {
      expect(st.scale).toBeGreaterThanOrEqual(0.55);
      expect(st.scale).toBeLessThanOrEqual(0.65);
    }
  });

  it('desktop chapters sit on the side opposite the copy: retail and gym (copy flipped right) left, the rest right', () => {
    expect(HOME_DESKTOP.map((st) => (st.x < 0.5 ? 'L' : 'R')).join('')).toBe('RRLRLR');
  });

  it('home returns the existing HOME tables by reference', () => {
    expect(stationsFor('home', false)).toBe(HOME_DESKTOP);
    expect(stationsFor('home', true)).toBe(HOME_PHONE);
  });
});

describe('crisp glide (E1)', () => {
  // Replaces the old 'midway is between both stations' guard's assumption that the glide fills the whole gap: the glide
  // is confined to a 22%-of-viewport band at the middle of the gap, so the mark holds still the rest of the time.
  const a: Station = { x: 0.73, y: 0.5, scale: 1 }, b: Station = { x: 0.27, y: 0.5, scale: 0.6 };
  const G = [0, 1000];
  it('holds the first station until the band starts and the second after it ends', () => {
    expect(stationAt([a, b], G, 300, false, 900)).toEqual(a);
    expect(stationAt([a, b], G, 700, false, 900)).toEqual(b);
  });
  it('is half way at the middle of the gap', () => {
    expect(stationAt([a, b], G, 500, false, 900).x).toBeCloseTo(0.5, 6);
  });
  it('the whole move happens inside 22% of the viewport height of scroll', () => {
    let moving = 0;
    for (let c = 0; c <= 1000; c += 1) {
      const x = stationAt([a, b], G, c, false, 900).x;
      if (x > 0.2701 && x < 0.7299) moving++;
    }
    expect(moving).toBeLessThanOrEqual(Math.ceil(0.22 * 900));
  });
});

describe('phone hero pose', () => {
  it('puts the apex 16px under the hero copy and keeps the whole mark above the bottom edge (390x844)', () => {
    const p = phoneHeroPose(520, 390, 844);
    expect(p.y * 844).toBeCloseTo(536, 3);
    // from the apex down the still frame is 1.27 * width * scale tall
    expect(p.y * 844 + 1.27 * 390 * p.scale).toBeLessThanOrEqual(844);
    expect(p.scale).toBeGreaterThan(0.3);
  });
  it('shrinks the mark on a short screen instead of covering the copy, never below the minimum', () => {
    expect(phoneHeroPose(520, 390, 700).scale).toBeLessThan(phoneHeroPose(520, 390, 844).scale);
    expect(phoneHeroPose(900, 390, 700).scale).toBe(PHONE_MARK_MIN);
  });
  it('never exceeds the maximum on a tall screen', () => {
    expect(phoneHeroPose(300, 390, 1200).scale).toBe(PHONE_MARK_MAX);
  });
});

describe('phone hero fade', () => {
  it('is 0 while the hero fills the viewport, 1 after 30% of the viewport has scrolled off its bottom', () => {
    expect(phoneHeroFade(844, 844)).toBe(0);
    expect(phoneHeroFade(844 * 0.7, 844)).toBeCloseTo(1, 6);
    expect(phoneHeroFade(0, 844)).toBe(1);
    expect(phoneHeroFade(844 * 0.85, 844)).toBeCloseTo(0.5, 6);
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
  const A: Station = { x: 0.75, y: 0.5, scale: 0.6 };
  const B: Station = { x: 0.75, y: 0.5, scale: 0.6, alpha: 0 };
  const G = [0, 1000];
  // Was: full alpha to 40% of the gap, half at 55%, gone from 70% (a dissolve spread over 30% of the gap, about 27vh).
  // Now it runs in the same 22%-of-viewport band as the colour change, centred half-way between the centres (500 here):
  // band 198px at vh 900, so 401 to 599. Can no longer catch: the dissolve drifting back to a long slow fade.
  it('holds full alpha until the band, is half at the middle, gone after the band', () => {
    expect(stationAt([A, B], G, 350, false, 900).alpha).toBe(1);
    expect(stationAt([A, B], G, 500, false, 900).alpha).toBeCloseTo(0.5, 6);
    expect(stationAt([A, B], G, 650, false, 900).alpha).toBe(0);
    expect(stationAt([A, B], G, 1000, false, 900).alpha).toBe(0);
  });
  it('condenses back the same way', () => {
    expect(stationAt([B, A], G, 350, false, 900).alpha).toBe(0);
    expect(stationAt([B, A], G, 500, false, 900).alpha).toBeCloseTo(0.5, 6);
    expect(stationAt([B, A], G, 650, false, 900).alpha).toBe(1);
  });
  it('never emits alpha for stations that do not define it', () => {
    expect('alpha' in stationAt(HOME_DESKTOP, C, 1800, false, VH)).toBe(false);
  });
  it('mirror keeps alpha and flips only x', () => {
    expect(mirror(B)).toEqual({ x: 0.25, y: 0.5, scale: 0.6, alpha: 0 });
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
