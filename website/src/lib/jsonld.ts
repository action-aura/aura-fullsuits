import type { Lang } from '../i18n';
import { ORIGIN, canonical } from './seo';

export function organizationLd(lang: Lang): object {
  return {
    '@context': 'https://schema.org', '@type': 'Organization',
    name: 'Action Aura', alternateName: 'أكشن أورا',
    url: `${ORIGIN}/${lang}/`, logo: `${ORIGIN}/icons/mark-512.png`,
    telephone: '+962780069485', email: 'info@actionaura.me',
    address: { '@type': 'PostalAddress', addressLocality: 'Amman', addressCountry: 'JO',
      streetAddress: 'Al-Rayyan Complex, Radio & Television St, Al-Muqabalain' },
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
