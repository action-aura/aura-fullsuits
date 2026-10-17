import type { Lang } from '../i18n';

export const ORIGIN = 'https://actionaura.me';
const SLUG = /^[a-z0-9-]*$/;

export function pagePath(lang: Lang, slug: string): string {
  if (!SLUG.test(slug)) throw new Error(`bad slug: ${slug}`);
  return slug ? `/${lang}/${slug}/` : `/${lang}/`;
}

export function canonical(lang: Lang, slug: string): string {
  return ORIGIN + pagePath(lang, slug);
}

export function alternates(slug: string): { ar: string; en: string; xDefault: string } {
  return { ar: canonical('ar', slug), en: canonical('en', slug), xDefault: canonical('en', slug) };
}
