export type Tier = 'rich' | 'lite' | 'still';
export function pickTier(env: {
  reducedMotion: boolean; webgl2: boolean; deviceMemory?: number; saveData?: boolean; width: number; dpr: number;
}): Tier {
  if (env.reducedMotion) return 'still';
  if (!env.webgl2) return 'lite';
  if (env.deviceMemory !== undefined && env.deviceMemory <= 4) return 'lite';
  if (env.saveData) return 'lite';
  // Spec section 14: phones get the lite still until a real mid-range Android proves the live
  // scene smooth. Measured 2026-10-18 (Lighthouse mobile, simulated): the rich tier on a phone
  // viewport cost 18-25 s of total blocking time. Revisit after the real-phone check (deploy.md).
  if (env.width < 900) return 'lite';
  return 'rich';
}
