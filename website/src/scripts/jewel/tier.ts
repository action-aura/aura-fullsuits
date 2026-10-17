export type Tier = 'rich' | 'lite' | 'still';
export function pickTier(env: {
  reducedMotion: boolean; webgl2: boolean; deviceMemory?: number; saveData?: boolean; width: number; dpr: number;
}): Tier {
  if (env.reducedMotion) return 'still';
  if (!env.webgl2) return 'lite';
  if (env.deviceMemory !== undefined && env.deviceMemory <= 4) return 'lite';
  if (env.saveData) return 'lite';
  if (env.width < 900 && env.dpr > 2.5) return 'lite';
  return 'rich';
}
