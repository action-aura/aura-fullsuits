import { describe, it, expect } from 'vitest';
import { pickTier } from '../../src/scripts/jewel/tier';

const base = { reducedMotion: false, webgl2: () => true, deviceMemory: 8, saveData: false, width: 1440, dpr: 1 };

describe('pickTier', () => {
  it('rich on a capable desktop', () => expect(pickTier(base)).toBe('rich'));
  it('still under reduced motion even when capable', () => expect(pickTier({ ...base, reducedMotion: true })).toBe('still'));
  it.each([
    ['no webgl2', { webgl2: () => false }],
    ['4GB memory', { deviceMemory: 4 }],
    ['save-data', { saveData: true }],
    ['dense small screen', { width: 390, dpr: 3 }],
  ])('lite when %s', (_n, patch) => expect(pickTier({ ...base, ...patch })).toBe('lite'));
  it('unknown deviceMemory does not force lite', () => expect(pickTier({ ...base, deviceMemory: undefined })).toBe('rich'));
  // Was 'a 390px phone at dpr 2 can be rich'. Spec section 14 makes lite the phone default until a
  // real-device check; this test now pins that. It can no longer catch phones wrongly losing rich.
  it('every phone-width viewport is lite until the real-phone check', () => {
    expect(pickTier({ ...base, width: 390, dpr: 2 })).toBe('lite');
    expect(pickTier({ ...base, width: 899, dpr: 1 })).toBe('lite');
    expect(pickTier({ ...base, width: 900, dpr: 1 })).toBe('rich');
  });
  it('never probes WebGL2 when a cheap check already decides', () => {
    for (const patch of [{ reducedMotion: true }, { deviceMemory: 4 }, { saveData: true }, { width: 390, dpr: 2 }]) {
      let probed = 0;
      pickTier({ ...base, ...patch, webgl2: () => { probed++; return true; } });
      expect(probed, JSON.stringify(patch)).toBe(0);
    }
    let probed = 0;
    expect(pickTier({ ...base, webgl2: () => { probed++; return true; } })).toBe('rich');
    expect(probed).toBe(1);
  });
});
