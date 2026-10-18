import { describe, it, expect } from 'vitest';
import { organizationLd, breadcrumbLd, softwareLd, faqLd } from '../../src/lib/jsonld';

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
  it('software has SoftwareApplication type, category, OS and canonical url', () => {
    const s = softwareLd('ar', 'aura-clinic', 'Aura Clinic', 'd') as Record<string, unknown>;
    expect(s['@type']).toBe('SoftwareApplication');
    expect(s.applicationCategory).toBe('BusinessApplication');
    expect(s.operatingSystem).toBe('Windows, Android');
    expect(s.url).toBe('https://actionaura.me/ar/aura-clinic/');
    expect(JSON.stringify(s)).not.toMatch(/offers|aggregateRating|review/);
  });
  it('faq has FAQPage type and one question per item in order', () => {
    const f = faqLd([{ q: 'Q1', a: 'A1' }, { q: 'Q2', a: 'A2' }]) as any;
    expect(f['@type']).toBe('FAQPage');
    expect(f.mainEntity).toHaveLength(2);
    expect(f.mainEntity[1].name).toBe('Q2');
    expect(f.mainEntity[1].acceptedAnswer.text).toBe('A2');
  });
});
