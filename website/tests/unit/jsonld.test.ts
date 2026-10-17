import { describe, it, expect } from 'vitest';
import { organizationLd, breadcrumbLd } from '../../src/lib/jsonld';

describe('json-ld', () => {
  it('organization has the real contact data and no ratings or offers', () => {
    const o = organizationLd('ar') as Record<string, unknown>;
    expect(o['@type']).toBe('Organization');
    expect(o.name).toBe('Action Aura');
    expect(o.alternateName).toBe('أكشن أورا');
    expect(o.telephone).toBe('+962780069485');
    expect(JSON.stringify(o)).not.toMatch(/aggregateRating|offers|review/);
  });
  it('breadcrumb positions are 1-based and urls absolute', () => {
    const b = breadcrumbLd('en', [{ name: 'Home', slug: '' }, { name: 'Aura Retail', slug: 'aura-retail' }]) as any;
    expect(b.itemListElement.map((i: any) => i.position)).toEqual([1, 2]);
    expect(b.itemListElement[1].item).toBe('https://actionaura.me/en/aura-retail/');
  });
});
