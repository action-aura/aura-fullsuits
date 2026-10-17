import { describe, it, expect } from 'vitest';
import { pagePath, alternates, canonical, ORIGIN } from '../../src/lib/seo';

describe('seo helpers', () => {
  it('builds trailing-slash paths', () => {
    expect(pagePath('en', '')).toBe('/en/');
    expect(pagePath('ar', 'aura-retail')).toBe('/ar/aura-retail/');
  });
  it('alternates are absolute, reciprocal, and x-default is english', () => {
    expect(alternates('aura-clinic')).toEqual({
      ar: `${ORIGIN}/ar/aura-clinic/`, en: `${ORIGIN}/en/aura-clinic/`, xDefault: `${ORIGIN}/en/aura-clinic/`,
    });
  });
  it('canonical is self', () => expect(canonical('ar', '')).toBe(`${ORIGIN}/ar/`));
  it('rejects slugs with slashes or uppercase', () => {
    expect(() => pagePath('en', '/x')).toThrow();
    expect(() => pagePath('en', 'Aura')).toThrow();
  });
});
