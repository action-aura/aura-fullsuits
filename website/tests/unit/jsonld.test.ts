import { describe, it, expect } from 'vitest';
import { organizationLd, professionalServiceLd, breadcrumbLd, softwareLd, faqLd } from '../../src/lib/jsonld';

describe('json-ld', () => {
  it('organization has the real contact data and no ratings or offers', () => {
    const o = organizationLd('ar') as Record<string, unknown>;
    expect(o['@type']).toBe('Organization');
    expect(o.name).toBe('Action Aura');
    expect(o.alternateName).toBe('أكشن أورا');
    expect(o.telephone).toBe('+962780069485');
    expect(JSON.stringify(o)).not.toMatch(/aggregateRating|offers|review/);
  });
  // Old logo path /icons/mark-512.png 404'd, and a per-language url gave two homepages for one entity.
  it('organization has one stable @id and url for both languages, a real logo and a contactPoint', () => {
    const ar = organizationLd('ar') as any;
    const en = organizationLd('en') as any;
    expect(ar.url).toBe('https://actionaura.me/');
    expect(en.url).toBe(ar.url);
    expect(ar['@id']).toBe('https://actionaura.me/#org');
    expect(en['@id']).toBe(ar['@id']);
    expect(ar.logo.endsWith('/icon-512.png')).toBe(true);
    expect(ar.contactPoint.telephone).toBe('+962780069485');
  });
  it('professional service links to the organization by @id and shares contact data', () => {
    const org = organizationLd('ar') as any;
    const p = professionalServiceLd('ar') as any;
    expect(p['@type']).toBe('ProfessionalService');
    expect(p.parentOrganization['@id']).toBe(org['@id']);
    expect(p.telephone).toBe(org.telephone);
    expect(p.address.streetAddress).toBe(org.address.streetAddress);
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

describe('organization sameAs', () => {
  it('lists the real Instagram profile and nothing invented', async () => {
    const { organizationLd } = await import('../../src/lib/jsonld');
    expect((organizationLd('en') as { sameAs: string[] }).sameAs).toEqual(['https://www.instagram.com/actionaura.me/']);
  });
});
