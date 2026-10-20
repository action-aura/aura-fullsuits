import type { Lang } from '../i18n';
import { ORIGIN, canonical } from './seo';
import { INSTAGRAM } from './lang';

export function organizationLd(lang: Lang): object {
  return {
    '@context': 'https://schema.org', '@type': 'Organization',
    '@id': `${ORIGIN}/#org`,
    name: 'Action Aura', alternateName: 'أكشن أورا',
    url: `${ORIGIN}/`, logo: `${ORIGIN}/icon-512.png`,
    telephone: '+962780069485', email: 'info@actionaura.me',
    address: { '@type': 'PostalAddress', addressLocality: 'Amman', addressCountry: 'JO',
      streetAddress: 'Al-Rayyan Complex, Radio & Television St, Al-Muqabalain' },
    sameAs: [INSTAGRAM],
    contactPoint: { '@type': 'ContactPoint', telephone: '+962780069485', email: 'info@actionaura.me',
      contactType: 'sales', areaServed: 'JO', availableLanguage: ['ar', 'en'] },
  };
}

// About and Contact (SEO plan section 4): the local business entity, linked to the Organization by @id.
export function professionalServiceLd(lang: Lang): object {
  return {
    '@context': 'https://schema.org', '@type': 'ProfessionalService',
    '@id': `${ORIGIN}/#business`,
    name: lang === 'ar' ? 'أكشن أورا' : 'Action Aura',
    url: `${ORIGIN}/${lang}/`, image: `${ORIGIN}/icon-512.png`,
    telephone: '+962780069485', email: 'info@actionaura.me',
    address: { '@type': 'PostalAddress', addressLocality: 'Amman', addressCountry: 'JO',
      streetAddress: 'Al-Rayyan Complex, Radio & Television St, Al-Muqabalain' },
    parentOrganization: { '@id': `${ORIGIN}/#org` },
  };
}

export function breadcrumbLd(lang: Lang, items: { name: string; slug: string }[]): object {
  return {
    '@context': 'https://schema.org', '@type': 'BreadcrumbList',
    itemListElement: items.map((it, i) => ({ '@type': 'ListItem', position: i + 1, name: it.name, item: canonical(lang, it.slug) })),
  };
}

export function softwareLd(lang: Lang, slug: string, name: string, description: string): object {
  return {
    '@context': 'https://schema.org', '@type': 'SoftwareApplication',
    name, description,
    applicationCategory: 'BusinessApplication', operatingSystem: 'Windows, Android',
    inLanguage: ['ar', 'en'], url: canonical(lang, slug),
    publisher: { '@type': 'Organization', name: 'Action Aura' },
  };
}

export function faqLd(items: ReadonlyArray<{ q: string; a: string }>): object {
  return {
    '@context': 'https://schema.org', '@type': 'FAQPage',
    mainEntity: items.map(i => ({ '@type': 'Question', name: i.q, acceptedAnswer: { '@type': 'Answer', text: i.a } })),
  };
}
